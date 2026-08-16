# Claude Skill: Ladybug -> Python Thermal Pipeline

`ladybug-thermal-pipeline.skill` packages the workflow in this repo as a
Claude Code / Claude (Cowork) skill: how to wire Ladybug Tools analyses
(Direct Sun Hours, Incident Radiation, Sky View Factor, Outdoor Solar MRT,
UTCI) in Grasshopper, how to write a Python "bridge" script that cleans and
converts the raw Grasshopper CSV output, and how the config-driven merge in
`stage4`/`stage5` expects that data to look.

It's useful if you're adapting this pipeline to a new city, adding a new
Ladybug metric, or debugging a Grasshopper analysis that "isn't working"
(empty CSVs, tree-shape mismatches, wrong units).

## Install

1. Download `ladybug-thermal-pipeline.skill` from this folder.
2. In Claude Code or Claude (Cowork), install it as a skill/plugin (drag the
   file into the app, or use your `/plugin` / skill-install flow - the exact
   steps depend on which Claude product you're using).
3. Once installed, mention Ladybug, Grasshopper, UTCI, MRT, SVF, shadow
   hours, or incident radiation in a conversation and Claude will pull in
   this skill automatically.

## What's inside

- `SKILL.md` - the workflow, the non-negotiable data contracts, and the
  "isn't working" diagnostic checklist.
- `references/` - one file per Ladybug analysis (shadow, radiation, svf,
  utci) plus the bridge-script pattern and pipeline-integration notes.
- `scripts/bridge_template.py` - a runnable starting point for writing a new
  bridge script (handles NUL bytes, BOM markers, off-by-one rows, and
  point_id alignment once, so you don't have to debug those every time).
