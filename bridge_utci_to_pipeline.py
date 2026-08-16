"""
Bridge: convert raw Grasshopper UTCI CSVs and MERGE into pipeline format.

Reads files like:
    02_Process/UTCI_Analysis/UTCI_Analysis <N>.csv
        x, y, z, 6AM, 7AM, 8AM, 9AM, 10AM, 11AM, 12PM, 1PM, 2PM, 3PM, 4PM, 5PM, 6PM
    (13 hourly UTCI values per point per month, in degrees Celsius)

For each month, this bridge:
  1. Loads hourly UTCI values from the GH CSV
  2. Auto-detects the hour columns (6AM..6PM or 06:00..18:00 - any time-like header)
  3. Computes per-point daily statistics across the analysis window:
        utci      -> mean across 6 AM .. 6 PM
        utci_max  -> peak hour (worst-case heat stress)
        utci_min  -> coolest hour
        utci_noon -> value at 12 PM (or closest midday hour)
  4. Maps row index -> point_id via points_for_grasshopper.csv
  5. MERGES into 00_Source/Thermal/Grasshopper_Output/grasshopper_results_month_NN.csv
     adding/updating utci, utci_max, utci_min, utci_noon without disturbing
     shadow_hours / radiation_kwh / svf

Usage:
    python bridge_utci_to_pipeline.py
    python bridge_utci_to_pipeline.py --month 7
    python bridge_utci_to_pipeline.py --month 1 --month 2 --month 3
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


# ----------------------------------------------------------------------------
# I/O helpers
# ----------------------------------------------------------------------------

def _read_robust_csv(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    cleaned = raw.rstrip(b"\x00 \r\n\t")
    if cleaned.startswith(b"\xef\xbb\xbf"):
        cleaned = cleaned[3:]
    elif cleaned.startswith(b"\xff\xfe") or cleaned.startswith(b"\xfe\xff"):
        cleaned = cleaned.decode("utf-16").encode("utf-8")
    return pd.read_csv(io.BytesIO(cleaned))


def _list_input_files(input_dir: Path, template: str) -> dict:
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


# ----------------------------------------------------------------------------
# Hour-column parsing
# ----------------------------------------------------------------------------

# Matches '6AM', '12PM', '06:00', '6', '18:00', '6 AM', etc.
_HOUR_RX_AMPM = re.compile(r"^\s*(\d{1,2})\s*(AM|PM)\s*$", re.IGNORECASE)
_HOUR_RX_HMM  = re.compile(r"^\s*(\d{1,2})\s*[:hH]\s*\d{1,2}\s*$")
_HOUR_RX_NUM  = re.compile(r"^\s*(\d{1,2})\s*$")


def _column_to_hour(col: str) -> int | None:
    """Return hour in 0..23 for a column header, or None if not a time."""
    s = str(col)
    m = _HOUR_RX_AMPM.match(s)
    if m:
        h = int(m.group(1)) % 12
        if m.group(2).upper() == "PM":
            h += 12
        return h
    m = _HOUR_RX_HMM.match(s)
    if m:
        return int(m.group(1))
    m = _HOUR_RX_NUM.match(s)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 23:
            return n
    return None


def _detect_hour_columns(df: pd.DataFrame, start_hour: int, end_hour: int) -> list[tuple[str, int]]:
    """Return list of (column_name, hour) for headers that look like hours
    AND fall inside [start_hour, end_hour] inclusive."""
    found: list[tuple[str, int]] = []
    for col in df.columns:
        if str(col).lower() in ("x", "y", "z", "point_id", "month"):
            continue
        h = _column_to_hour(col)
        if h is None:
            continue
        if start_hour <= h <= end_hour:
            found.append((col, h))
    # Sort by hour so summaries are deterministic
    found.sort(key=lambda t: t[1])
    return found


# ----------------------------------------------------------------------------
# Per-file merge
# ----------------------------------------------------------------------------

def merge_one(src_csv: Path, pipeline_csv: Path, point_ids: list, month: int,
              start_hour: int, end_hour: int, logger):
    raw = _read_robust_csv(src_csv).dropna(how="all").reset_index(drop=True)

    if len(raw) == len(point_ids) + 1:
        logger.info("  trimming 1 extra row (file has %d, expected %d)", len(raw), len(point_ids))
        raw = raw.iloc[:len(point_ids)].reset_index(drop=True)

    if len(raw) != len(point_ids):
        raise ValueError(
            f"{src_csv.name}: row count {len(raw)} != points count {len(point_ids)}"
        )

    hour_cols = _detect_hour_columns(raw, start_hour, end_hour)
    if not hour_cols:
        raise KeyError(
            f"No hour-like columns detected in {src_csv.name}. "
            f"Columns: {list(raw.columns)}"
        )

    logger.info(
        "  detected %d hour column(s): %s",
        len(hour_cols),
        ", ".join(f"{c}->{h:02d}h" for c, h in hour_cols),
    )

    # Coerce all hour columns to numeric
    hour_frame = raw[[c for c, _ in hour_cols]].apply(pd.to_numeric, errors="coerce")

    utci_mean = hour_frame.mean(axis=1).round(3)
    utci_max  = hour_frame.max(axis=1).round(3)
    utci_min  = hour_frame.min(axis=1).round(3)

    # Pick the column nearest to 12 (noon)
    noon_col, _ = min(hour_cols, key=lambda t: abs(t[1] - 12))
    utci_noon = pd.to_numeric(raw[noon_col], errors="coerce").round(3)
    logger.info("  noon column chosen: %s", noon_col)

    logger.info(
        "  utci_mean: min=%.2f max=%.2f mean=%.2f  |  utci_max peak=%.2f",
        utci_mean.min(), utci_mean.max(), utci_mean.mean(), utci_max.max(),
    )

    # Load existing pipeline CSV (shadow/radiation/svf should have populated it)
    if pipeline_csv.exists():
        pipe = pd.read_csv(pipeline_csv)
        if "point_id" not in pipe.columns or len(pipe) != len(point_ids):
            raise ValueError(
                f"{pipeline_csv.name}: missing point_id col or row mismatch "
                f"(got {len(pipe)}, expected {len(point_ids)})"
            )
    else:
        logger.info("  pipeline CSV missing - creating fresh: %s", pipeline_csv.name)
        pipe = pd.DataFrame({"point_id": point_ids, "month": month})

    pipe["utci"]      = utci_mean.values
    pipe["utci_max"]  = utci_max.values
    pipe["utci_min"]  = utci_min.values
    pipe["utci_noon"] = utci_noon.values

    pipe.to_csv(pipeline_csv, index=False)
    logger.info("  merged into %s (cols: %s)", pipeline_csv.name, list(pipe.columns))
    return pipe


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def convert_all(cfg, only_months=None):
    logger = setup_logging(cfg, "bridge_utci_to_pipeline")
    bridge = cfg["bridge_utci"]

    input_dir = resolve_path(cfg, bridge["input_dir"])
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Bridge input dir not found: {input_dir}")

    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    if not points_csv.exists():
        raise FileNotFoundError(f"points_for_grasshopper.csv missing: {points_csv}")
    points = pd.read_csv(points_csv)
    point_ids = points["point_id"].astype(str).tolist()
    logger.info("Loaded %d point_ids", len(point_ids))

    start_hour = int(cfg["analysis"]["analysis_period"]["start_hour"])
    end_hour   = int(cfg["analysis"]["analysis_period"]["end_hour"])
    logger.info("Analysis window: %02d:00 .. %02d:00", start_hour, end_hour)

    template = bridge["input_filename_template"]
    found = _list_input_files(input_dir, template)
    logger.info("Discovered %d UTCI file(s) matching '%s'", len(found), template)
    if not found:
        raise RuntimeError(f"No files match '{template}' in {input_dir}")

    months_to_run = sorted(found.keys()) if not only_months else sorted(set(only_months))
    out_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    merged = []
    for month in months_to_run:
        if month not in found:
            logger.warning("Month %d not found - skipping", month)
            continue
        src = found[month]
        logger.info("Converting %s (month=%d)", src.name, month)
        if src.stat().st_size == 0:
            logger.error("  -> %s is empty (0 bytes) - skipping", src.name)
            continue
        pipeline_name = cfg["grasshopper"]["output_naming_template"].format(month=month)
        pipeline_csv = out_dir / pipeline_name
        try:
            merge_one(src, pipeline_csv, point_ids, month, start_hour, end_hour, logger)
            merged.append(pipeline_csv)
        except Exception as exc:
            logger.error("  -> failed: %s", exc)
            continue

    logger.info("UTCI bridge complete: %d file(s) merged", len(merged))
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--month", type=int, action="append",
                        help="Run only this month (repeatable)")
    args = parser.parse_args()
    cfg = load_config(args.config)
    convert_all(cfg, only_months=args.month)


if __name__ == "__main__":
    main()
