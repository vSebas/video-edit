# Application validation

**Verified:** 2026-09-01
**Image:** `video-editing-app:local` (faster-whisper + CUDA libs + rclone)

Newest checks are at the bottom.
Older sections describe the system as it was on their dates; the
morning-routine fixture and Phase 2A review machinery they reference were
removed on 2026-08-18, and the POC directory itself on 2026-08-19.

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

## Dual-review hardening (2026-08-19)

An external full-project review (Codex, gpt-5.6-sol at max reasoning) and an
internal five-dimension review were cross-checked, then every claim was put
to an adversarial verifier that tried to refute it against the code. Twelve
were confirmed, two came back partial. Fixes landed with regression tests
that fail against the pre-fix code (13 of 19 new tests fail when the fixes
are stashed):

- **Grounding gate.** `span_supported` tested only a cut's midpoint, so a
  span could run arbitrarily far past its evidence; it now requires approved
  ranges to cover ≥60% of the cut (0.5 s edge slack for word snapping).
  `revise_plan` never applied the gate at all — a revision could introduce
  wholly unobserved footage; it now takes `approved_ranges` like compilation.
- **Frame alignment.** Only event duration was quantized; the word-snapped
  source start stayed a float, so ffmpeg (raw seek) and the exporters
  (round-to-frame) could disagree by one frame. Both edges are on the grid.
- **Media type.** Nothing stopped a cited voiceover or photo from compiling
  into the video track. Cut spans are now video-only.
- **ASR run recency.** Captions, word snapping, and language detection each
  picked their run with `sorted(glob("asr-live-*"))[-1]` — a lexicographic
  sort of random UUIDs, so after re-analysis they used a stale run about half
  the time. One helper now selects by manifest `imported_at`.
- **Concurrency.** `project.json` read-modify-write pairs were unserialized
  across job threads and request handlers (lost analysis flags), `write_json`
  shared one predictable `.tmp` path per target (torn writes), and
  `_corroborate_speech_claims` wrote `reviews.json` without the lock human
  reviews take. All three are fixed; a widened-window threading test covers
  the first.
- **Media identity.** `sync_media` matched assets by filename and never
  re-probed, so replacing a clip's bytes under the same name kept the old
  evidence attached to new footage. Size changes now trigger a re-probe,
  re-hash, and a stale-analysis warning.
- **Spanish risk patterns.** The auto-approve blacklist was English-only on
  Spanish-first footage: "parece emocionada", "habla con", "la misma persona"
  and "marca" all sailed through the hedge filter. Each family now lists its
  Spanish forms.
- **UI.** `deleteProject` called `visibleProjects()`, removed during the
  fixture cleanup — deleting a vlog threw every time after the server-side
  delete had already succeeded.
- **Access.** Optional `VIDEO_EDITING_TOKEN` gate (header, one-time query
  parameter, or cookie) for use with `VIDEO_EDITING_BIND=0.0.0.0`. Unset
  keeps today's open behavior for localhost use.


After Codex was shown the verification results it withdrew its
documentation-contradiction claim (it had conflated the removed *fixture*
with the retained generalized scripts) and refined its concept-grounding
claim into a concrete rule, which is now implemented: `_sanitize_concepts`
receives the evidence the planner was given — approved and pending alike,
since citing a pending moment is legitimate — and drops any citation that
overlaps no observation at all, letting the existing beat and concept
minimums cascade. Its remaining objection stands: pipeline-critical render
and export scripts still live under a directory named `poc-morning-routine`.
(Resolved 2026-08-19: they moved to `app/pipeline/` and `app/schemas/`, and
the POC directory was deleted.)

40 tests pass.

### Known and deliberately not changed

- Jobs remain in-memory: a restart loses job status. Durable jobs and
  content-addressed artifacts are the architectural fix both reviews
  recommended and are not attempted here.
- The workspace and the full-scope rclone Drive token are still mounted
  read-write into the container.

## POC retirement and independent validation (2026-08-19)

`poc-morning-routine/` is gone. The code the app actually used moved to
`app/pipeline/` (`render_edit.py`, `export_timelines.py`, `validate_edit.py`)
and `app/schemas/`; `poc_root`, `VIDEO_EDITING_POC_ROOT`, and the `poc`
Compose service were removed with it. Every script now takes explicit
`--plan`, `--inventory`, and `--media-root` arguments instead of defaulting to
benchmark fixtures.

