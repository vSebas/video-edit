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
- Known provenance wording bug: normalized visual runs always warn that
  captions came from deterministic keyframes, including records whose raw
  `input_mode` is native video with audio. Raw records remain authoritative;
  the warning must be made mode-aware in a behavior-change patch.

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
  pixels, and Resolve remains the escape hatch. `docs/history/TRIAL_OPENTAKE.md` contains
  the gate evidence.
- Both OpenTake scripts remain trial tooling: no automated tests exercise their
  MCP transport, and they have no retry/reconciliation, revision binding, or
  rollback across destructive operations. Productionization is a roadmap
  item, not a completed validation claim.
- Provider wiring caveat: `providers.py` includes a native Anthropic client,
  but the concept and revision service paths still construct the
  OpenAI-compatible client directly, and Compose does not forward the key.
  Anthropic has not been validated as an app planning provider.

Current automated result in `video-editing-app:local`: **48 passed**. The only
warning is Starlette's deprecation notice for its current `httpx` TestClient
integration.

## Provenance wording fix (2026-09-01)

The normalized visual-run warning always claimed captions came from
"deterministic shot keyframes", even for audio-carrying native-video runs.
`provenance_note()` now derives the wording from the recorded `input_mode`s;
three regression tests cover audio, keyframe-fallback, and mixed runs.
51 tests pass.

## Timeline→plan sync (2026-09-01)

The hybrid's keystone landed: `opentake_sync.py` (pure, fail-closed
translation of a timeline readback into a candidate plan, with split
attribution resolved clip-id-first) plus preview/apply endpoints that run
the candidate through the full edit-plan validator and install it through
the standard revision archive. Golden tests reproduce the real trial edits:
the untouched placement round-trips with zero semantic diff, and the
dead-air cleanup reconstructs as one split (v18__a/__b), 32 source frames
gone, 2314 frames total. Apply carries replay protection (candidate is
revision-bound and single-use). The adapter persists opentake-bridge.v1 on
every verified placement and gained `--sync/--apply-sync`. 68 tests pass.

## P1–P6 mandate + follow-on batch (2026-09-01, one session)

Everything below is verified by tests that measure the actual output —
pixels sampled from renders, band-filtered audio levels, structure parsed
back out of exports — not by asserting that code ran.

- **P1 dialogue cleanup**: candidates computed from the word transcript
  mapped through the live clip layout; apply is bound to a timeline
  fingerprint (a mutated timeline is refused with "list them again"). The
  stale-apply test caught a real ordering flaw (MCP client built before
  the fingerprint check).
- **P2 B-roll**: sync accepts one extra OpenTake video track (clip-id-first
  identity via bridge `broll_events`, linked audio ignored with a diff
  note); renderer overlay verified by sampling center pixels before /
  during / after the overlay window on synthetic red/blue media; OTIO and
  XMEML carry V2; placement fills an existing second track (the MCP cannot
  create tracks — probed live).
- **P3**: OpenTake clip volume round-trips (probed live including explicit
  0.0 for mute; unchanged levels preserve the stored plan value). J-cut
  render verified by frequency: the next scene's 1200 Hz tone dominates a
  band-filtered window while the picture is still the previous scene's
  pixels. Gaps render as black/silence instead of concat-squeezing (latent
  bug surfaced by the new contiguity check and fixed).
- **P4**: LLM chooses ONE op from a closed set; appliers are bounds-checked
  pure functions (ripple carries B-roll, titles, and voiceovers; J/L plans
  refuse structural edits). Live: deepseek mapped "baja el volumen de la
  última escena a -10dB" to set_volume v22_closing on the real project.
- **P5**: burned captions verified by peak-brightness sampling of the lower
  third vs a caption-less render; 12 ms edge fades present in the render
  command; `fill` reframing verified by rendering a half-red/half-blue
  source at center_x 0 and 1.
- **P6**: job history survives a restart (active jobs reload as
  "interrupted"); duplicate active submits return the running job; an
  unchanged plan returns the cached render — all three also proven live
  against the running app.
- **Voiceover**: ducking verified by band-filtered levels — the 1200 Hz
  voiceover is audible only inside its window, and the 300 Hz bed is
  measurably ducked under it and recovered after.
- **Rotation detection**: parser rejects non-cardinal answers; compiler
  application (with manual_review flagged) covered by compile tests.
