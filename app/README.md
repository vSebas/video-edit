# Local Video Editing Workbench

This is the first product-facing vertical slice around the validated editing POC.
It is intentionally a project/orchestration layer, not a new timeline engine.

Current behavior:

- presents the reviewed morning-routine footage, semantic observations, three concepts, missing-shot advice, canonical edit plan, review video, and editable outputs;
- records concept selection without pretending that every concept already has a compiled edit plan;
- launches deterministic FFmpeg rendering and OTIO/XMEML export as background jobs;
- indexes arbitrary media folders inside the workspace using real ffprobe metadata, hashes, and generated thumbnails;
- marks new projects as `awaiting_semantic_analysis` until visual and/or speech adapters provide grounded content evidence;
- imports saved OpenStoryline sessions through a background job without copying
  credentials, restores original filenames, clamps shot ranges to ffprobe
  durations, flags risky claims, and validates a versioned normalized schema;
- shows imported Qwen and Gemini evidence side by side while keeping every
  provider caption pending review and unsafe for direct edit-plan compilation;
- supports approve, edit-and-approve, and reject decisions per observation,
  stored as a separate audit trail so raw provider evidence is never rewritten;
- finalizes completed reviews into separate versioned provider evidence sets
  and a scorecard, without silently merging providers or selecting a winner;
- cross-checks known benchmark failures, displays unresolved conflicts inline,
  and lets completed decisions be revised without losing the current viewport;
- reports OpenStoryline, local ASR, CutScript-source, renderer, and editable-export readiness.

The original workstation has the private morning-routine source media and can
play/render that fixture immediately. Raw media and generated renders are not
committed to Git, so a clean clone shows the retained plan/concept metadata but
requires user-supplied footage for media playback and new renders.

Run with Docker:

```bash
cd /home/saveas/Documents/video-editing
docker compose up --build app
```

Open <http://127.0.0.1:8787>.

The current finalized result is available in the browser under **Provider
scorecard** and through
`GET /api/projects/morning-routine/analysis/finalized`. The persisted current
artifact is
`runtime/projects/morning-routine/analysis/finalized/review-outcome.json`, with
content-addressed revisions under its `versions/` directory.

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
the host. Set `VIDEO_EDITING_BIND=0.0.0.0` to reach the app from other
devices. Model choices are evidence-based: see `bench/RESULTS.md`.
