"""
Shared utilities for the Dubai Thermal Comfort Pipeline.

All stage scripts import from this module:
    - load_config / resolve_path  (YAML + project-root path handling)
    - setup_logging               (file + console logging)
    - normalize_point_id          (filename -> 'Street_View_360_N')
    - validate_coordinates        (GPS sanity check)
    - read_nested                 (dotted-key access on dict)
    - save_checkpoint             (DataFrame -> CSV)
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


# ----------------------------------------------------------------------------
# Config & paths
# ----------------------------------------------------------------------------

def project_root(start: Path | None = None) -> Path:
    """Walk up from *start* (or this file) until we find 00_Source/ next to 01_Code/."""
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "00_Source").is_dir() and (parent / "01_Code").is_dir():
            return parent
    # Fall back to two levels up from this file (01_Code/thermal_comfort_pipeline/utils.py).
    return Path(__file__).resolve().parents[2]


def load_config(config_file: str | Path = "config.yaml") -> dict:
    """Load YAML config and stamp the resolved project root onto it."""
    cfg_path = Path(config_file)
    if not cfg_path.is_absolute():
        cfg_path = Path(__file__).resolve().parent / cfg_path
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    root_override = (cfg.get("project") or {}).get("root", "") or ""
    cfg["_root"] = Path(root_override).resolve() if root_override else project_root()
    cfg["_config_path"] = cfg_path
    return cfg


def resolve_path(cfg: dict, rel_or_abs: str | Path) -> Path:
    """Resolve a config path relative to the project root unless already absolute."""
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (cfg["_root"] / p).resolve()


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

def setup_logging(cfg: dict, stage_name: str) -> logging.Logger:
    """Configure root logger to write to logs/<stage>.log and stdout."""
    log_dir = resolve_path(cfg, cfg["output"]["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{stage_name}.log"

    level_name = (cfg.get("logging") or {}).get("level", "INFO").upper()
    fmt = (cfg.get("logging") or {}).get(
        "log_format", "[%(asctime)s] %(levelname)s: %(message)s"
    )

    # Reset handlers so re-runs in the same process don't double-log.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger(stage_name)
    logger.info("=" * 70)
    logger.info("Stage start: %s", stage_name)
    logger.info("Log file: %s", log_file)
    return logger


# ----------------------------------------------------------------------------
# Filename / point_id helpers
# ----------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_point_id(name: str, style: str = "underscore") -> str:
    """
    Convert a filename stem like 'Street View 360 12' to 'Street_View_360_12'.

    Strips common suffixes ('.metadata', '_colored_segmented', '_blend')
    before normalising whitespace.
    """
    stem = Path(name).stem  # drops final extension if any
    for suffix in ("_colored_segmented", "_blend", ".metadata"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    stem = stem.strip()
    if style == "underscore":
        return _WHITESPACE_RE.sub("_", stem)
    return stem


def point_id_from_path(path: str | Path, style: str = "underscore") -> str:
    """Convenience wrapper - takes any file path and returns the canonical point_id."""
    return normalize_point_id(Path(path).name, style=style)


# ----------------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------------

def validate_coordinates(lat: float, lng: float, bounds: dict | None = None) -> bool:
    """True if (lat, lng) falls inside the configured bounding box."""
    if lat is None or lng is None:
        return False
    if bounds is None:
        bounds = {"lat_min": -90, "lat_max": 90, "lng_min": -180, "lng_max": 180}
    try:
        return (
            bounds["lat_min"] <= float(lat) <= bounds["lat_max"]
            and bounds["lng_min"] <= float(lng) <= bounds["lng_max"]
        )
    except (TypeError, ValueError):
        return False


def read_nested(data: dict, dotted_key: str, default: Any = None) -> Any:
    """Read 'date.year' style keys from nested dicts; returns *default* if missing."""
    cur: Any = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ----------------------------------------------------------------------------
# I/O helpers
# ----------------------------------------------------------------------------

def save_checkpoint(df, checkpoint_file: str | Path, logger: logging.Logger | None = None) -> Path:
    """Write a DataFrame to CSV, creating parent dirs first."""
    out = Path(checkpoint_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    if logger is not None:
        logger.info("Checkpoint saved: %s (%d rows)", out, len(df))
    return out


def iter_files(folder: str | Path, pattern: str) -> Iterable[Path]:
    """Sorted glob over a directory (kept as a thin wrapper for testability)."""
    return sorted(Path(folder).glob(pattern))
