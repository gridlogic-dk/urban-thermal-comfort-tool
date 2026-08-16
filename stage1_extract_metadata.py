"""
Stage 1 - Extract Master Metadata
==================================
Reads every *.metadata.json file under <metadata_dir>, pulls the essential
fields, validates GPS coordinates against the configured bounding box, and
writes a single tidy CSV.

Usage:
    python stage1_extract_metadata.py [--config config.yaml] [--skip-if-exists]

Output:
    00_Source/Thermal/master_metadata.csv
        columns: point_id, lat, lng, elevation, year, month, pano_id, rotation
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from utils import (
    load_config,
    resolve_path,
    setup_logging,
    point_id_from_path,
    validate_coordinates,
    read_nested,
    save_checkpoint,
)


def extract_metadata(cfg: dict) -> pd.DataFrame:
    logger = setup_logging(cfg, "stage1_metadata_extraction")

    metadata_dir = resolve_path(cfg, cfg["project"]["paths"]["metadata_dir"])
    suffix = cfg["filenames"]["metadata_suffix"]
    pid_style = cfg["filenames"]["point_id_style"]
    bounds = cfg["metadata"]["coordinate_bounds"]
    on_missing = cfg["processing"]["on_missing_metadata"]
    on_invalid = cfg["processing"]["on_invalid_coords"]
    fields = cfg["metadata"]["essential_fields"]

    if not metadata_dir.is_dir():
        raise FileNotFoundError(f"Metadata directory not found: {metadata_dir}")

    files = sorted(metadata_dir.glob(f"*{suffix}"))
    logger.info("Found %d metadata files in %s", len(files), metadata_dir)
    if not files:
        raise RuntimeError(f"No '*{suffix}' files in {metadata_dir}")

    records: list[dict] = []
    skipped_missing = 0
    skipped_invalid = 0

    for json_file in tqdm(files, desc="Reading metadata", unit="file"):
        point_id = point_id_from_path(json_file, style=pid_style)
        try:
            with json_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.warning("Could not parse %s: %s", json_file.name, exc)
            if on_missing == "halt":
                raise
            skipped_missing += 1
            continue

        lat = read_nested(data, fields["lat"])
        lng = read_nested(data, fields["lng"])

        if not validate_coordinates(lat, lng, bounds):
            logger.warning("Invalid coords for %s: lat=%s lng=%s", point_id, lat, lng)
            if on_invalid == "halt":
                raise ValueError(f"Invalid coordinates for {point_id}")
            skipped_invalid += 1
            continue

        records.append({
            "point_id":  point_id,
            "lat":       float(lat),
            "lng":       float(lng),
            "elevation": read_nested(data, fields["elevation"]),
            "year":      read_nested(data, fields["year"]),
            "month":     read_nested(data, fields["month"]),
            "pano_id":   read_nested(data, fields["pano_id"]),
            "rotation":  read_nested(data, fields["rotation"]),
            "source_file": json_file.name,
        })

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.sort_values("point_id").reset_index(drop=True)

    output_file = resolve_path(cfg, cfg["metadata"]["output_file"])
    save_checkpoint(df, output_file, logger)

    logger.info(
        "Stage 1 complete: %d valid, %d missing/unreadable, %d invalid coords",
        len(df), skipped_missing, skipped_invalid,
    )
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1: extract master metadata")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--skip-if-exists", action="store_true",
                        help="Exit early if output CSV already exists")
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_file = resolve_path(cfg, cfg["metadata"]["output_file"])

    if args.skip_if_exists and output_file.exists():
        print(f"[stage1] Output already present, skipping: {output_file}")
        return
    if cfg["processing"].get("skip_metadata_extraction") and output_file.exists():
        print(f"[stage1] skip_metadata_extraction=true and file exists: {output_file}")
        return

    extract_metadata(cfg)


if __name__ == "__main__":
    main()
