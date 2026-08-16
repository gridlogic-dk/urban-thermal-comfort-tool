"""
Bridge: convert raw Grasshopper Shadow CSVs -> pipeline format.

Reads 02_Process/Shadow_Analysis/shadow_results <N>.csv (columns:
x, y, z, shadow_fraction), maps rows by index to point_ids from
points_for_grasshopper.csv, auto-detects whether shadow_fraction is in
hours (0..12) or sunny ticks (0..72), then writes:

    00_Source/Thermal/Grasshopper_Output/grasshopper_results_month_NN.csv
        columns: point_id, month, shadow_hours

Handles two GH writer quirks:
    - Trailing NUL bytes (pre-allocated file space not truncated)
    - Trailing blank line (off-by-one row count)

Usage:
    python bridge_shadow_to_pipeline.py                # all months found
    python bridge_shadow_to_pipeline.py --month 12     # one month only
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


def _read_robust_csv(path: Path) -> pd.DataFrame:
    """Strip trailing NUL/whitespace bytes before parsing - GH leaves them behind."""
    raw = path.read_bytes()
    cleaned = raw.rstrip(b"\x00 \r\n\t")
    # Also strip BOM if present
    if cleaned.startswith(b"\xef\xbb\xbf"):
        cleaned = cleaned[3:]
    elif cleaned.startswith(b"\xff\xfe") or cleaned.startswith(b"\xfe\xff"):
        # UTF-16 BOM
        cleaned = cleaned.decode("utf-16").encode("utf-8")
    return pd.read_csv(io.BytesIO(cleaned))


def _list_input_files(input_dir, template):
    pattern = re.escape(template).replace(re.escape("{month}"), r"(\d{1,2})")
    rx = re.compile("^" + pattern + "$", re.IGNORECASE)
    found = {}
    for f in sorted(input_dir.iterdir()):
        if not f.is_file():
            continue
        m = rx.match(f.name)
        if m:
            found[int(m.group(1))] = f
    return found


def _detect_unit(max_val, analysis_hours, timestep):
    hours_cap = analysis_hours * 1.1
    ticks_cap = analysis_hours * timestep * 1.1
    if max_val <= hours_cap:
        return "hours"
    if max_val <= ticks_cap:
        return "sunny_ticks"
    return "unknown"


def _convert_one(input_csv, point_ids, *, value_column, analysis_hours,
                 timestep, value_unit, raw_is_sunny, month, logger):
    df = _read_robust_csv(input_csv)

    if value_column not in df.columns:
        raise KeyError(
            f"{input_csv.name}: missing column '{value_column}'. "
            f"Found: {list(df.columns)}"
        )

    # Drop any rows where every cell is NaN (trailing blank line)
    before = len(df)
    df = df.dropna(how="all").reset_index(drop=True)
    if len(df) != before:
        logger.info("  dropped %d blank row(s)", before - len(df))

    if len(df) == len(point_ids) + 1:
        logger.info("  trimming 1 extra row (file has %d, expected %d)", len(df), len(point_ids))
        df = df.iloc[:len(point_ids)].reset_index(drop=True)

    if len(df) != len(point_ids):
        raise ValueError(
            f"{input_csv.name}: row count {len(df)} != "
            f"points count {len(point_ids)}"
        )

    raw = df[value_column].astype(float)
    unit = value_unit
    if unit == "auto":
        unit = _detect_unit(float(raw.max()), analysis_hours, timestep)
        logger.info("  unit auto-detected: %s (raw max=%.2f)", unit, raw.max())

    if unit == "unknown":
        raise ValueError(
            f"{input_csv.name}: raw max {raw.max():.1f} > both hours cap "
            f"({analysis_hours}) and ticks cap ({analysis_hours*timestep}). "
            "Re-run the GH model constrained to a single design day."
        )

    if unit == "hours":
        sun_hours = raw
    elif unit == "sunny_ticks":
        sun_hours = raw / timestep
    else:
        raise ValueError(f"value_unit must be auto|hours|sunny_ticks, got {unit!r}")

    if raw_is_sunny:
        shadow_hours = analysis_hours - sun_hours
    else:
        shadow_hours = sun_hours
    shadow_hours = shadow_hours.clip(lower=0, upper=analysis_hours)

    return pd.DataFrame({
        "point_id":     point_ids,
        "month":        month,
        "shadow_hours": shadow_hours.round(4),
    })


def convert_all(cfg, only_months=None):
    logger = setup_logging(cfg, "bridge_shadow_to_pipeline")
    bridge = cfg["bridge_shadow"]

    input_dir = resolve_path(cfg, bridge["input_dir"])
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Bridge input dir not found: {input_dir}")

    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    if not points_csv.exists():
        raise FileNotFoundError(
            f"points_for_grasshopper.csv missing: {points_csv}. Run Stage 3 first."
        )
    points = pd.read_csv(points_csv)
    point_ids = points["point_id"].astype(str).tolist()
    logger.info("Loaded %d point_ids from %s", len(point_ids), points_csv.name)

    template = bridge["input_filename_template"]
    found = _list_input_files(input_dir, template)
    logger.info("Discovered %d shadow result file(s) matching '%s'",
                len(found), template)
    if not found:
        raise RuntimeError(f"No files match '{template}' in {input_dir}")

    months_to_run = sorted(found.keys()) if not only_months else sorted(set(only_months))
    out_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for month in months_to_run:
        if month not in found:
            logger.warning("Month %d not found - skipping", month)
            continue
        src = found[month]
        logger.info("Converting %s (month=%d)", src.name, month)
        if src.stat().st_size == 0:
            logger.error("  -> %s is empty (0 bytes) - skipping", src.name)
            continue
        try:
            df = _convert_one(
                src, point_ids,
                value_column=bridge["input_value_column"],
                analysis_hours=float(bridge["analysis_hours"]),
                timestep=int(bridge["timestep"]),
                value_unit=str(bridge.get("value_unit", "auto")),
                raw_is_sunny=bool(bridge.get("raw_is_sunny_ticks", True)),
                month=month, logger=logger,
            )
        except Exception as exc:
            logger.error("  -> failed: %s", exc)
            continue

        out_name = cfg["grasshopper"]["output_naming_template"].format(month=month)
        out_path = out_dir / out_name
        df.to_csv(out_path, index=False)
        logger.info(
            "  -> %s rows=%d shadow_hours min=%.2f max=%.2f mean=%.2f",
            out_path.name, len(df),
            df["shadow_hours"].min(), df["shadow_hours"].max(), df["shadow_hours"].mean(),
        )
        written.append(out_path)

    logger.info("Bridge complete: %d file(s) written to %s", len(written), out_dir)
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--month", type=int, action="append")
    args = parser.parse_args()
    cfg = load_config(args.config)
    convert_all(cfg, only_months=args.month)


if __name__ == "__main__":
    main()
