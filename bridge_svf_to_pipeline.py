"""
Bridge: convert raw GH Sky View Factor CSV and MERGE into pipeline.

Reads one file:
    02_Process/SVF_Analysis/svf_result.csv
        x, y, z, svf       (or x, y, z, view_percent / results - auto-detected)

SVF is time-invariant (just geometry), so the same value broadcasts across
all 12 months of the existing grasshopper_results_month_NN.csv files.

If the values are 0..100 (percentage), they're auto-normalized to 0..1.

Usage:
    python bridge_svf_to_pipeline.py
    python bridge_svf_to_pipeline.py --config config.yaml
"""
from __future__ import annotations

import argparse
import io
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


def _pick_value_column(df, preferred):
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


def bridge_svf(cfg):
    logger = setup_logging(cfg, "bridge_svf_to_pipeline")
    bridge = cfg["bridge_svf"]

    src_path = resolve_path(cfg, bridge["input_file"])
    if not src_path.exists():
        raise FileNotFoundError(f"SVF source CSV not found: {src_path}")
    if src_path.stat().st_size == 0:
        raise RuntimeError(f"SVF source CSV is empty (0 bytes): {src_path}")

    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    points = pd.read_csv(points_csv)
    point_ids = points["point_id"].astype(str).tolist()
    logger.info("Loaded %d point_ids", len(point_ids))

    raw = _read_robust_csv(src_path).dropna(how="all").reset_index(drop=True)
    if len(raw) == len(point_ids) + 1:
        logger.info("Trimming 1 extra row")
        raw = raw.iloc[:len(point_ids)].reset_index(drop=True)
    if len(raw) != len(point_ids):
        raise ValueError(
            f"{src_path.name}: row count {len(raw)} != points count {len(point_ids)}"
        )

    value_col = _pick_value_column(raw, preferred=["svf", "view_percent", "view_factors", "results", "sky_view"])
    logger.info("Value column detected: %s", value_col)

    svf = pd.to_numeric(raw[value_col], errors="coerce")
    logger.info("Raw SVF: min=%.3f max=%.3f mean=%.3f", svf.min(), svf.max(), svf.mean())

    # Auto-normalize percentages -> fractions
    if svf.max() > 1.5:
        logger.info("Values appear to be percentages (max=%.2f), dividing by 100", svf.max())
        svf = svf / 100.0
    svf = svf.clip(lower=0, upper=1).round(4)
    logger.info("Normalized SVF: min=%.3f max=%.3f mean=%.3f", svf.min(), svf.max(), svf.mean())

    # Broadcast to all 12 monthly pipeline CSVs
    out_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    naming = cfg["grasshopper"]["output_naming_template"]

    merged = []
    for month in range(1, 13):
        pipeline_csv = out_dir / naming.format(month=month)
        if not pipeline_csv.exists():
            logger.warning("Pipeline file missing for month %d - skipping", month)
            continue
        pipe = pd.read_csv(pipeline_csv)
        if len(pipe) != len(point_ids):
            raise ValueError(
                f"{pipeline_csv.name}: row mismatch (got {len(pipe)}, expected {len(point_ids)})"
            )
        pipe["svf"] = svf.values
        pipe.to_csv(pipeline_csv, index=False)
        logger.info("  -> %s (cols: %s)", pipeline_csv.name, list(pipe.columns))
        merged.append(pipeline_csv)

    logger.info("SVF bridge complete: %d files updated", len(merged))
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    bridge_svf(cfg)


if __name__ == "__main__":
    main()
