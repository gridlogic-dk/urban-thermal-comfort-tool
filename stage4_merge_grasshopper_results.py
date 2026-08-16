"""
Stage 4 - Merge Grasshopper Results
====================================
Reads the 12 monthly Grasshopper output CSVs (grasshopper_results_month_01.csv
... month_12.csv), validates and stitches them into a single long table, then
joins master metadata and segmentation metrics.

Expected GH columns: point_id, month, shadow_hours, radiation_kwh, svf, utci, mrt

Usage:
    python stage4_merge_grasshopper_results.py [--config config.yaml]

Output:
    00_Source/Thermal/Merged_Data/merged_thermal_data.csv
        2,875 points * 12 months = 34,500 rows (when complete)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging, save_checkpoint


_MONTH_RE = re.compile(r"month[_-](\d{1,2})", re.IGNORECASE)


def _infer_month(filename: str) -> int | None:
    m = _MONTH_RE.search(filename)
    return int(m.group(1)) if m else None


def merge_grasshopper_results(cfg: dict) -> pd.DataFrame:
    logger = setup_logging(cfg, "stage4_merge_grasshopper")

    gh_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    if not gh_dir.is_dir():
        raise FileNotFoundError(f"Grasshopper output dir not found: {gh_dir}")

    expected_cols = list(cfg["grasshopper"]["expected_columns"])
    files = sorted(gh_dir.glob("grasshopper_results_month_*.csv"))
    logger.info("Found %d Grasshopper result file(s) in %s", len(files), gh_dir)
    if not files:
        raise RuntimeError(
            f"No 'grasshopper_results_month_*.csv' files in {gh_dir}. "
            "Run Stage 4 (manual Grasshopper simulation) first."
        )
    if len(files) != 12:
        logger.warning("Expected 12 monthly files, found %d", len(files))

    frames = []
    for f in files:
        df = pd.read_csv(f)
        missing = [c for c in expected_cols if c not in df.columns and c != "mrt"]
        if missing:
            logger.warning("%s missing required columns: %s", f.name, missing)
        if "month" not in df.columns:
            month = _infer_month(f.name)
            if month is None:
                logger.warning("Cannot infer month from %s - skipping", f.name)
                continue
            df["month"] = month
        # Coerce month to int
        df["month"] = pd.to_numeric(df["month"], errors="coerce").astype("Int64")
        frames.append(df)
        logger.info("Loaded %s: %d rows", f.name, len(df))

    merged = pd.concat(frames, ignore_index=True, sort=False)

    # Join master metadata
    metadata_file = resolve_path(cfg, cfg["metadata"]["output_file"])
    if metadata_file.exists():
        meta = pd.read_csv(metadata_file)
        merged = merged.merge(
            meta, on="point_id", how="left", suffixes=("", "_meta")
        )
        logger.info("Joined metadata (%d rows)", len(meta))
    else:
        logger.warning("Master metadata CSV missing: %s", metadata_file)

    # Join segmentation if present
    seg_file = resolve_path(cfg, cfg["segmentation"]["output_file"])
    if seg_file.exists():
        seg = pd.read_csv(seg_file)
        merged = merged.merge(seg, on="point_id", how="left")
        logger.info("Joined segmentation (%d rows)", len(seg))
    else:
        logger.info("Segmentation metrics not found (optional): %s", seg_file)

    merged = merged.sort_values(["point_id", "month"]).reset_index(drop=True)

    output_file = resolve_path(cfg, cfg["merge"]["output_file"])
    save_checkpoint(merged, output_file, logger)

    unique_points = merged["point_id"].nunique()
    months = sorted(merged["month"].dropna().unique().tolist())
    logger.info(
        "Stage 4 complete: %d rows | %d unique points | months present: %s",
        len(merged), unique_points, months,
    )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 4: merge Grasshopper results")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    merge_grasshopper_results(cfg)


if __name__ == "__main__":
    main()
