# Container workflow

## Local application

Build and start the browser workbench:

```bash
cd /home/saveas/Documents/video-editing
docker compose up --build app
```

Open `http://127.0.0.1:8787`. The app is bound to localhost only. It mounts the
workspace so project metadata and generated outputs remain under `runtime/` on
the host. Override the port with `VIDEO_EDITING_APP_PORT`.

## Deterministic proof of concept

Build once:

```bash
cd /home/saveas/Documents/video-editing
docker compose build poc
```

Run validations:

```bash
docker compose run --rm poc scripts/validate_artifacts.py
docker compose run --rm poc scripts/validate_edit.py \
  --render artifacts/reference-edit/morning-routine-review.mp4
```

Rebuild the render and editable timelines:

```bash
docker compose run --rm poc scripts/render_reference_edit.py
docker compose run --rm poc scripts/export_timelines.py
docker compose run --rm poc scripts/export_opentake_project.py
```

Verified on 2026-07-17: the image built successfully, a fresh
`morning-routine-review-docker.mp4` rendered inside the container, all artifact
and technical render checks passed, and OTIO/XMEML round trips passed. The
container check also exposed and fixed Debian font-path and FFmpeg 5 audio-layout
portability differences.

The whole `/home/saveas/Documents/video-editing` directory is bind-mounted at `/workspace/video-editing`, so generated files remain visible on the host and source media paths resolve consistently. The container runs as UID/GID 1000 by default; override `LOCAL_UID` and `LOCAL_GID` if needed.

## FireRed-OpenStoryline

The optional service uses the already present `firered-openstoryline:local` image and host networking so its MCP server can bind to `127.0.0.1:8001` and its web interface to `127.0.0.1:7860`.

It is deliberately behind a Compose profile:

```bash
docker compose --profile openstoryline up -d openstoryline
```

Do not start it yet with the current tracked config: `repos/FireRed-OpenStoryline/config.toml` has blank LLM/VLM model, URL, and API-key fields. A separate untracked secret-bearing config should be mounted before service startup. The existing image was inspected read-only: it contains Python 3.11, FFmpeg 7.1, and the 117 MB TransNetV2 resource at `/app/.storyline/models/transnetv2-pytorch-weights.pth`; its baked config has model/base-URL defaults but no API keys. No credential has been invented or copied.

## Why OpenTake is not in Docker

OpenTake's pure Rust core can be built in a container, but its useful manual-editor path is a Tauri desktop application using host display, audio, FFmpeg, and GPU APIs. Containerizing that adds display/GPU/socket plumbing without improving isolation. We keep its visible source at `repos/OpenTake` and build/test it natively; the deterministic headless media pipeline is what Docker is best suited to here.
