# Vlog Studio — Local Video Editing Workbench

An AI editing assistant for daily personal vlogs: phone footage in, a
grounded story proposal, a rendered cut, and DaVinci-editable exports out.
It is an orchestration layer over evidence — every cut traces to verified
observations with timecodes — not a new timeline engine.

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
3. `POST /concepts` — grounded Spanish-first stories by deepseek-v4-pro
   (blind video-screening verdict), intent-first, with missing-shot and
   voiceover recommendations; capture time/GPS metadata informs chronology.
4. `POST /selection` + `POST /plan` — deterministic frame-exact compilation
   with word-snapped cut edges; unverified-only ranges are dropped.
5. `POST /render` and `POST /exports {include_proxies:true}` — review MP4,
   OTIO, DaVinci XMEML (+DNxHR-proxy variant, import verified in Resolve),
   and timeline-aligned `captions.srt`.
6. `POST /plan/revise` — natural-language re-cuts without re-analysis;
   prior revisions kept.

Project management: clone with shared analysis, reset (keep or wipe
analysis), delete; per-clip value scores (`GET /clip-scores`); clip removal
deletes the file from the laptop folder (phone originals unaffected).

Providers live in the ignored root `.env` (`DASHSCOPE_API_KEY`,
`GEMINI_API_KEY`, optional `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`,
`TWELVELABS_API_KEY`, `RUNPOD_API_KEY`); rclone's config is mounted from
the host. Model choices are evidence-based: see `bench/RESULTS.md`.

## Reaching it from other devices

`VIDEO_EDITING_BIND=0.0.0.0` opens the port beyond the laptop. Pair it with
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
