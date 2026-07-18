# Local Video Editing Workbench

This is the first product-facing vertical slice around the validated editing POC.
It is intentionally a project/orchestration layer, not a new timeline engine.

Current behavior:

- presents the reviewed morning-routine footage, semantic observations, three concepts, missing-shot advice, canonical edit plan, review video, and editable outputs;
- records concept selection without pretending that every concept already has a compiled edit plan;
- launches deterministic FFmpeg rendering and OTIO/XMEML export as background jobs;
- indexes arbitrary media folders inside the workspace using real ffprobe metadata, hashes, and generated thumbnails;
- marks new projects as `awaiting_semantic_analysis` until visual and/or speech adapters provide grounded content evidence;
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

The next adapter milestone is live visual analysis through OpenStoryline (after a
real LLM/VLM configuration is supplied) and timestamped local ASR based on the
useful CutScript/WhisperX patterns. Those adapters must populate the existing
analysis and concept schemas; they do not replace `edit-plan.json`.
