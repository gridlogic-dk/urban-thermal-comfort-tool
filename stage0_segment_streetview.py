"""
Stage 0 - Semantic Segmentation of Street-View Panoramas
===========================================================
Runs semantic segmentation on Google Street View panoramas using ZenSVI's
Segmenter (Mapillary Vistas model), producing the coloured/blend masks that
Stage 2 (stage2_analyze_segmentation.py) reads to compute per-class pixel
ratios (sky, building, vegetation, road, etc.) for every point.

Credit: this stage is a thin wrapper around zensvi.cv.Segmenter from the
ZenSVI library. See https://zensvi.readthedocs.io/en/latest/autoapi/zensvi/cv/index.html
for full documentation of the underlying model and options.

Usage:
    python stage0_segment_streetview.py --config config.yaml [--device cuda|cpu]

Output:
    <segmentation_dir>/*_colored_segmented.png, *_blend.png
    (paths are read from config.yaml -> project.paths.images_dir / segmentation_dir)
"""

from __future__ import annotations

import argparse

from utils import load_config, resolve_path, setup_logging


def run_segmentation(cfg: dict, device: str, batch_size: int) -> None:
    logger = setup_logging(cfg, "stage0_segmentation")

    try:
        from zensvi.cv import Segmenter
    except ImportError as exc:
        raise ImportError(
            "zensvi is required for Stage 0. Install with: pip install zensvi\n"
            "See https://zensvi.readthedocs.io/ for setup and GPU requirements."
        ) from exc

    images_dir = resolve_path(cfg, cfg["project"]["paths"]["images_dir"])
    seg_dir = resolve_path(cfg, cfg["project"]["paths"]["segmentation_dir"])
    seg_dir.mkdir(parents=True, exist_ok=True)

    if not images_dir.is_dir():
        raise FileNotFoundError(f"Street-view image directory not found: {images_dir}")

    jpg_count = len(list(images_dir.glob(f"*{cfg['filenames']['image_extension']}")))
    logger.info("Found %d images in %s", jpg_count, images_dir)
    logger.info("Segmented masks will be written to %s", seg_dir)

    segmenter = Segmenter(
        dataset="mapillary",  # best coverage for urban street scenes
        task="semantic",
        device=device,
    )

    segmenter.segment(
        dir_input=str(images_dir),
        dir_summary_output=str(seg_dir),
        dir_image_output=str(seg_dir),
        batch_size=batch_size,
    )

    logger.info("Stage 0 complete. Run stage2_analyze_segmentation.py next.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 0: ZenSVI semantic segmentation")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                         help="Set to 'cpu' if no CUDA-capable GPU is available")
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_segmentation(cfg, device=args.device, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
