"""
Bridge: convert raw Grasshopper Radiation CSVs and MERGE into pipeline format.

Reads files like:
    02_Process/Radiation_Analysis/Radiation_result <N>.csv
        x, y, z, radiation_kwh     (or 'shadow_fraction' if writer wasn't updated)

For each month, this bridge:
  1. Loads radiation values from the GH CSV
  2. Auto-detects the value column (prefers radiation_kwh; falls back to any
     numeric column besides x/y/z)
  3. Maps row index -> point_id via points_for_grasshopper.csv
  4. MERGES into 00_Source/Thermal/Grasshopper_Output/grasshopper_results_month_NN.csv
     adding/updating a radiation_kwh column without disturbing shadow_hours

Usage:
    python bridge_radiation_to_pipeline.py
    python bridge_radiation_to_pipeline.py --month 12
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


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


def _pick_value_column(df: pd.DataFrame, preferred: list) -> str:
    """Return the value column to read. Preferred names first, then any
    non-x/y/z numeric column."""
    cols_lower = {c.lower(): c for c in df.columns}
    for name in preferred:
        if name.lower() in cols_lower:
            return cols_lower[name.lower()]
    for col in df.columns:
        if col.lower() in ("x", "y", "z"):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            return col
    raise KeyError(f"No usable value column in {list(df.columns)}")


def merge_one(src_csv: Path, pipeline_csv: Path, point_ids: list, month: int, logger):
    raw = _read_robust_csv(src_csv).dropna(how="all").reset_index(drop=True)

    if len(raw) == len(point_ids) + 1:
        logger.info("  trimming 1 extra row (file has %d, expected %d)", len(raw), len(point_ids))
        raw = raw.iloc[:len(point_ids)].reset_index(drop=True)

    if len(raw) != len(point_ids):
        raise ValueError(
            f"{src_csv.name}: row count {len(raw)} != points count {len(point_ids)}"
        )

    value_col = _pick_value_column(raw, preferred=["radiation_kwh", "radiation", "kwh"])
    logger.info("  value column detected: %s", value_col)

    rad = pd.to_numeric(raw[value_col], errors="coerce")
    rad = rad.clip(lower=0)
    logger.info("  radiation_kwh: min=%.2f max=%.2f mean=%.2f", rad.min(), rad.max(), rad.mean())

    # Load existing pipeline CSV (must exist - shadow run created it)
    if pipeline_csv.exists():
        pipe = pd.read_csv(pipeline_csv)
        if "point_id" not in pipe.columns or len(pipe) != len(point_ids):
            raise ValueError(
                f"{pipeline_csv.name}: missing point_id col or row mismatch "
                f"(got {len(pipe)}, expected {len(point_ids)})"
            )
    else:
        # Pipeline file doesn't exist yet - create fresh with point_id + month
        logger.info("  pipeline CSV missing - creating fresh: %s", pipeline_csv.name)
        pipe = pd.DataFrame({"point_id": point_ids, "month": month})

    pipe["radiation_kwh"] = rad.round(4).values
    pipe.to_csv(pipeline_csv, index=False)
    logger.info("  merged into %s (cols: %s)", pipeline_csv.name, list(pipe.columns))
    return pipe


def convert_all(cfg, only_months=None):
    logger = setup_logging(cfg, "bridge_radiation_to_pipeline")
    bridge = cfg["bridge_radiation"]

    input_dir = resolve_path(cfg, bridge["input_dir"])
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Bridge input dir not found: {input_dir}")

    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    if not points_csv.exists():
        raise FileNotFoundError(f"points_for_grasshopper.csv missing: {points_csv}")
    points = pd.read_csv(points_csv)
    point_ids = points["point_id"].astype(str).tolist()
    logger.info("Loaded %d point_ids", len(point_ids))

    template = bridge["input_filename_template"]
    found = _list_input_files(input_dir, template)
    logger.info("Discovered %d radiation file(s) matching '%s'", len(found), template)
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
            merge_one(src, pipeline_csv, point_ids, month, logger)
            merged.append(pipeline_csv)
        except Exception as exc:
            logger.error("  -> failed: %s", exc)
            continue

    logger.info("Radiation bridge complete: %d file(s) merged", len(merged))
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--month", type=int, action="append")
    args = parser.parse_args()
    cfg = load_config(args.config)
    convert_all(cfg, only_months=args.month)


if __name__ == "__main__":
    main()
