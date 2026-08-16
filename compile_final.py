"""
Compile Final - one-command orchestrator
========================================
Runs every bridge + merge + final-geojson stage in the correct order, so the
final compiled output (CSV + GeoJSON) is rebuilt from whatever raw Grasshopper
data currently lives under 02_Process/.

Order of operations:
    1. bridge_shadow_to_pipeline      (shadow_hours)
    2. bridge_radiation_to_pipeline   (radiation_kwh)
    3. bridge_svf_to_pipeline         (svf)
    4. bridge_utci_to_pipeline        (utci, utci_max, utci_min, utci_noon)
    5. stage4_merge_grasshopper       -> Merged_Data/merged_thermal_data.csv
    6. stage5_build_final_geojson     -> Merged_Data/dubai_thermal_complete.geojson

Each step is wrapped in a try/except so a single failure logs a clear error
without aborting the rest of the pipeline.  Use the flags to skip stages.

Usage:
    python compile_final.py
    python compile_final.py --skip-shadow --skip-radiation
    python compile_final.py --only-utci          # bridge UTCI + re-merge + re-build geojson
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from utils import load_config, resolve_path, setup_logging


# ----------------------------------------------------------------------------
# Step runner
# ----------------------------------------------------------------------------

def _run_step(name: str, fn, logger, *args, **kwargs) -> bool:
    logger.info("")
    logger.info("=" * 70)
    logger.info(">>> STEP: %s", name)
    logger.info("=" * 70)
    t0 = time.perf_counter()
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.error("STEP FAILED: %s\n%s", name, traceback.format_exc())
        return False
    elapsed = time.perf_counter() - t0
    logger.info("<<< STEP OK: %s  (%.1fs)", name, elapsed)
    return True


# ----------------------------------------------------------------------------
# Sanity-check on the final compiled CSV
# ----------------------------------------------------------------------------

def _audit_merged_csv(cfg, logger) -> None:
    import pandas as pd

    merged_file = resolve_path(cfg, cfg["merge"]["output_file"])
    if not merged_file.exists():
        logger.warning("Audit: merged file missing (%s)", merged_file)
        return

    df = pd.read_csv(merged_file)
    logger.info("Audit: merged file has %d rows", len(df))
    logger.info("Audit: columns = %s", list(df.columns))

    for col in ("shadow_hours", "radiation_kwh", "svf",
                "utci", "utci_max", "utci_min", "utci_noon"):
        if col in df.columns:
            nz = df[col].notna().sum()
            pct = 100.0 * nz / len(df) if len(df) else 0
            logger.info("Audit: %s populated in %d / %d rows (%.1f%%)",
                        col, nz, len(df), pct)
        else:
            logger.warning("Audit: %s MISSING from merged file", col)

    if "point_id" in df.columns and "month" in df.columns:
        unique_points = df["point_id"].nunique()
        months = sorted(df["month"].dropna().unique().tolist())
        logger.info("Audit: %d unique points x months %s", unique_points, months)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all bridges + merge + final geojson in one shot.",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--skip-shadow",    action="store_true")
    parser.add_argument("--skip-radiation", action="store_true")
    parser.add_argument("--skip-svf",       action="store_true")
    parser.add_argument("--skip-utci",      action="store_true")
    parser.add_argument("--skip-merge",     action="store_true")
    parser.add_argument("--skip-geojson",   action="store_true")
    parser.add_argument(
        "--only-utci",
        action="store_true",
        help="Run only: bridge_utci + stage4 merge + stage5 final geojson.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = setup_logging(cfg, "compile_final")
    logger.info("Project: %s", cfg["project"]["name"])
    logger.info("Project root: %s", cfg["_root"])

    if args.only_utci:
        args.skip_shadow = True
        args.skip_radiation = True
        args.skip_svf = True

    # Import here so a missing module gives a clean error in the relevant step
    failures: list[str] = []

    if not args.skip_shadow:
        from bridge_shadow_to_pipeline import convert_all as bridge_shadow
        if not _run_step("bridge_shadow_to_pipeline", bridge_shadow, logger, cfg):
            failures.append("shadow")
    else:
        logger.info("--skip-shadow: skipping shadow bridge")

    if not args.skip_radiation:
        from bridge_radiation_to_pipeline import convert_all as bridge_radiation
        if not _run_step("bridge_radiation_to_pipeline", bridge_radiation, logger, cfg):
            failures.append("radiation")
    else:
        logger.info("--skip-radiation: skipping radiation bridge")

    if not args.skip_svf:
        from bridge_svf_to_pipeline import bridge_svf
        if not _run_step("bridge_svf_to_pipeline", bridge_svf, logger, cfg):
            failures.append("svf")
    else:
        logger.info("--skip-svf: skipping SVF bridge")

    if not args.skip_utci:
        from bridge_utci_to_pipeline import convert_all as bridge_utci
        if not _run_step("bridge_utci_to_pipeline", bridge_utci, logger, cfg):
            failures.append("utci")
    else:
        logger.info("--skip-utci: skipping UTCI bridge")

    if not args.skip_merge:
        from stage4_merge_grasshopper_results import merge_grasshopper_results
        if not _run_step("stage4_merge_grasshopper_results",
                         merge_grasshopper_results, logger, cfg):
            failures.append("stage4")
    else:
        logger.info("--skip-merge: skipping stage 4")

    if not args.skip_geojson:
        from stage5_build_final_geojson import build_final_geojson
        if not _run_step("stage5_build_final_geojson",
                         build_final_geojson, logger, cfg):
            failures.append("stage5")
    else:
        logger.info("--skip-geojson: skipping stage 5")

    logger.info("")
    logger.info("=" * 70)
    logger.info("FINAL AUDIT")
    logger.info("=" * 70)
    try:
        _audit_merged_csv(cfg, logger)
    except Exception:
        logger.error("Audit failed:\n%s", traceback.format_exc())

    logger.info("")
    if failures:
        logger.error("compile_final.py: %d step(s) FAILED -> %s",
                     len(failures), ", ".join(failures))
        return 1

    logger.info("compile_final.py: all steps completed successfully.")
    logger.info("Final compiled CSV : %s",
                resolve_path(cfg, cfg["merge"]["output_file"]))
    logger.info("Final compiled GeoJSON : %s",
                resolve_path(cfg, cfg["output"]["final_geojson"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
