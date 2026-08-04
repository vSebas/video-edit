# Application validation

**Verified:** 2026-08-04
**Image:** `video-editing-app:local` (rebuilt with faster-whisper)

## Walking-skeleton live checks (2026-08-04)

Run as a fresh project over the seven private benchmark clips:

- Live Qwen visual analysis (`qwen3.7-plus`, owned adapter): 25 observations,
  20 auto-approved by policy, 3 risk-flagged, 0 schema failures.
- Local faster-whisper ASR (small/int8/CPU): 25 low-confidence segments all
  correctly held below the auto-approve gate (footage has no real dialogue).
- Concept generation: 2 grounded concepts, each with prioritized missing-shot
  advice and edit-around fallbacks; all cited ranges survived clamping.
- Deterministic plan compilation: 15 events, schema-valid `edit-plan.v1`.
- Render: 36.236 s 1080x1920 H.264/AAC review video.
- Exports: OTIO and DaVinci XMEML round-trip validation report all-pass
  (7 video clips, 7 audio clips, 1 title marker).

## Automated checks

- Python source compilation: pass
- Browser JavaScript syntax check: pass
- Docker Compose configuration: pass
- API tests: 14 passed (5 legacy + 9 walking-skeleton unit tests)
  - reviewed fixture exposes seven assets, three concepts, a 31-second plan, and a render;
  - concept selection distinguishes compiled from uncompiled plans;
  - a generated media folder is hashed, probed, and thumbnailed without fabricated semantics.
  - a saved provider run is mapped to the original asset, range-clamped,
    risk-flagged, schema-validated, persisted without its credential-bearing
    session state, and exposed as review-only evidence; a reviewed correction
    is overlaid without mutating the normalized provider record.
  - completed reviews produce a schema-validated, content-addressed evidence
    revision with accepted/rejected sets and reviewed wording;
  - an approved material hallucination remains approved in the audit history
    but is surfaced as a conflict and blocked from planning eligibility.

## Live container checks

- `GET /api/health`: pass
- `GET /api/status`: pass
- `GET /api/projects/morning-routine`: pass
- Browser page and static assets: pass
- Headless Chromium layout/asset inspection: pass
- Headless Chromium Qwen/Gemini comparison inspection: pass
- Headless Chromium provider-scorecard inspection: pass; both scorecards,
  timings, the no-winner verdict, and two verified-footage conflict links render.
- Rendered DOM inspection: pass; 43 completed approvals expose revision
  controls and exactly two evidence cards carry inline conflict warnings.
- Isolated headless Chromium review interaction: pass; approving a caption kept
  the same evidence card at the same viewport position and preserved both
  expanded filename sections without reloading the project.
- Background render job: pass
- Background editable-export job: pass

The app-rendered `runtime/projects/morning-routine/outputs/review.mp4` passed the
existing independent edit validator:

- H.264/AAC, 1080×1920, 30 fps, exactly 31.000 seconds
- 48 kHz stereo, decoded maximum −1.4 dBFS
- nine valid source trims, no repeated source frames
- contiguous video timeline, linked audio, six title beats

The app-generated editable copies independently parse as:

- OTIO: 31.000 seconds, two tracks, six markers
- DaVinci XMEML: 930 frames, nine video clips, nine audio clips

## Deliberately incomplete

- The browser UI has no controls yet for the new pipeline endpoints
  (visual/speech analysis, concept generation, plan compilation); the walking
  skeleton is currently API-driven.
- Sideways-stored clips without rotation metadata render unrotated; the
  compiler does not yet set `rotation_degrees` automatically.
- DaVinci Resolve import of the generated XMEML is unverified (Resolve is not
  installed; the installer download is registration-gated and user-owned).
- Prompt-driven plan revision is not implemented yet.
- The legacy benchmark scorecard still intentionally blocks automatic
  promotion of its two verified-footage conflicts; this does not affect new
  projects, which use the auto-approval policy instead.
