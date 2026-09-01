# Vlog Studio — Local Video Editing Workbench

An AI editing assistant for daily personal vlogs: phone footage in, a
grounded story proposal, a rendered cut, and DaVinci-editable exports out.
It is an orchestration layer over evidence — every cut traces to verified
observations with timecodes — not a new timeline engine.

The ratified execution direction is hybrid: OpenTake is the editing surface,
the owned FFmpeg path renders final pixels, and Resolve remains the editable
export escape hatch. OpenTake placement and cleanup are currently bounded
trial scripts, not yet buttons in this workbench; timeline-to-plan sync is the
next required integration.

Run with Docker:

```bash
cd /home/saveas/Documents/video-editing
docker compose up --build -d app
```

Open <http://127.0.0.1:8787> (or from any device on the tailnet:
`http://pacman.tailf9616b.ts.net:8787`).

The UI is a three-step guided flow — Create (one button chains analysis and
story writing), Pick a story (evidence thumbnails, missing-shot and
voiceover advice), Watch/tweak/export (scene-by-scene workspace, revision
chat, clip value scores, recommendations) — with raw pipeline controls and
project management in an Advanced drawer.

## Walking-skeleton pipeline (daily vlogs)

The owned pipeline turns phone footage plus an optional note into a rendered
proposal and editable exports:

1. **Get footage in** (any of):
   - Google Drive **VlogInbox** (preferred, async): upload from the Drive
     app into `VlogInbox/<title>` with an optional `nota` text file as the
     prompt; the UI banners waiting folders and one click imports.
     Strictly read-only toward Drive.
   - Browser upload (`POST /api/uploads`, phone or laptop) with live
     progress on both ends; per-clip `POST /api/uploads/item` for iOS
     Shortcuts; `POST /api/projects/{id}/uploads` adds clips/voiceovers to
     an existing vlog. Reachable from anywhere via Tailscale.
   - A folder already on disk (picker shows `footage/` only).
2. `POST /analysis/visual` — audiovisual shot+moment evidence from
   gemini-3.6-flash (bench winner; audio-carrying segments; 6 parallel
   calls). `POST /analysis/speech` — faster-whisper large-v3 on CUDA,
   Spanish/English word timings; transcript-corroborated speech claims
   auto-approve.
   Optional `POST /analysis/context` builds the dormant, non-citable
   `source-context.v1` sidecar; `GET /analysis/telemetry` reports per-run VLM
   calls, retries, bytes, tokens, wall time, and de-overlapped source seconds.
3. `POST /concepts` — grounded Spanish-first stories by deepseek-v4-pro
   (blind video-screening verdict), intent-first, with missing-shot and
   voiceover recommendations; capture time/GPS metadata informs chronology.
   `{"use_source_context":true}` opts into the sidecar; default is false.
4. `POST /selection` + `POST /plan` — deterministic frame-exact compilation
   with word-snapped cut edges; unverified-only ranges are dropped.
5. `POST /render` and `POST /exports {include_proxies:true}` — review MP4,
   OTIO, DaVinci XMEML (+DNxHR-proxy variant, import verified in Resolve),
   and timeline-aligned `captions.srt`.
6. `POST /plan/revise` — natural-language re-cuts without re-analysis;
   prior revisions kept.
7. `POST /opentake/cleanup` + `/opentake/cleanup/apply` — Spanish dialogue
   cleanup: conservative filler/dead-air candidates from the local word
   transcript, reviewed as a checklist in the workbench, applied as ONE
   atomic ripple in OpenTake (fingerprint-bound so a changed timeline
   rejects the apply); then pulled into the plan via sync.
8. `POST /opentake/sync` + `/opentake/sync/apply` — pull the OpenTake
   timeline back into the plan: preview returns the diff (splits, trims,
   moves, deletions, all fail-closed within originally grounded material);
   apply installs it as a new revision through the same archive/log path as
   natural-language revisions. Host callers pass the `get_timeline` readback
   in the body (`opentake_adapter.py --sync [--apply-sync]` does this);
   a host-run app can fetch it itself via `OPENTAKE_MCP_URL`/`_TOKEN`.

## OpenTake hybrid bridge (trial tooling)

`app/scripts/opentake_adapter.py` places the flat video track of an existing
`edit-plan.v1` into the currently open scratch OpenTake project and verifies
the emitted geometry, source trims, media references, and linked audio.
`app/scripts/opentake_cleanup.py` lists or applies conservative Spanish
filler/dead-air candidates from the newest local large-v3 transcript.

These are evidence-producing trial tools, not the production adapter. They
use one MCP session with no retry/reconciliation, identify media by filename
stem, assume the trial's flat 30 fps/1x timeline, and do not make remove-then-
add transactional or roll it back. The cleanup candidate numbering is not
revision-bound. Run them only against a saved project you can recover. The
productionization item is tracked in `STATUS_AND_ROADMAP.md`.

Project management: clone with shared analysis, reset (keep or wipe
analysis), delete; per-clip value scores (`GET /clip-scores`); clip removal
deletes the file from the laptop folder (phone originals unaffected).

Provider credentials live in the ignored root `.env`. The standard Compose
service passes DashScope, Gemini, and OpenAI keys. `providers.py` contains a
native Anthropic client, but concept generation and plan revision bypass its
client factory, and `compose.yaml` does not forward its key; Anthropic is
therefore incomplete as an app provider. `TWELVELABS_API_KEY` is
benchmark-only, and no current code consumes `RUNPOD_API_KEY`. OpenTake trial
scripts read `OPENTAKE_MCP_TOKEN` directly from `.env`; rclone's config is
mounted from the host. Model choices are evidence-based: see
`bench/RESULTS.md`.

## Reaching it from other devices

The app container runs with host networking (required to reach OpenTake's
loopback-only MCP listener), which also means the container is no longer
network-isolated from other host-local services — a consequence to keep in
mind alongside the token gate. `VIDEO_EDITING_BIND=0.0.0.0` opens the port
beyond the laptop. Pair it with
`VIDEO_EDITING_TOKEN=<secret>`: unset, the API has no authentication at all,
and anyone who can route to the port can download footage, delete clips, and
spend API credit. With it set, open `http://<host>:8787/?token=<secret>` once
on the phone — the token is stored in a cookie afterwards — and send
`X-Vlog-Token: <secret>` from iOS Shortcuts. `/api/health` stays open for
container checks.

## Grounding guarantees

A cut compiles only when confirmed observations cover at least 60% of it
(`MIN_SUPPORTED_FRACTION`, with half a second of edge slack for word
snapping), and revisions pass the same gate rather than only checking the
asset's bounds. Cut ranges land on the frame grid at both edges so the
render and the NLE timeline cannot disagree by a frame. Only `media_type`
video enters the video track — a cited voiceover or photo is dropped rather
than compiled into it.