- **Concept trust**: caption cross-check flags fabricated in-range claims,
  passes matching ones, and gives short abstract claims the benefit of the
  doubt.
- **Artifact identity v1**: content keys are order-stable and sensitive to
  media/model/prompt changes; a matching key returns the existing run.
- **Access control**: verified live — 401 without the token, 200 with it,
  host rclone config read-only from inside the container.

Current automated result in `video-editing-app:local`: **126 passed**, plus a same-day adversarial cross-review whose 2 blockers and 20 majors were fixed and re-tested.

## Reference Style Intelligence — MVP #1 slice (2026-09-01)

- **Deterministic extraction** proven on synthetic media: a generated
  4-shot color reference (3s shots, tone on the first half only) yields
  shot_count 4, median ≈3s, ≈15 cuts/min, speech_ratio ≈0.5; a missing
  file raises `StyleError` (fail-closed).
- **Semantic whitelist**: VLM answers outside the hook/shape enums are
  dropped, not stored — an invented narrative label disappears from the
  observation.
- **Aggregation**: single-reference templates cap confidence at 0.55;
  requirements (needs_payoff, needs_broll, dialogue_density) derive from
  the observed grammar, not the model's self-reported confidence.
- **Matching is deterministic** (no model call): payoff-less concepts
  score 0.2 on payoff_fit with a Spanish "missing" reason; too few
  evidence moments degrade pacing_feasibility; no spare footage degrades
  broll_feasibility. Score weights 0.35/0.25/0.25/0.15.
- **Style-conditioned generation**: `style_id` on the concepts request
  resolves the stored template and appends `style_guidance` (grammar-only,
  with the "never invent content" grounding reminder) to the planner
  guidance; concept `editorial` metadata is schema-validated and
  sanitizer-whitelisted.
- **Live**: `/api/styles`, `/api/styles/references` return 200 with the
  token against the rebuilt container; analysis endpoint untested live —
  waiting on a real reference video in `references/` (gitignored).

Suite after this slice: **155 passed** in `video-editing-app:local`.

## Style slice — Codex adversarial review triage (2026-09-02)

18 findings (1 blocker, 12 major, 5 minor); all fixed except two
consciously narrowed (see below). Proof of the blocker on the user's real
reference: the windowed VLM segmenter reported 2.07s median shot /
23.5 cuts-min; the new raw scene-cut measurer reports 0.72s / 55 —
the reference is a fast-cut video and the old numbers were fabricated
by the segmenter's 1.5s merge floor. Fixes, each with a test:

- dedicated `_raw_shots` (no min-merge, no 8s split, fail-closed);
  montage keeps 0.5s cuts, a 20s take is ONE shot
- `speech_ratio` requires an audio stream and a clean ffmpeg exit —
  None (unknown) otherwise, never a guess; muted video → None
- tone is a 24-word controlled vocabulary end to end (VLM whitelist,
  planner editorial whitelist, schema enum) — closes the
  reference-video → planner-prompt injection channel; the style name is
  stripped and the guidance block is framed as untrusted data
- fail-closed numerics: confidence "high"/NaN → 0.3, explicit 0 stays 0
- schemas are enforcement points: validated on write AND read (invalid
  stored styles are skipped with a warning), enums/ranges tightened in
  all three style schemas and the concepts `editorial` block
- consensus aggregation: medoid narrative shape (majority beats longest),
  deterministic mode tie-break, frequency-ordered tones, disagreement
  multiplies confidence down (proven: 2 agreeing + 1 outlier < 2 agreeing)
- matching: order-aware narrative fit (reversed arc scores lower),
  payoff full credit needs the declared shape to actually end in one,
  pacing counts DISTINCT moments (a range repeated 20× is one), spare
  B-roll excludes cutaway-used assets, tone joins the score (renormalized
  weights .30/.25/.20/.15/.10), min_distinct_shots surfaces in "missing"
- symlink containment on reference filenames (escape → error, traversal
  → neutralized to basename)
- concept-job fingerprint hashes ALL result-affecting options; style-job
  fingerprint includes content identity (size+mtime), name, prompt version
- style card rendering escapes stored-template values

Consciously narrowed: (a) payoff verification still trusts the concept's
declared narrative shape (cross-checking beats against footage semantics
is a later tier — the declared-flag-only path now caps at 0.6); (b)
`speech_ratio` remains an audio-activity upper bound on dialogue, not VAD
— renamed in docstring, not schema. Suite: **166 passed**.

