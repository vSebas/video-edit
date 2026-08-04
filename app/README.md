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

The next adapter milestone is live visual invocation through the owned provider
boundary using deterministic scene ranges/keyframes, followed by timestamped
local ASR based on the useful
CutScript/WhisperX patterns. Those adapters must populate the existing analysis
and concept schemas; they do not replace `edit-plan.json`.
