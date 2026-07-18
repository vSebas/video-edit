# CutScript evaluation

**Checkout:** `repos/CutScript`
**Commit:** `e5c47e31b39a4178ff3e86a9bd69eb908b7b8bb5`
**License:** MIT
**Verdict:** useful component/reference, not the core planner or timeline.

## What it contributes

- WhisperX word-level transcription and alignment, with a standard Whisper fallback
- Optional pyannote speaker diarization
- Transcript-driven filler removal and short-clip suggestions through Ollama, OpenAI, or Claude
- Text-based destructive-range editing with undo/redo
- SRT/VTT/ASS captions, optional burn-in, DeepFilterNet audio cleanup, and FFmpeg export
- A practical Electron/React transcript-editor UI

## What it does not cover for this project

- The clip-suggestion endpoint sends transcript words and timestamps to the LLM; it does not inspect video frames.
- It does not fuse visual actions, composition, shot quality, speech, and non-speech audio into one grounded analysis.
- It does not propose missing shots or build a story from multiple raw media assets.
- Its project schema contains one `videoPath`, transcript data, and deleted ranges rather than a multi-track neutral edit plan.
- No OTIO, XMEML/FCPXML, DaVinci, CapCut, or OpenTake export is implemented.
- The UI currently requests a fixed 60-second clip suggestion.

## Checks performed

- Python source compilation: pass
- React/TypeScript production build: pass
- Upstream automated tests: none found
- Frontend dependency audit: four fixable advisories (one low, one moderate, two high)
- Heavy WhisperX/Torch/pyannote runtime installation and ASR execution: not run in this bounded inspection

## Recommended use

Keep the neutral `edit-plan.json` and existing deterministic renderer/exporters. Reuse or adapt CutScript's transcription result shape, transcript editor interaction, word-level captioning, and possibly its audio-cleanup path. Treat ASR as one evidence stream alongside visual observations, not as the entire creative planner.

The current morning-routine clips are mostly silent or ambient, so they are a poor benchmark for CutScript's strongest feature. A later dialogue-heavy sample should test transcription accuracy, word-aligned cuts, filler removal, captions, and A/V continuity.