CORRECTION (Codex verification, 2026-09-02): the paragraph above
overstated the triage — Codex's follow-up rated 9 of 18 findings fully
fixed and 9 partial, and surfaced 8 new majors + 8 minors introduced or
exposed by the fixes. Second round, all fixed with tests (suite 166→178):

- fail-closed numerics tightened again: booleans and out-of-range values
  are invalid, never clamped (True must not become confidence 1.0)
- consensus honesty: an empty-shape observation is disagreement (halves
  confidence, no more bypassing the single-reference cap); 1-1
  categorical ties resolve to unknown, not lexicographically
- matching: order now dominates narrative fit (a reversed arc scores
  <0.5 and gets no "coincide" reason); payoff cross-checks the declared
  story position against the style's measured one; a measured ZERO-cut
  (long-take) style is valid data, not missing (pacing 1.0, its own
  guidance line, UI shows "toma continua"); concepts predating
  `editorial` score unknown (0.5) instead of being falsely accused of
  lacking a resolution; min_distinct_shots capped at 24 and compared
  against distinct MOMENTS (same units on both sides)
- matches carry `template_confidence` (fit and trust are separate axes,
  both shown); guidance flags low-confidence templates and no longer
  interpolates the reference-derived NAME into the planner prompt at all
- style ids are deterministic (sha1 of sources+name): re-analyzing a
  reference REPLACES its style; DELETE /api/styles/{id} + UI button
