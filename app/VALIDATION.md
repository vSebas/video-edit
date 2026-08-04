# Application validation

**Verified:** 2026-07-18
**Image:** `video-editing-app:local`

## Automated checks

- Python source compilation: pass
- Browser JavaScript syntax check: pass
- Docker Compose configuration: pass
- API tests: 5 passed
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

New arbitrary projects stop at `awaiting_semantic_analysis` until a saved run is
imported or a future live adapter is invoked. Reviewed evidence can be finalized
but still cannot produce generic concepts or an edit plan. The current benchmark
scorecard intentionally blocks automatic promotion because two approved claims
conflict with verified footage and the provider runs used different shot
boundaries. Live provider invocation and the local timestamped-ASR runtime
remain unimplemented.
