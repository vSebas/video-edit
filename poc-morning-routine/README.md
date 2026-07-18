# Morning Routine Video-Editing Proof of Concept

This directory contains the first benchmark and reusable artifacts for the AI-assisted video-editing project.

The benchmark uses the seven existing `IMG_*.mp4` morning-routine clips. Its immediate purpose is to test grounded media understanding, creative recommendations, missing-shot advice, deterministic timeline construction, rendering, and editable DaVinci export without committing to a full editor architecture.

## Layout

- `BENCHMARK.md` — human-readable goal and acceptance criteria
- `benchmark.json` — machine-readable benchmark configuration
- `schemas/` — versioned JSON Schemas for pipeline artifacts
- `scripts/index_media.sh` — dependency-light FFmpeg media indexer
- `scripts/apply_semantic_observations.sh` — reproducibly merges reviewed observations into per-asset analysis
- `scripts/validate_artifacts.py` — validates schemas, concept durations, and all referenced time ranges
- `scripts/render_reference_edit.py` — renders the selected plan with deterministic FFmpeg filters
- `scripts/validate_edit.py` — validates the plan, rendered media, frame alignment, geometry, and audio peak
- `scripts/export_timelines.py` — exports and round-trip validates OTIO and DaVinci-compatible XMEML
- `scripts/export_opentake_project.py` — creates a native `.opentake` directory project
- `semantic/verified-observations.json` — observations reconciled from backend output and targeted frame review
- `SEMANTIC_BACKEND_COMPARISON.md` — FireRed, Crayotter, and independent-review findings
- `IMPLEMENTATION_RESULTS.md` — completed render/export results and remaining manual checks
- `OPENTAKE_SPIKE.md` — hands-on OpenTake build, edit, persistence, and interchange verdict
- `artifacts/creative-concepts.json` — three distinct, evidence-grounded concepts with missing-shot advice
- `artifacts/` — generated inventory, per-asset analysis, verification sheets, and reports

## Run the indexer

```bash
bash scripts/index_media.sh
```

An alternative source directory can be supplied as the first argument:

```bash
bash scripts/index_media.sh /path/to/source/clips
```

The indexer does not modify source media. It creates hashes, ffprobe metadata, FFmpeg scene boundaries, representative frames/contact sheets, and audio loudness/silence diagnostics. If no local ASR backend is available, the transcript state is recorded explicitly as unavailable.

## Apply the reviewed semantic layer

```bash
bash scripts/apply_semantic_observations.sh
```

## Validate recommendation artifacts

Create the project-local environment once:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then validate all current analysis and concept artifacts:

```bash
.venv/bin/python scripts/validate_artifacts.py
```

## Build the selected reference edit

```bash
.venv/bin/python scripts/validate_edit.py
.venv/bin/python scripts/render_reference_edit.py
.venv/bin/python scripts/validate_edit.py \
  --render artifacts/reference-edit/morning-routine-review.mp4
.venv/bin/python scripts/export_timelines.py
.venv/bin/python scripts/export_opentake_project.py
```

Primary outputs:

- `artifacts/reference-edit/morning-routine-review.mp4`
- `artifacts/edit-plan.json`
- `artifacts/timelines/morning-routine.otio`
- `artifacts/timelines/morning-routine-davinci.xml`
- `artifacts/timelines/morning-routine.opentake/`
