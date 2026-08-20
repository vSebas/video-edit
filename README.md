# AI-Assisted Video Editing Workspace

This directory now separates code, third-party source, surviving experiment data, and generated outputs explicitly.

## Current status

See `STATUS_AND_ROADMAP.md` for the completed milestones, tool-test matrix,
OpenStoryline configuration finding, model strategy, outstanding checks, and
ordered implementation plan.

## Code we own

- `app/` — local project API and browser workflow for footage indexing, concept review, rendering, and editable exports
- `app/pipeline/` — deterministic rendering, OTIO/XMEML export, and independent edit validation
- `app/schemas/` — versioned media-analysis, concept, edit-plan, and validation schemas
- `bench/` — model bake-offs and their recorded results
- `compose.yaml` and `app/Dockerfile` — reproducible container execution

## Third-party source

All supplied/current candidate repositories are visible under `repos/`. Exact remotes and pinned commits are recorded in `repos/README.md`.

The GitHub source snapshot intentionally includes only `repos/README.md`, not
nested third-party working trees. Clone those upstream repositories from their
recorded remotes/commits when needed.

## Experiment data, not source repositories

- `Crayotter/` — surviving Crayotter experiment data and raw media
- `FireRed-OpenStoryline/` — surviving FireRed experiment artifacts

Their fresh source checkouts are `repos/Crayotter/` and `repos/FireRed-OpenStoryline/`.

## Main result

The working system is `app/` — see `app/README.md` for the daily loop and
`app/VALIDATION.md` for what has been verified live. The July morning-routine
proof of concept that preceded it was removed on 2026-08-19; its results
survive in git history and in `STATUS_AND_ROADMAP.md`.

## Containers

See `CONTAINERS.md`. The app runs in Docker. OpenStoryline has an optional Compose profile. OpenTake remains a native host build because its Tauri desktop/GPU path does not benefit from being hidden inside a container.

## Local application

Start the first product-facing vertical slice with:

```bash
cd /home/saveas/Documents/video-editing
docker compose up --build app
```

Then open `http://127.0.0.1:8787`. The reviewed benchmark is available immediately. New folders can be indexed without fabricating semantics; live visual and speech adapters are the next integration gate.

## Repository data policy

Raw personal recordings, extracted frames, rendered videos, model weights,
papers, experiment caches, runtime projects, and nested third-party repositories
are intentionally excluded from Git. The original workstation retains them.
A clean clone contains the application, pipeline code, schemas, reports,
semantic review data, and small canonical JSON examples; provide your own media
folder to run a new ingest or restore the private benchmark media to reproduce
its render.
