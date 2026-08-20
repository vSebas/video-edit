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
docker compose build app
```

Validate a compiled plan and its render independently of the app:

```bash
docker compose run --rm app python pipeline/validate_edit.py \
  --plan   ../runtime/projects/<id>/plan/edit-plan.json \
  --inventory ../runtime/projects/<id>/plan/media-inventory.json \
  --media-root .. \
  --render ../runtime/projects/<id>/outputs/review.mp4 \
  --report ../runtime/projects/<id>/outputs/validation-report.json
```

The app itself invokes `pipeline/render_edit.py` and
`pipeline/export_timelines.py` as subprocesses; both take explicit
`--plan`, `--inventory`, and `--media-root` arguments.

The whole `/home/saveas/Documents/video-editing` directory is bind-mounted at its
host path, so generated files remain visible on the host and source media paths
resolve consistently for DaVinci Resolve. The container runs as UID/GID 1000 by
default; override `LOCAL_UID` and `LOCAL_GID` if needed.

## FireRed-OpenStoryline

The optional service uses the already present `firered-openstoryline:local`
image and host networking so its MCP server can bind to `127.0.0.1:8001` and
its web interface to `127.0.0.1:7860`.

It is deliberately behind a Compose profile. Choose one tracked provider
override; each maps values from the ignored root `.env` without embedding a
secret in Compose or `config.toml`:

```bash
docker compose -f compose.yaml -f compose.openstoryline.qwen.yaml \
  --profile openstoryline up -d openstoryline

docker compose -f compose.yaml -f compose.openstoryline.gemini-vlm.yaml \
  --profile openstoryline up -d --force-recreate openstoryline
```

Required `.env` names are documented in `.env.example`. The mounted
`repos/FireRed-OpenStoryline/config.toml` intentionally keeps blank model fields;
OpenStoryline then resolves the complete LLM/VLM triplets from environment
variables. The existing image contains Python 3.11, FFmpeg 7.1, the 117 MB
TransNetV2 weights, and the resource bundle. Keep the service bound to localhost
and do not print `docker compose config` without redacting its environment.

The Gemini override intentionally keeps Qwen as the tool-calling LLM and uses
Gemini only as the VLM. Gemini 3.5's OpenAI-compatible function calls require a
thought signature on subsequent turns; OpenStoryline's current ChatOpenAI agent
path drops that provider-specific field and receives HTTP 400 after its first
tool call. The split configuration isolates the visual backend while keeping
the tool-calling path operational.

## Why OpenTake is not in Docker

OpenTake's pure Rust core can be built in a container, but its useful manual-editor path is a Tauri desktop application using host display, audio, FFmpeg, and GPU APIs. Containerizing that adds display/GPU/socket plumbing without improving isolation. We keep its visible source at `repos/OpenTake` and build/test it natively; the deterministic headless media pipeline is what Docker is best suited to here.
