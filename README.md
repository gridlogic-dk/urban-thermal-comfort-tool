# Street-Level Thermal Comfort Pipeline

A modular Python + Grasshopper/Ladybug pipeline that turns Google Street View
panoramas into a point-by-point, month-by-month outdoor thermal comfort
(UTCI) dataset - built and tested on a 2,875-point survey of **Business Bay,
Dubai**, and reusable for any other city by editing one config file.

It combines street-level semantic segmentation (sky/building/vegetation/road
exposure) with environmental simulation (shadow hours, incident radiation,
sky view factor, mean radiant temperature, UTCI) to produce a GeoJSON that's
ready for GIS, dashboards, or urban design decision-making - e.g. "which
streets need shade trees vs. shade structures vs. are already comfortable."

![Pipeline overview](assets/pipeline_diagram.png)

## Interactive dashboard

`dashboard/dubai_enhanced_dashboard_offline.html` visualises the final
GeoJSON output: click any of the 2,875 points to inspect its monthly UTCI,
shadow/radiation curves, sky view factor, and scene composition, or box-select
an area to compare it against the dataset median.

![Dashboard screenshot](assets/dashboard_screenshot.png)

**No API key, no build step, no server.** Leaflet and Chart.js are bundled
directly into the HTML file (open it straight from disk, in any browser) -
the only network call it makes is to CartoDB's free, keyless basemap tile
service for the background map; everything else, including all 2,875 points'
data (`dashboard/dashboard_data.js`), works fully offline. If you'd rather not
make that one basemap call at all, delete the `L.tileLayer(...)` block near
the top of the `<script>` section - the coloured points still render fine on
a blank background.

## Why

Standard microclimate studies are usually done at a handful of representative
points chosen by a designer. This pipeline instead runs the analysis at every
street-view vantage point along a network - thousands of points instead of a
dozen - so thermal comfort becomes a continuous, mappable layer you can
overlay on a masterplan rather than a handful of spot-checks.

## Pipeline

```
Stage 0   Google Street View panoramas
          -> ZenSVI semantic segmentation (sky/building/vegetation/road masks)
Stage 1   Panorama metadata (lat/lng/elevation/pano_id) -> master_metadata.csv
Stage 2   Segmentation masks -> per-class pixel ratios (sky/building/veg/road/...)
Stage 3   Points -> GeoJSON + CSV formatted for Grasshopper
Stage 3.5 MANUAL: run Ladybug Tools in Grasshopper for 12 monthly design days
          (shadow hours, incident radiation, sky view factor, MRT, UTCI)
Bridges   Raw Grasshopper CSVs -> clean pipeline-format CSVs (per metric)
Stage 4   Merge all metrics + metadata + segmentation -> long-format CSV
Stage 5   Pivot to final per-point GeoJSON: monthly analysis + annual summary
          + planning priority (Critical / High / Medium / Low)
```

Each stage is a standalone script, config-driven, idempotent (safe to
re-run), and logs to `<output>/logs/<stage>.log`.

## Repo contents

```
stage0_segment_streetview.py         ZenSVI semantic segmentation
stage1_extract_metadata.py           Panorama metadata -> CSV
stage2_analyze_segmentation.py       Segmentation masks -> class ratios
stage3_prepare_grasshopper.py        Points -> Grasshopper input
stage4_merge_grasshopper_results.py  Merge all metrics
stage5_build_final_geojson.py        Final per-point GeoJSON
bridge_shadow_to_pipeline.py         Raw GH shadow CSV -> pipeline format
bridge_radiation_to_pipeline.py      Raw GH radiation CSV -> pipeline format
bridge_svf_to_pipeline.py            Raw GH sky-view-factor CSV -> pipeline format
bridge_utci_to_pipeline.py           Raw GH UTCI/MRT CSV -> pipeline format
compile_final.py                     Final compile + audit pass
utils.py                             Config loading, logging, path/ID helpers
config.example.yaml                  Example config (Business Bay, Dubai)
requirements.txt
dashboard/                           Self-contained offline results dashboard
                                      (no API key, Leaflet+Chart.js bundled in)
claude-skill/                        Claude Code skill for wiring/debugging
                                      the Ladybug <-> Python integration
```

Data (panoramas, segmentation masks, EPW files, Grasshopper output, generated
CSV/GeoJSON) is **not** included in this repo - see `.gitignore`. Bring your
own street-view imagery and Rhino/Grasshopper model.

## Install

```bash
git clone <this-repo>
cd <this-repo>
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then edit paths/coordinates for your project
```

Stage 0 (segmentation) needs a CUDA-capable GPU for reasonable speed; pass
`--device cpu` to run without one.

## Run

```bash
python stage0_segment_streetview.py --config config.yaml
python stage1_extract_metadata.py
python stage2_analyze_segmentation.py
python stage3_prepare_grasshopper.py

# Manual step: open your Grasshopper file, run the Ladybug Tools analyses
# (Direct Sun Hours, Incident Radiation, Sky View Factor, Outdoor Solar MRT,
# UTCI Comfort) for each of the 12 design days (21st of each month, 6 AM-6 PM),
# and export one CSV per month per metric.

python bridge_shadow_to_pipeline.py
python bridge_radiation_to_pipeline.py
python bridge_svf_to_pipeline.py
python bridge_utci_to_pipeline.py

python stage4_merge_grasshopper_results.py
python stage5_build_final_geojson.py
python compile_final.py
```

Every script accepts `--config <path>` and most accept `--skip-if-exists` to
resume a partial run.

## Reuse for another city

1. Copy `config.example.yaml` -> `config.yaml`, and edit `project.name`,
   `project.latitude/longitude`, `metadata.coordinate_bounds`, the paths
   under `project.paths`, and the EPW weather file reference.
2. Drop your street-view images + metadata JSON into the folders described
   in `config.yaml`.
3. Run stages 0 -> 5 as above.

## Using this with Claude

`claude-skill/ladybug-thermal-pipeline.skill` packages the patterns in this
repo (Grasshopper data-tree wiring, the CSV-cleaning "bridge" pattern, unit
conversions, and the config-driven merge) as a Claude Code / Claude Cowork
skill, so Claude can help you wire a new Ladybug analysis into the pipeline
or debug one that "isn't producing output." See `claude-skill/README.md` for
install instructions.

## Credits

- **[ZenSVI](https://zensvi.readthedocs.io/en/latest/autoapi/zensvi/cv/index.html)**
  - Stage 0's semantic segmentation (`zensvi.cv.Segmenter`, Mapillary Vistas
  model) is the foundation this whole pipeline is built on. All credit for
  the segmentation model and the street-view toolkit goes to the ZenSVI
  project and its authors. If you use this pipeline, please also credit and
  cite ZenSVI directly.
- **[Ladybug Tools](https://www.ladybug.tools/)** for the Grasshopper
  environmental analysis components (shadow, radiation, sky view factor,
  MRT, UTCI) that drive stages 3.5 and the bridge scripts.
- Built with assistance from **Claude** (Anthropic) for pipeline structure,
  the bridge-script pattern, and the packaged Claude skill.

## License

MIT - see [LICENSE](LICENSE). Third-party tools above are used under their
own licenses.
