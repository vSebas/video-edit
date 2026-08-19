# Application validation

**Verified:** 2026-08-19
**Image:** `video-editing-app:local` (faster-whisper + CUDA libs + rclone)

Newest checks are at the bottom (Capture-loop checks, 2026-08-18/19).
Older sections describe the system as it was on their dates; the
morning-routine fixture and Phase 2A review machinery they reference were
removed on 2026-08-18.

## Perception-stack checks (2026-08-16/17)

- GeminiClient (native API) live-verified: audiovisual shot description with
  sub-moments on real footage; `input_mode: video+audio`.
- faster-whisper large-v3 on CUDA verified inside the server container after
  bundling cublas/cudnn (real clip: 8 Spanish segments, lang p=0.99); the
  earlier silent degradation to small/cpu (missing libcublas at encode time)
  is covered by the fallback chain plus baked-in libraries.
- Class-vlog re-analysis: 806 audiovisual observations + 42 Spanish speech
  segments; corroborate-v1 auto-approved 29 transcript-confirmed speech
  mentions; concept generation produced Spanish titles/hooks with earned
  durations (93 s / 10 scenes) honoring the encoded editorial preferences.
- Full bake-off methodology and results: `bench/RESULTS.md`,
  `bench/planner/` (blind brackets), 18 API/unit tests passing.

## Moment-level selection checks (2026-08-05)

The visual adapter now sends each shot as a downscaled native-video segment
(≈1.2k video tokens ≈ $0.0005/shot on `qwen3.7-plus`) and returns sub-shot
moments; keyframes remain the fallback. Verified against the independently
checked benchmark ground truth:

- IMG_0997 wave (verified 1.45–3.3 s): best moment reported 2.0–3.0 s —
  "makes direct eye contact and waves at the camera". PASS.
- IMG_0996 shoe reveal (verified ~8.5–11.3 s): best moment 10.9–12.9 s —
  "lifts the sneaker into frame". PASS.
- Evidence density: 24 shot captions + 67 sub-moments (v1: 25 coarse blocks);
  84 auto-approved, 7 pending.

Cut boundaries now snap to spoken-word edges (±0.12 s padding) using the ASR
word timings at compile and revision time, and faster-whisper attempts
large-v3 on CUDA before falling back to small/int8 CPU. 18 tests pass.

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

## Revision live check (2026-08-04)

Instruction "drop the sideways wake-up shot, tighten to ~25s, end on the
scooter" against the compiled 36.2 s plan produced revision 2: the sideways
range was removed (the upright tail of the same clip was kept), a duplicate
cut was dropped, duration became 31.2 s, the last cut is the scooter exit,
and the review video re-rendered. The prior plan is retained under
`plan/revisions/` with an instruction log.

## DaVinci Resolve import check (2026-08-05) — PASS

DaVinci Resolve 21.0.3 (free, Linux) was installed locally and the generated
XMEML was imported and verified in the real application:

- Timeline "Daily Skeleton A": 6 of 6 video clips and 6 of 6 audio clips in
  exact plan order; every clip duration frame-accurate; V1 total 937 of 937
  frames; 1080x1920 @ 30 fps. Verdict PASS via the in-app console checker
  (`app/scripts/resolve_verify_current.py`).
- Platform findings baked into the exporter: the free Linux edition does not
  decode H.264 video (original phone clips import audio-only), so
  `POST /exports {"include_proxies": true}` now transcodes DNxHR LB / 48 kHz
  PCM proxies and writes `timeline-davinci-proxies.xml` referencing them with
  matching declared audio characteristics. The XMEML now also carries the
  `<!DOCTYPE xmeml>` header. This is an edition licensing limit, not a
  version issue; Resolve Studio or another OS decodes H.264 natively.
- Free-edition scripting is console-only (external scripting is
  Studio-gated); helper scripts live under `app/scripts/`.

## Deliberately incomplete

- Sideways-stored clips without rotation metadata render unrotated; the
  compiler does not yet set `rotation_degrees` automatically.
- Revision re-renders the video but does not automatically rebuild the
  OTIO/XMEML exports; use the export step after revising.
- Revision duration targets are honored approximately (asked ~25 s, got
  31.2 s).
- (resolved 2026-08-18: the archived fixture and scorecard were removed
  entirely.)

## Capture-loop checks (2026-08-18/19)

- Browser upload endpoint: E2E verified (file → project created/indexed);
  live receiver-side progress verified mid-flight with a rate-limited
  transfer; failed creation cleans its folder.
- Per-item Shortcut endpoint: simulated 3-request loop → create then
  append (1→2→3 clips).
- Drive VlogInbox: additive test folder uploaded, banner listed it, import
  synced clips + nota-as-prompt and created the project; Drive contents
  verified untouched afterwards (no delete code path exists).
- Clone (0.15 s, analysis shared), both reset modes, delete, clip add and
  remove-with-file-deletion, still-image frames: all verified live against
  throwaway projects.
- Fixture removal: 14 tests pass; workspace lists only real projects.
