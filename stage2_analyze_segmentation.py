"""
Stage 2 - Analyze Segmentation Masks
=====================================
Reads each *_colored_segmented.png mask, matches every pixel against the
Mapillary colour palette defined in config.yaml, and writes pixel ratios per
semantic class.

Usage:
    python stage2_analyze_segmentation.py [--config config.yaml] [--skip-if-exists]

Output:
    00_Source/Thermal/Segmentation_Extracted/segmentation_metrics.csv
        columns: point_id, sky_ratio, building_ratio, vegetation_ratio,
                 road_ratio, pavement_ratio, ..., other_ratio
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from tqdm import tqdm

from utils import (
    load_config,
    resolve_path,
    setup_logging,
    point_id_from_path,
    save_checkpoint,
)


def _palette_arrays(palette: dict[str, list[int]]) -> tuple[list[str], np.ndarray]:
    """Convert palette dict to (labels, Nx3 uint8 array)."""
    labels = list(palette.keys())
    rgb = np.array([palette[k] for k in labels], dtype=np.int16)
    return labels, rgb


def analyze_mask(path: Path, labels: list[str], rgb: np.ndarray, tol: int) -> dict[str, float]:
    """Return {label: pixel_ratio, 'other': ratio} for a single mask."""
    with Image.open(path) as im:
        arr = np.array(im.convert("RGB"), dtype=np.int16)
    h, w, _ = arr.shape
    flat = arr.reshape(-1, 3)                          # (P, 3)

    # Manhattan distance from each pixel to each palette colour: (P, N)
    diff = np.abs(flat[:, None, :] - rgb[None, :, :]).sum(axis=2)
    best = diff.argmin(axis=1)                         # (P,)
    best_dist = diff[np.arange(diff.shape[0]), best]   # (P,)
    matched = best_dist <= tol

    total = flat.shape[0]
    counts = np.bincount(best[matched], minlength=len(labels))
    ratios = {f"{lbl}_ratio": float(counts[i]) / total for i, lbl in enumerate(labels)}
    ratios["other_ratio"] = float((~matched).sum()) / total
    return ratios


def analyze_all_masks(cfg: dict) -> pd.DataFrame:
    logger = setup_logging(cfg, "stage2_segmentation_analysis")

    seg_dir = resolve_path(cfg, cfg["project"]["paths"]["segmentation_dir"])
    suffix = cfg["filenames"]["segmentation_colored_suffix"]
    pid_style = cfg["filenames"]["point_id_style"]
    tol = int(cfg["segmentation"].get("color_match_tolerance", 8))

    labels, rgb = _palette_arrays(cfg["segmentation"]["class_palette"])
    logger.info("Palette classes: %s (tolerance=%d)", labels, tol)

    if not seg_dir.is_dir():
        raise FileNotFoundError(f"Segmentation directory not found: {seg_dir}")

    files = sorted(seg_dir.glob(f"*{suffix}"))
    logger.info("Found %d segmented masks in %s", len(files), seg_dir)
    if not files:
        raise RuntimeError(f"No '*{suffix}' files in {seg_dir}")

    records: list[dict] = []
    for mask in tqdm(files, desc="Analyzing masks", unit="img"):
        point_id = point_id_from_path(mask, style=pid_style)
        try:
            ratios = analyze_mask(mask, labels, rgb, tol)
            ratios["point_id"] = point_id
            records.append(ratios)
        except Exception as exc:
            logger.warning("Failed mask %s: %s", mask.name, exc)

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        # Put point_id first, then ratio columns alphabetically.
        ratio_cols = sorted(c for c in df.columns if c != "point_id")
        df = df[["point_id", *ratio_cols]].sort_values("point_id").reset_index(drop=True)

    output_file = resolve_path(cfg, cfg["segmentation"]["output_file"])
    save_checkpoint(df, output_file, logger)
    logger.info("Stage 2 complete: %d masks analysed", len(df))
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2: analyse segmentation masks")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--skip-if-exists", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_file = resolve_path(cfg, cfg["segmentation"]["output_file"])

    if args.skip_if_exists and output_file.exists():
        print(f"[stage2] Output already present, skipping: {output_file}")
        return
    if cfg["processing"].get("skip_segmentation_analysis") and output_file.exists():
        print(f"[stage2] skip_segmentation_analysis=true and file exists: {output_file}")
        return

    analyze_all_masks(cfg)


if __name__ == "__main__":
    main()