- incompatible stored templates surface as visible stubs ("quedó
  incompatible — re-analiza") instead of silently vanishing
- style-match.v1 is now schema-validated too (all three schemas enforced)
- concept-job fingerprints include the project-state token (prompt,
  evidence approvals, latest source context) — approving evidence and
  resubmitting no longer returns the stale running job
- concepts prompt now lists the tone vocabulary (writer tones were being
  silently stripped, disabling tone matching entirely)
- probe hardening: r_frame_rate fallback, N/A duration → StyleError;
  scene-boundary floor cut 150ms → 50ms (a 4-frame insert survives)

Still open by choice: dialogue_density stays an audio-activity proxy
(VAD is a later tier); size+mtime style fingerprints are best-effort
(the observation's sha256 records what was actually analyzed); template
confidence informs the planner and UI but does not scale match scores
(fit and trust stay separate axes).

## v2 measurement expansion + capability slice (2026-09-02, overnight)

- **Styled titles**: edit-plan events gain optional `text_style`
  {font: sans|handwritten|clean|display, size 24-140, position
  top|center|lower}; three OFL fonts bundled in `app/fonts/` (Caveat,
  Inter, Montserrat — the host also has them for OpenTake's preview);
  `set_title` accepts the new args with fail-closed validation; PROVEN BY
  PIXELS: a centered handwritten title lights up the middle band of the
  frame that a top title leaves dark.
- **Audio beat grid (v2 §18.2/§21)**: deterministic onset-autocorrelation
  tempo estimate + beat phase + median cut-to-beat offset, numpy-only.
  Proven on a synthetic 120 BPM click track (measured 120±6, cuts on the
  0.5s grid → cut_to_beat ≤0.1); a silent video claims nothing (None).
  The real reference measures 68 BPM, cuts 0.19s off-beat →
  `cuts_on_beat: false` — its cuts follow the voiceover, not music.
- **Per-field evidence tiers at the consumption boundary**: templates
  carry `grammar_tiers` (measured vs semantic per grammar field);
  matches carry `template_confidence`; low-confidence templates are
  flagged in the planner guidance itself.
- **style-application.v1 (light)**: a styled concepts document records
  style_id, template confidence, analyzers, and the exact guidance block
  — baseline and styled runs are distinguishable artifacts now.
- **VlogInbox live status**: per-folder clip count/size, a
  receiving/ready signal (file ModTime within 120s), visibility-aware
  polling, and REAL import progress (copied MB vs expected) replacing
  the fake stage list. Verified live against the two folders actually
  waiting in the user's Drive inbox.

Suite: **183 passed**.

## Design-level review of Reference Style Intelligence (2026-09-02)

Codex (same session, asked explicitly to be willing to disapprove):
**APPROVE WITH RESERVATIONS.** Approved: concept×style as the ranking
unit, measurement-first extraction, deterministic matching for the MVP,
the social/trend gate. Reservations adopted the same day: the match
score is presented as "Compatibilidad estimada" (heuristic, not a
probability); the roadmap's run-#1 protocol now splits matching validity
from application validity with a fixed-concept styled arm; success is
defined as measurable change in the RENDERED cut (compiler-level
style-application + re-measured grammar), not as prompt provenance.
The identified architectural risk — a self-confirming label loop where
model-written labels agree with model-written labels while the pixels
barely change — is recorded in the v2 doc header as the thing run #1
must disprove.

## Design-review reservations implemented (2026-09-02, second pass)

The APPROVE-WITH-RESERVATIONS conditions that were engineering work (not
docs) are now built, each with tests (suite 183→189):

- **Compiler binding**: style-application.v1 carries a resolved contract
  ({targets, owners, unsupported}); `compile_plan` passes the measured
  targets into `build_plan`, where the reference's B-roll ratio binds
  the cutaway layout (a 0.7-ratio style may hold a cutaway to ~70% of
  its beat window vs the legacy 4.0s cap; a sparse style shrinks to
  2.5s) — always bounded by sanitizer-approved cutaways, no target
  invents footage. Proven: same spans, high target → >1s more coverage;
  sparse target → ≤2.6s; no targets → byte-identical legacy plans.
- **Achieved grammar on the plan**: styled plans record
  `style_application.achieved_plan` (visual cuts/min incl. B-roll
  edges, B-roll ratio) next to the targets.
- **Pixel loop**: a fresh render of a styled plan re-measures the OUTPUT
  with the same instruments used on references
  (`measure_rendered_grammar`) and stores achieved-vs-target in the
  render state + response.
- **Coverage honesty**: matches report `coverage` (share of scoring
  weight from known dimensions) so a renormalized score can't pose as
  fully observed; UI already labels the score "Compatibilidad estimada"
  and warns when concepts were style-conditioned
  (`concepts_conditioned_by`).

Deliberately NOT auto-edited: pacing beyond B-roll (splitting primary
cuts adds no visual change; shortening them amputates story) stays
planner-owned; beat quantization and music remain recorded as
unsupported until the plan can express music.

## Third review round — verification findings fixed (2026-09-02)

Codex's verification of the reservation work (verdict: still APPROVE WITH
RESERVATIONS, "genuine vertical slice" but partially discharged) produced
8 majors + 3 minors. All implemented, suite 190→195:

- **B-roll is now a GLOBAL coverage budget** (target × timeline) spent
  across approved cutaways — a 0.0 target emits none, 0.1 yields ~1.6s
  on a 16s cut, and unmet budget is recorded as
  `broll_shortfall_seconds`, never approximated with a floor.
- **The contract stopped overstating**: only broll_ratio claims compiler
  ownership (pacing is planner guidance until actually bound); each
  target carries its epistemic tier; the ENTIRE resolved contract
  (style_id, confidence, owners, unsupported) travels into the plan.
- **Achieved grammar is correct and never stale**: the t=0 phantom-cut
  precedence bug fixed; primary boundaries hidden under B-roll excluded;
  `compute_achieved_plan` recomputes on every mutation path (atomic ops,
  OpenTake sync, LLM revision — which now also PRESERVES the style block
  it used to drop).
- **A/B integrity enforced, not advised**: `style_mode="none"` guarantees
  a baseline; explicit style_id over a style-conditioned concept is
  REFUSED unless `allow_conditioned=true`; provenance is per concept
  (kept/mixed sets report correctly), and matches carry
  `concept_conditioned_by`.
- **Coverage measures observation, not fallbacks**: a pre-editorial
  concept reports 0.35, not 0.9.
- **Combine requires independence**: observations deduplicated by source
  sha256, duplicate style ids rejected, stored observations
  schema-validated before aggregation.
- **Aggregate confidence = weakest-axis agreement** across shape, pacing
  numerics, and categorical fields — identical labels with different
  pacing no longer average up to "agreeing".
- Cached renders return the stored pixel measurement and re-measure when
  the analyzer version changes (without re-rendering); the concepts
  document is validated AS PERSISTED (after provenance/application
  attach); match schema requires coverage and template_confidence.

## Fourth round (2026-09-02): remaining partials closed

Codex's re-verification (6/11 discharged) left 5 majors + 3 minors; all
implemented (suite 195→197):

- lineage resolves from the SELECTED concept (kept style-A concept no
  longer compiles under document-level style B); legacy concepts in a
  styled document fail closed as conditioned
- agreement = MINIMUM over supported axes (one clashing axis can't be
  averaged away — proven: identical labels + opposite pacing score well
  below true agreement), and <4 supported axes cap agreement at 0.75
- broll_shortfall_seconds is derived state owned by
  refresh_style_application — recomputed on every mutation
- render measurement: dedicated STYLE_MEASURE_VERSION, success-only
  stamping, logged failures with measurement_error, retry until a valid
  current-version result exists
- renders serialized per project (captioned/uncaptioned share artifacts)
- achieved cuts/min counts clip→gap→clip transitions (both edges)
- B-roll budget distributed by proportional per-beat quota, not
  first-come order
- combine_styles reports included/excluded references WITH reasons and
  only accepts observations its template actually recorded

## Fifth round (2026-09-02): closing the last partials

- agreement now spans ALL observed axes (adds tone-set Jaccard, payoff
  position, speech ratio, BPM, cut-to-beat) — still minimum-over-axes
- shortfall computed from summed B-roll durations (the rounded ratio
  could hide a real deficit near target)
- quota floor: a fair share under the 0.8s atomic minimum lifts to the
  floor while the global budget holds the total (a 6s budget over ten
  beats now places B-roll instead of none)
- renders run UNLOCKED to per-invocation temp files and promote
  atomically (artifact+state paired) under the per-project lock — the
  worker-starvation risk of holding the lock through ffmpeg is gone

## Sixth round (2026-09-02): the last two code items

- leftover B-roll budget now redistributes in a second pass to ANY
  cutaway with unused source material and window room — a truncated
  clip extends beyond its quota when budget remains, bounded by the
  70%-of-window honesty cap; the shortfall reports exactly what the
  windows genuinely cannot hold
- render results are IMMUTABLE keyed artifacts (review.<key>.mp4,
  pruned to the newest 4): a job's recorded output URL always resolves
  to its own pixels even after a concurrent captioned/plain render
  replaces review.mp4 (which remains the stable "latest" via hardlink)

## Final verdict (2026-09-02): APPROVED

After six adversarial rounds (implementation ×2, design, verification
×3 — ~450k Codex tokens total), the closing check discharged the last
two items with no new defects. Codex's verdict verbatim: "APPROVED.
Remaining style-intelligence reservations are now ONLY run-#1-gated:
real-choice weight calibration, the blind A/B execution, and
multi-reference validation using genuinely independent videos."
The subsystem is engineering-complete until real footage arrives.
Suite: 198 passed.

## Multi-reference style — LIVE (2026-09-02)

The owner added four real references (all Spanish short-form). Each
analyzed clean with genuinely distinct measured grammars (0.6-1.27s
median shot, 26.6-82.9 cuts/min, 15-90% B-roll, 68-136 BPM, none
beat-cut). Combined into `style-675776bf` ("mi estilo (5 referencias)"):
5/5 included, 0 excluded, consensus grammar hook→setup→explainer→
montage→reflection, 1.18s median / 42.9 cpm / 65% B-roll / voiceover /
minimal captions / personal-casual-educational tone. Confidence 0.69 —
above the 0.55 single-reference cap because five independent sources
agree on the core, below full trust because pacing genuinely disagrees:
the weakest-axis penalty working on real variance, exactly as designed.
Live matches show every honesty signal at once: coverage 0.35
(pre-editorial concepts), template confidence beside the score, and the
"regenera las ideas" hint. Run #1's style arm is now fully unblocked.

## Trust-system design review (2026-09-02): REVISE

Codex reviewed the afirmaciones system's purpose and implementation.
Purpose ratified; implementation verdict REVISE with a BLOCKER:
**authorization is by TIME RANGE, not by CLAIM** — a pending claim can
reach a rendered title if any approved observation overlaps its seconds.
CORRECTION to earlier entries: the pipeline should NOT be described as
fail-closed until claim-level lineage gates both pixels and editorial
language. Fix rounds follow; UI copy softened same day.

## Trust-system fixes, round one (2026-09-02)

Against the REVISE verdict (suite 198→203):

- **Claim-level grounding at compile AND revise**: besides the pixel/time
  gate, every citation's stated content must now be supported by APPROVED
  captions overlapping its range (`claim_supported`); `needs_review`
  citations never compile unconfirmed; unsupported cutaways drop. Proven:
  "celebra que ganó la carrera" over an approved walking-scene range is
  dropped while the honest claim passes.
- **Titles are gated as rendered language**: a title asserting what no
  approved observation supports refuses to compile with an actionable
  Spanish error (fabricated-championship title test).
- **Corroboration scoped to speech-presence**: captions reporting speech
  CONTENT ("explica que…", quotes) are excluded from ASR auto-approval,
  and ≥3 overlapping recognized words are required (one stray word is
  noise).
- **Risk lexicon broadened** (es+en): quiere/intenta/disfruta/orgulloso/
  se siente; explica/pregunta/responde/cuenta/susurra/voz; la misma
  mujer/continúa/regresa/más tarde; NEW outcome/causality category
  (ganó/perdió/renunció/porque/logró).
- **UI copy honest**: "el corte nunca las usa sin confirmar: si una
  historia depende de una, la escena se omite o se te pedirá confirmarla."

Accepted limitations, documented deliberately: the semantic gate uses the
lexical-overlap heuristic as a fail-CLOSED authorizer (errs toward
dropping scenes; shared-noun false-passes remain possible until claim-ID
lineage from writer citations exists); auto-approval still keys on
self-reported confidence — a user-ratified product policy (post-Phase-2A)
whose blast radius the new claim/title gates now bound.

## Trust-system round two (2026-09-02): claim-ID lineage

The user directed: "if this is a design fault revise it and do anything
necessary to fix it." The architecture Codex prescribed is now built
(suite 203→210):

- **Authorization is by EVIDENCE IDENTITY.** Evidence lines in the
  planner prompt carry their observation ids; citations must return
  `evidence_ids` (schema-enforced); the sanitizer validates them against
  real observations (same asset + overlap) and RE-ATTACHES them
  deterministically when the writer omits or fabricates them. At
  compile/revise, a citation with any rejected or pending id fails
  closed; all-approved ids authorize — proven by the key test: an honest
  PARAPHRASE with zero vocabulary overlap now compiles on approved
  lineage (the false-drop Codex predicted), while an approved-footage
  citation with a risky embellishment still drops.
- **Both new blockers closed**: risky claims ("ganó la carrera") no
  longer pass on brevity — a RISK-pattern claim requires actual support
  in every path, lineage or legacy; and REVISED titles pass the same
  rendered-language gate as compiled ones.
- **Title gate is risk-scoped**: poetic/descriptive titles pass without
  vocabulary overlap; only titles asserting outcomes, speech content,
  identity, emotion, or brands need approved support. User-typed titles
  (set_title) are the user's own speech — marked `user_authored` and
  exempt.
- **needs_review and evidence_ids now ride the span into the compile
  gates** (they were dropped when spans were rebuilt).
- **Restored revisions pass the title gate too** (pre-gate plans can't
  smuggle a risky model title back).
- **Corroboration guard broadened**: afirma/anuncia/menciona/habla
  sobre/contando/relata + English forms.
- Reference panel now resolves analyzed-state from stored source labels
  (truncated names lied for long filenames).

Remaining honest limits: legacy concepts without lineage use the
risk-gated lexical fallback; auto-approve confidence policy unchanged
(ratified routing policy).

## Trust-system round three (2026-09-02): the minimum remaining list

Codex's second verification caught that the evidence-pack ID edit had
been silently lost to a failed script assertion — the prompt demanded
ids the writer could not see, so everything ran on the attach-all
fallback. All five items of its minimum list are now implemented
(suite 210→213):

- evidence pack PRINTS ids (verified in-file this time); fresh documents
  whose writer omits/invents ids on a citation FAIL CLOSED (attach-by-
  overlap is reserved for legacy documents with no lineage anywhere)
- risky claims get the strict support test in BOTH branches — no brevity
  benefit-of-doubt against an unrelated approved id
- lineage persists into plan events (schema'd) and revisions inherit it
  deterministically from the current plan's overlapping events — never
  from the revision model
- restored revisions' title support is scoped to the restored plan's own
  cited material (an unused clip can no longer launder a title), and
  RENDER is the last-exit gate: legacy plans' risky unsupported model
  titles refuse to render (user-typed titles exempt, and that exemption
  survives revisions when the text is unchanged)

Known cost, accepted: legacy concepts (pre-lineage) compile via the
risk-gated fallback and shrink conservatively (23-29-abril's current
stories drop 8→3 / 6→2 scenes) — regenerating ideas restores full
coverage WITH lineage.