`validate_edit.py` had been calibrated to the July benchmark rather than to
the format, and the app never invoked it. Three checks encoded POC editorial
policy rather than invariants and were generalized: frame alignment compared
against a 1e-6 tolerance that plans storing six-decimal seconds can never
meet (1/30 s reads as 0.033333); title structure demanded the six-beat band
cover the whole timeline; source reuse was an error rather than a legitimate
cut. Geometry now accounts for reframe mode — "fit" scales any source into
the project frame, so a landscape clip is not a defect.

Verified live against `last-spring-quarter-class` (22 cuts, 78.2 s) after the
move: render and export jobs both completed through the relocated scripts,
the recompiled plan is frame-aligned at all 178 numeric values, and the
independent validator reports **VALID** on all ten checks — including
`frame_alignment`, which failed on the plan compiled before the quantization
fix and passes on the one compiled after it.

## Benchmark media relocation (2026-08-20)

The grounding benchmark's seven ground-truth clips were moved from
`Crayotter/crayotter-data/user_temp/` to `bench/media/`, and `Crayotter/`
was deleted. Verified by running `grounding_bench.clip_bytes` — the real
ffmpeg transcode path, not a path existence check — against every entry in
`bench/ground-truth.json` from the new location: 7 clips, 12 moments, all
resolved. sha256 of each copied clip matched the original before deletion.

The `--help` invocation documented in `bench/RESULTS.md` was run to confirm
the reproduction command resolves inside the container.

Also corrected: the capability endpoint advertised `"OTIO, DaVinci XMEML,
OpenTake"`, but the OpenTake exporter was removed with `poc-morning-routine`
on 2026-08-19. It now reports what it actually produces.

40 tests pass.

## Source context and VLM telemetry (2026-09-01)

- `source-context.v1` is schema-validated and stored as a derived, non-citable
  run. Regression tests cover whole-source versus windowed encoding, timestamp
  offsets and overlap de-duplication, event-to-evidence anchoring, the 2,500
  character planner cap, telemetry aggregation, and exclusion from both
  approved and pending evidence.
- The first live run analyzed 39/39 video assets in 39 Gemini calls: 110
  events, 50 relationships, and 109 anchored events. Its manifest records one
  retry, 106,523,293 uploaded request bytes, 171,824 prompt tokens, 14,781
  completion tokens, 888.348 aggregate call-seconds, and 1,778.666 unique
  source seconds.
- The owner preferred baseline concepts in the first A/B, so
  `use_source_context` remains false by default. Audit caveat: the retained
  A/B JSON includes the `source_context` treatment flag and stores its key
  beside the sets. The result is directional n=1 evidence, not a sealed blind
  verdict; the next judge packet must strip provenance and separate the key.
- VLM telemetry is persisted in visual/context run manifests and exposed by
  `GET /api/projects/{id}/analysis/telemetry`.

## OpenTake hybrid trial (2026-09-01)

- `opentake_adapter.py` placed the 22-cut video track over external MCP. Its
  hardened live readback verified 22/22 video clips, 2346/2346 frames, all 21
  nonzero source trims, selected media references, and one matching linked
  audio partner per video on the fields the adapter emits. The raw timeline
  response is persisted beside the project. The title track is deliberately
  outside this trial adapter.
- `opentake_cleanup.py` derived candidates from the newest local large-v3 word
  run and applied the reviewed batch through one `ripple_delete_ranges` call.
  The persisted readback is 2314 frames with 23 video and 23 audio pieces, 23
  unique non-null link groups, and identical video/audio link-group sets.
- Kill/restart recovery, export, and visual comparison closed the trial with
  the owner-ratified hybrid: OpenTake edits, the owned renderer produces final
  pixels, and Resolve remains the escape hatch. `TRIAL_OPENTAKE.md` contains
  the gate evidence.
- Both OpenTake scripts remain trial tooling: no automated tests exercise their
  MCP transport, and they have no retry/reconciliation, revision binding, or
  rollback across destructive operations. Productionization is a roadmap
  item, not a completed validation claim.

Current automated result in `video-editing-app:local`: **48 passed**. The only
warning is Starlette's deprecation notice for its current `httpx` TestClient
integration.
