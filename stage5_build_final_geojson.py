"""
Stage 5 - Build Final Thermal GeoJSON
======================================
Pivots merged_thermal_data.csv to one GeoJSON Feature per point with
month-by-month thermal arrays, UTCI categories from config thresholds, and
an annual summary (avg/max/min UTCI, comfortable vs hot months, planning
priority).

Tolerates missing columns: if utci / utci_max / radiation_kwh / svf / mrt
isn't in the merged CSV yet, fields default to null and annual summary is
computed from whatever is available.

Usage:
    python stage5_build_final_geojson.py [--config config.yaml]

Output:
    00_Source/Thermal/Merged_Data/dubai_thermal_complete.geojson
"""
from __future__ import annotations

import argparse
import calendar
import json
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


def _classify_utci(utci, thresholds):
    if utci is None:
        return "Unknown", "N/A"
    for band in thresholds:
        if band["min"] <= utci < band["max"]:
            return band["label"], band["action"]
    return "Unknown", "N/A"


def _planning_priority(avg_utci, hot_months, has_utci):
    if not has_utci:
        return "Unknown - UTCI not computed yet"
    if len(hot_months) > 6:
        return "Critical - Add shade & water features"
    if len(hot_months) > 3:
        return "High - Add shade & vegetation"
    if avg_utci is not None and avg_utci > 32:
        return "Medium - Shade trees recommended"
    return "Low - Acceptable comfort"


def _maybe_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _col_or_none(row, col):
    """Read row[col] if column exists, else None."""
    if col not in row.index:
        return None
    return _maybe_float(row[col])


def build_final_geojson(cfg):
    logger = setup_logging(cfg, "stage5_build_geojson")

    merged_file = resolve_path(cfg, cfg["merge"]["output_file"])
    if not merged_file.exists():
        raise FileNotFoundError(f"Merged CSV not found: {merged_file}. Run stage4 first.")

    df = pd.read_csv(merged_file)
    logger.info("Loaded %d rows from %s", len(df), merged_file)

    thresholds = cfg["comfort_thresholds"]["utci_categories"]
    seg_cols = [c for c in df.columns if c.endswith("_ratio")]
    has_utci      = "utci"      in df.columns
    has_utci_max  = "utci_max"  in df.columns
    has_utci_min  = "utci_min"  in df.columns
    has_utci_noon = "utci_noon" in df.columns
    has_mrt       = "mrt"       in df.columns
    has_rad       = "radiation_kwh" in df.columns
    has_svf       = "svf"       in df.columns
    logger.info(
        "Optional columns: utci=%s utci_max=%s utci_min=%s utci_noon=%s "
        "radiation_kwh=%s svf=%s mrt=%s",
        has_utci, has_utci_max, has_utci_min, has_utci_noon, has_rad, has_svf, has_mrt,
    )

    features = []
    for point_id, group in df.groupby("point_id", sort=True):
        group = group.sort_values("month")
        first = group.iloc[0]

        # Per-month entries
        monthly = []
        for _, row in group.iterrows():
            utci_val = _col_or_none(row, "utci")
            category, action = _classify_utci(utci_val, thresholds)
            utci_max_val = _col_or_none(row, "utci_max")
            peak_category, peak_action = _classify_utci(utci_max_val, thresholds)
            month_num = int(row["month"]) if pd.notna(row.get("month")) else None
            monthly.append({
                "month": month_num,
                "month_name": calendar.month_name[month_num] if month_num else None,
                "shadow_hours":  _col_or_none(row, "shadow_hours"),
                "radiation_kwh": _col_or_none(row, "radiation_kwh"),
                "svf":           _col_or_none(row, "svf"),
                "utci":          utci_val,
                "utci_max":      utci_max_val,
                "utci_min":      _col_or_none(row, "utci_min"),
                "utci_noon":     _col_or_none(row, "utci_noon"),
                "utci_category":      category,
                "utci_peak_category": peak_category,
                "recommendation":      action,
                "peak_recommendation": peak_action,
                "mrt":           _col_or_none(row, "mrt"),
            })

        # Annual summary
        if has_utci:
            utci_series = pd.to_numeric(group["utci"], errors="coerce").dropna()
            if has_utci_max:
                peak_col = pd.to_numeric(group["utci_max"], errors="coerce")
                hot_months = [
                    int(r["month"]) for (_, r), v in zip(group.iterrows(), peak_col)
                    if pd.notna(v) and v > 38
                ]
                peak_max_utci = float(peak_col.dropna().max()) if peak_col.dropna().size else None
            else:
                hot_months = [
                    int(r["month"]) for _, r in group.iterrows()
                    if pd.notna(r.get("utci")) and r["utci"] > 38
                ]
                peak_max_utci = None
            comfortable_months = [
                int(r["month"]) for _, r in group.iterrows()
                if pd.notna(r.get("utci")) and 9 <= r["utci"] <= 26
            ]
            avg_utci = float(utci_series.mean()) if not utci_series.empty else None
            max_utci = float(utci_series.max()) if not utci_series.empty else None
            min_utci = float(utci_series.min()) if not utci_series.empty else None
        else:
            comfortable_months = []
            hot_months = []
            avg_utci = max_utci = min_utci = peak_max_utci = None

        shadow_series = pd.to_numeric(group.get("shadow_hours", pd.Series(dtype=float)),
                                       errors="coerce").dropna()
        if not shadow_series.empty:
            avg_shadow = float(shadow_series.mean())
            max_shadow = float(shadow_series.max())
            min_shadow = float(shadow_series.min())
        else:
            avg_shadow = max_shadow = min_shadow = None

        annual_summary = {
            "avg_utci": avg_utci,
            "max_utci": max_utci,
            "min_utci": min_utci,
            "peak_utci": peak_max_utci,
            "avg_shadow_hours": avg_shadow,
            "max_shadow_hours": max_shadow,
            "min_shadow_hours": min_shadow,
            "comfortable_months": comfortable_months,
            "hot_months": hot_months,
            "planning_priority": _planning_priority(avg_utci, hot_months, has_utci),
        }

        seg_props = {c: _col_or_none(first, c) for c in seg_cols}

        coords = [_col_or_none(first, "lng"), _col_or_none(first, "lat")]
        if coords[0] is None or coords[1] is None:
            logger.warning("Skipping %s - missing coordinates", point_id)
            continue

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": {
                "point_id":  str(point_id),
                "elevation": _col_or_none(first, "elevation"),
                "pano_id":   str(first.get("pano_id")) if pd.notna(first.get("pano_id")) else "",
                "segmentation":     seg_props,
                "monthly_analysis": monthly,
                "annual_summary":   annual_summary,
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}

    output_file = resolve_path(cfg, cfg["output"]["final_geojson"])
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as fh:
        json.dump(geojson, fh, indent=2)

    logger.info("Stage 5 complete: %d features written to %s", len(features), output_file)
    return geojson


def main():
    parser = argparse.ArgumentParser(description="Stage 5: build final thermal GeoJSON")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    build_final_geojson(cfg)


if __name__ == "__main__":
    main()
