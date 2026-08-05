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

The owned live pipeline turns a media folder plus a prompt into a rendered
proposal and editable exports without OpenStoryline:

1. `POST /api/projects` — index a folder (ffprobe facts, hashes, thumbnails).
2. `POST /api/projects/{id}/analysis/visual` — deterministic ffmpeg shot
   detection and keyframes, described by the configured hosted VLM
   (`{"provider": "qwen"}` by default). Unflagged, confident captions are
   auto-approved under the audited `auto-live-v1` policy; risky claims stay
   pending without blocking planning.
3. `POST /api/projects/{id}/analysis/speech` — local faster-whisper ASR with
   word timings; audio never leaves the machine.
4. `POST /api/projects/{id}/concepts` — grounded concepts with hooks, beats,
   honest weaknesses, and concrete missing-shot advice.
5. `POST /api/projects/{id}/selection` then `POST /api/projects/{id}/plan` —
   deterministic compilation into a schema-validated `edit-plan.v1`.
6. `POST /api/projects/{id}/render` and `POST /api/projects/{id}/exports` —
   review MP4 plus OTIO and DaVinci-compatible XMEML from the same plan.
7. `POST /api/projects/{id}/plan/revise` — natural-language revision
   ("shorten the intro", "end on the scooter"); only the plan and render are
   rebuilt, media analysis stays cached, and prior plan revisions are kept
   under `plan/revisions/`.

The browser workbench exposes every step: a pipeline action bar, a
flagged-claims review panel (routine evidence is auto-approved), concept
cards with missing-shot advice, and a "Revise this edit" box on compiled
plans. The legacy Qwen/Gemini benchmark comparison and scorecard UI was
removed with the daily-vlog pivot.

Provider credentials come from the ignored root `.env`
(`DASHSCOPE_API_KEY`, `GEMINI_API_KEY`); they are never written into
artifacts. The legacy OpenStoryline import path remains only for the archived
benchmark evidence.
