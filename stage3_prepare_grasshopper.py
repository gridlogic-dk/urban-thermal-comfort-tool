"""
Stage 3 - Prepare Grasshopper Input
====================================
Reads master_metadata.csv and emits:
    * points_for_grasshopper.geojson   (FeatureCollection of Points)
    * points_for_grasshopper.csv       (flat CSV variant for non-GIS tools)

Each point carries: point_id, lat, lng, elevation, analysis_height, pano_id.

Usage:
    python stage3_prepare_grasshopper.py [--config config.yaml] [--skip-if-exists]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


def prepare_grasshopper_input(cfg: dict) -> dict:
    logger = setup_logging(cfg, "stage3_prepare_grasshopper")

    metadata_file = resolve_path(cfg, cfg["metadata"]["output_file"])
    if not metadata_file.exists():
        raise FileNotFoundError(
            f"Master metadata CSV not found: {metadata_file}. Run stage1 first."
        )
    df = pd.read_csv(metadata_file)
    logger.info("Loaded %d points from %s", len(df), metadata_file)

    analysis_height = float(cfg["analysis"]["analysis_height"])

    features = []
    for row in df.itertuples(index=False):
        if pd.isna(row.lat) or pd.isna(row.lng):
            logger.warning("Skipping %s - missing coords", row.point_id)
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row.lng), float(row.lat)],
            },
            "properties": {
                "point_id": str(row.point_id),
                "lat": float(row.lat),
                "lng": float(row.lng),
                "elevation": float(row.elevation) if pd.notna(row.elevation) else None,
                "analysis_height": analysis_height,
                "pano_id": str(row.pano_id) if pd.notna(row.pano_id) else "",
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}

    # Write GeoJSON
    out_geojson = resolve_path(cfg, cfg["grasshopper"]["input_geojson"])
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    with out_geojson.open("w", encoding="utf-8") as fh:
        json.dump(geojson, fh, indent=2)
    logger.info("GeoJSON written: %s (%d features)", out_geojson, len(features))

    # Write CSV variant
    out_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    export = df[["point_id", "lat", "lng", "elevation", "pano_id"]].copy()
    export["analysis_height"] = analysis_height
    export.to_csv(out_csv, index=False)
    logger.info("CSV written: %s (%d rows)", out_csv, len(export))

    logger.info("Stage 3 complete - ready for Grasshopper import")
    return geojson


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3: prepare Grasshopper input")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--skip-if-exists", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    out_geojson = resolve_path(cfg, cfg["grasshopper"]["input_geojson"])

    if args.skip_if_exists and out_geojson.exists():
        print(f"[stage3] Output already present, skipping: {out_geojson}")
        return
    if cfg["processing"].get("skip_prepare_grasshopper") and out_geojson.exists():
        print(f"[stage3] skip_prepare_grasshopper=true and file exists: {out_geojson}")
        return

    prepare_grasshopper_input(cfg)


if __name__ == "__main__":
    main()
