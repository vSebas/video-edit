# AI-Assisted Video Editing — Status and Roadmap

**Updated:** 2026-08-04
**Current phase:** Daily-vlog walking skeleton complete. A new folder plus a
prompt now produces live visual evidence, local ASR, grounded concepts with
missing-shot advice, a validated edit plan, a review render, and OTIO/XMEML
exports entirely through owned adapters — no OpenStoryline involvement.
DaVinci import verification is pending a local Resolve installation.

**Product target (2026-08-04):** daily personal vlogs, a few minutes each,
from raw phone footage. Every edit must produce BOTH a rendered review video
and an editable export for a conventional editor. The Phase 2A per-observation
review machinery below is retained for the archived benchmark but is legacy
for daily use; routine evidence is auto-approved under an audited policy and
human review concentrates on risk-flagged claims and creative choices.

**Repository:** <https://github.com/vSebas/video-edit>

## Product Goal

Build a real-footage-first editing assistant that understands video, photos,
audio, speech, on-screen text, and user prompts. It should explain what the
footage contains, propose grounded short-form stories, recommend specific
pickup shots when coverage is weak, render an approved edit, and preserve a
manually editable project or timeline.

AI-generated video is not part of the default workflow. Models are used for
understanding, planning, and editing decisions; the user's media remains the
primary source material.

## Current Architecture Decision

The project will remain a thin independent orchestration layer rather than a
fork of one existing editor.

- The local application owns projects, prompts, analysis state, concepts, jobs,
  approvals, and outputs.
- A versioned `edit-plan.json` is the canonical editing decision artifact.
- Technical probing, validation, rendering, and exports remain deterministic.
- Visual, language, speech, and editor integrations are replaceable adapters.
- External tools may contribute components without becoming the only project
  format or hiding the edit inside an LLM conversation.

The intended data flow is:

```text
Raw media + prompt
        |
        v
Technical inventory, scenes, keyframes, audio facts, ASR
        |
        v
Grounded visual/audio observations with source timecodes
        |
        v
Concepts, hooks, structures, and missing-shot advice
        |
        v
Approved neutral edit-plan.json
       / \
      v   v
 Review render     Editable exports/projects
```

### Best-of component strategy

The goal is not to choose one repository as the whole product. The owned
workbench is the product boundary and combines the strongest proven parts:

- **Owned workbench:** project state, asset identity, evidence schemas,
  provenance, validation, approvals, planning, and the neutral edit plan.
- **Qwen/Gemini:** broad audiovisual description and planning candidates.
- **Vidi:** local or optional temporal retrieval for finding when an action
  occurs; it does not own the timeline.
- **CutScript/WhisperX:** word-level ASR, transcript editing, captions,
  diarization, and possible audio cleanup.
- **Crayotter/OpenStoryline:** workflow, artifact, revision, and planning ideas.
- **FFmpeg and possibly MediaMolder:** deterministic execution, rendering, and
  observability behind the same edit plan.
- **OpenTake and interchange exporters:** optional native/manual/MCP editing and
  conventional editor handoff.

Every component connects through an adapter. This also keeps incompatible
licenses and optional research models from becoming inseparable from the core.

## Standing Today

### Daily-vlog walking skeleton (2026-08-04)

Owned modules added under `app/video_app/`: `providers.py` (OpenAI-compatible
Qwen/Gemini client, secrets never persisted), `visual.py` (deterministic
ffmpeg scene shots, three keyframes per shot, grounded VLM captions with risk
flags and the audited `auto-live-v1` auto-approval policy), `speech.py`
(local faster-whisper with word timings and a confidence gate), and
`planning.py` (compact evidence pack → grounded concepts with missing-shot
advice; deterministic plan compiler with schema validation). The render and
OTIO/XMEML export scripts were generalized to arbitrary projects.

Verified end to end on the seven benchmark clips as a fresh project:

- 25 live visual observations; 20 auto-approved, 3 correctly risk-flagged
  (including the Chobani brand claim the old benchmark flagged), honest low
  confidence on blurry/dark shots.
- 25 low-confidence ASR segments all held below the auto-approve gate —
  correct, since the footage has no real dialogue (Whisper noise
  hallucination contained by policy).
- Two distinct concepts with prioritized missing-shot advice and fallbacks.
- A 36.2 s 1080x1920 H.264/AAC review render plus schema-validated OTIO and
  DaVinci XMEML from the same compiled `edit-plan.v1`.
- 14 API/unit tests pass; market survey (2026-08) confirmed no consumer-priced
  tool covers this loop (closest: Eddie AI at pro pricing; Novacut/Threadline
  speech-centric).

### Completed research and benchmark work

- Audited the supplied repositories, relevant additional projects, surviving
  Crayotter and FireRed experiment artifacts, and the two local papers.
- Indexed seven morning-routine source clips totaling 170.240 seconds.
- Compared FireRed semantic output, Crayotter semantic output, and 77 targeted
  independent verification frames.
- Created three evidence-grounded concepts with missing-shot advice.
- Selected and compiled the chronological concept, “Out the Door in 31
  Seconds,” into the canonical edit plan.
- Rendered and validated a 31-second 1080x1920 H.264/AAC review video with nine
  unique source ranges, linked natural audio, titles, and corrected rotation.
- Exported and structurally validated OTIO and DaVinci-compatible XMEML.
- Generated and round-tripped a native OpenTake project.

### Completed product-shell work

- Built a FastAPI project API/store and local browser workbench under `app/`.
- Added project listing, reviewed-fixture display, concept selection, technical
  ingest for arbitrary media folders, and background render/export jobs.
- Added Docker Compose workflows for the application, deterministic proof of
  concept, and optional OpenStoryline service.
- Verified API, browser, render, audio, OTIO, and XMEML paths.
- Added a background import job for saved OpenStoryline sessions. It restores
  original filename provenance, clamps ranges to immutable ffprobe durations,
  flags inference/brand/speech risks, validates `semantic-evidence.v1`, and
  keeps the result review-only.
- Imported the complete Qwen and Gemini-VLM benchmark sessions and exposed a
  side-by-side per-file comparison in the workbench.
- Added per-observation approve, edit-and-approve, and reject controls. Review
  decisions are stored separately with an append-only event history; raw and
  normalized provider evidence remain unchanged.
- Finalized both completed review runs into separate, versioned provider
  evidence sets and a scorecard. The finalizer preserves the user's decisions,
  records approved/rejected evidence separately, and does not pick a winner.
- Cross-checked the approved evidence against the independently verified
  benchmark. Two material conflicts remain visible and unpromoted: Gemini's
  invented foam event in `IMG_0994.mp4` and Qwen's bicycle/sign claim in
  `IMG_0999.mp4`.
- Added conflict jump links and revision controls to the workbench. Revising a
  reviewed decision refreshes the versioned scorecard without reloading the
  page or losing the current comparison position.
- Published the owned source, schemas, tests, canonical small JSON artifacts,
  and reports to GitHub. Private media, renders, models, papers, runtime data,
  and upstream working trees remain local.

### Deliberately incomplete

- New arbitrary projects stop at `awaiting_semantic_analysis` unless a saved
  OpenStoryline run is explicitly imported; imported projects move to
  `semantic_review_required`.
- The application does not invoke a hosted visual provider live yet.
- The current Qwen/Gemini scorecard cannot select a winner because their agent
  runs used different shot boundaries, and two approved captions still
  conflict with independently verified footage.
- Timestamped speech is not produced live yet.
- Concepts, missing-shot advice, and edit plans are not automatically compiled
  for new projects.
- Generic projects cannot render until semantic planning is implemented.
- Actual DaVinci Resolve import, prompt-driven revision, and a dialogue-heavy
  benchmark remain open acceptance checks.

## Original Plan Status

| Original phase-1 step | Status | Result |
|---|---|---|
| Define media, analysis, concept, edit-plan, and validation schemas | Complete | Versioned schemas and canonical artifacts exist |
| Build deterministic validation and OTIO/XMEML exports | Complete | Render and interchange round trips pass |
| Compare candidate semantic backends | Complete for first benchmark | Qwen and Gemini-VLM completed full runs and are normalized side by side; exact precomputed-range adapter comparison remains Phase 2 work |
| Generate and render a short timeline | Complete | Validated 31-second vertical edit |
| Test OpenTake as an editable target | Complete with partial verdict | Native project/edit/save/export works; import and some interchange fidelity do not |
| Choose the product architecture | Complete | Thin orchestrator with replaceable adapters |

## Tool Test Matrix

Test depth labels:

- **Runtime validated:** executed on the benchmark with independently checked
  outputs.
- **Build validated:** source compiled or its tests/build ran, but its strongest
  runtime workflow was not exercised.
- **Artifact evaluated:** surviving outputs were inspected and compared without
  a clean fresh run.
- **Source/research reviewed:** bounded code, README, license, and architecture
  review only.

| Tool or component | Test depth | Current verdict | Next test or trigger |
|---|---|---|---|
| Owned FastAPI workbench | Runtime validated | Keep as project/orchestration shell | Add owned live provider invocation and generic planning |
| FFmpeg/ffprobe | Runtime validated | Proven core for technical ingest, rendering, audio, and QA | Keep; add crop/caption/performance cases |
| OpenTimelineIO + owned XMEML exporter | Runtime validated | Keep as neutral and DaVinci interchange paths | Import XMEML into real DaVinci Resolve |
| FireRed-OpenStoryline | Runtime validated with Qwen and Gemini-VLM full runs | Keep as workflow/integration reference, not source truth | Compare providers through the owned adapter on exact ranges plus unseen footage |
| Crayotter | Artifact evaluated; source reviewed | Useful artifact/revision design reference; stored semantic output is unreliable | Fresh bounded comparison only after primary live adapter works; no code reuse without license clarity |
| OpenTake | Runtime/build validated for core project path | Promising optional native/manual/MCP target | After Vidi and ASR evidence compile into a neutral plan, test GUI/MCP revision, compositor render, and real DaVinci import on that plan |
| CutScript | Build validated | Useful WhisperX, transcript-editing, caption, diarization, and audio reference | After the MediaMolder/Vidi bridge spike, run CutScript and direct Faster-Whisper/WhisperX on the same dialogue-heavy material |
| MediaMolder | Build/runtime validated against mock Vidi service | Nine focused adapter tests pass; useful HTTP, buffering, passthrough, and error-handling ideas, but the documented model contract has material gaps | Next bounded spike: connect one real batch to the resident local Vidi model; then assess render parity separately |
| Vidi/Vidi2.5 | Local 8-bit inference validated with a PARTIAL verdict | The MLX checkpoint runs through CUDA managed memory on the 6 GiB RTX 2060. At 1 fps it found the verified wave onset, but ranges were too narrow; 0.5 fps failed. Keep as optional candidate retrieval, never edit authority | Expose the resident model through a bounded owned service, validate/expand ranges, and compare identical queries with Qwen/Gemini |
| NarratoAI | Source/research reviewed | Useful commentary workflow and CapCut reference | Test during CapCut compatibility phase |
| video-autopilot-kit | Source/research reviewed | Useful CapCut draft and delivery-QA reference | Test alongside NarratoAI during CapCut phase |
| Palmier Pro | Source/research reviewed | Strong agent/editor and FCPXML reference; macOS-only and GPL | Reference only unless a macOS test target becomes available |
| speclip-skills | Source/research reviewed | Reusable editing methodology, not an editing engine | Reuse relevant workflow ideas as features are implemented |
| OpenReels and other browser editors | Source/research reviewed | Possible future manual timeline UI | Revisit only if the current workbench needs a full embedded editor |
| Storyboard/Remotion/generation-first tools | Source/research reviewed | Outside the real-footage-first core | Test only if generated B-roll, voiceover, or motion-graphics scope expands |

All visible upstream source checkouts and pinned commits are listed in
`repos/README.md`. They are local reference checkouts and are not vendored into
the GitHub repository.

## OpenStoryline Configuration and Provider Readiness

The local `firered-openstoryline:local` image is installed and contains the
required TransNetV2 and resource payloads. Its Compose service mounts the local
blank `repos/FireRed-OpenStoryline/config.toml`, but this checkout has a verified
environment fallback whenever any LLM/VLM field in TOML is incomplete:

- `OPENSTORYLINE_LLM_MODEL`
- `OPENSTORYLINE_LLM_BASE_URL`
- `OPENSTORYLINE_LLM_API_KEY`
- `OPENSTORYLINE_VLM_MODEL`
- `OPENSTORYLINE_VLM_BASE_URL`
- `OPENSTORYLINE_VLM_API_KEY`

Provider credentials now live in the protected, ignored root `.env` file. Both
credentials were tested without printing them:

- Gemini authenticated against Google's model-list endpoint.
- Alibaba Model Studio authenticated through the user's workspace-specific
  Germany (Frankfurt) OpenAI-compatible endpoint.
- The Frankfurt workspace exposes `qwen3.7-plus` and `qwen3.7-max`; both passed
  minimal chat-completion checks.
- `qwen3.7-plus` reached the image-input validation path, confirming that the
  visual request route is active.
- `qwen3.5-omni-plus` is not exposed by this Frankfurt workspace.

Tracked Compose overrides map the appropriate secret variables into
OpenStoryline without embedding their values. Secrets must not be committed to
TOML, Compose, logs, reports, or canonical project artifacts.

## Initial Model Strategy

This is the starting configuration to benchmark, not a permanent vendor lock.
Model IDs and provider documentation must be rechecked before configuration.

### Recommended first integrated baseline

Use `qwen3.7-plus` for both the OpenStoryline LLM and VLM roles.

Reasons:

- The Frankfurt workspace and exact model ID have already authenticated.
- Qwen uses the same OpenAI-compatible interface that OpenStoryline and
  Crayotter expect.
- `qwen3.7-plus` is suitable for both planning and visual analysis, while
  `qwen3.7-max` is available for a later planner-only quality comparison.
- One Alibaba credential also gives the cleanest later Crayotter comparison.

Official references:

- <https://www.alibabacloud.com/help/en/model-studio/models>
- <https://www.alibabacloud.com/help/en/model-studio/vision-model/>
- <https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope>

### Independent visual comparison

Keep `qwen3.7-plus` as OpenStoryline's tool-calling LLM and use
`gemini-3.5-flash` as its VLM on the same inputs and prompts.

Reasons:

- The credential and OpenAI-compatible endpoint have already authenticated.
- Gemini accepts text, images, video, and audio, making it the main comparison
  for combined audiovisual semantics while Qwen Omni is unavailable locally.
- Keeping the planner, prompts, footage, and validation identical isolates the
  visual-provider effect from the workflow and planning model.

A pure Gemini LLM/VLM OpenStoryline smoke test reached the first `load_media`
tool successfully, then failed on the next agent turn with HTTP 400 because
Gemini requires its provider-specific thought signature to be replayed with a
function call. OpenStoryline's current ChatOpenAI/LangChain path drops that
field. This is an integration limitation, not a credential or visual-model
failure. Direct Gemini audiovisual analysis remains part of the owned-adapter
benchmark.

Official references:

- <https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash>
- <https://ai.google.dev/gemini-api/docs/video-understanding>
- <https://ai.google.dev/gemini-api/docs/openai>

After the baseline is reproducible, compare `qwen3.7-max` as a planner/reviewer
while retaining the better visual backend. GPT or Claude remain optional later
comparators rather than prerequisites for Phase 2.

### Speech model

Use local Faster-Whisper or WhisperX for timestamped ASR. This requires a local
speech-model download but no hosted API key. CutScript's implementation is a
useful reference, but speech remains an evidence adapter rather than the entire
creative planner.

### Privacy boundary

A hosted VLM test sends selected media or extracted frames, and possibly audio
or transcripts, to the provider. The application must expose that fact and
obtain project-level approval before upload. A local-only adapter can be added
for sensitive footage. Vidi 9B is not expected to be a fast default on the
current 6 GiB RTX 2060, but its 8-bit MLX/CUDA path is being tested locally
before that feasibility decision is finalized.

## Phase 2 — Live Semantic Analysis

Current implementation status: saved Qwen/Gemini OpenStoryline runs can be
imported through an asynchronous API job. Raw node artifacts remain separate;
normalized evidence is schema-validated, mapped to original assets, clamped,
risk-flagged, persisted, and shown side by side. It starts as
`review_status: pending` and `safe_for_edit_plan: false`; explicit review
decisions are applied as a separate overlay with an audit history.
Completed reviews can now be finalized into immutable version revisions plus a
current scorecard pointer. Independent benchmark conflicts remain attached to
the approved evidence and block automatic promotion without overwriting the
human decision.

### Purpose of Phase 2A

Phase 2A builds the stable evidence and adapter boundary needed to combine
models safely. It is not the final editing workflow and the benchmark review UI
is not intended to make the user approve every production clip forever.

It answers the integration questions that otherwise make a best-of system
brittle: which original asset and source range a claim belongs to, which model
and prompt produced it, how raw output differs from normalized output, whether
a range exceeds immutable media duration, whether a claim is risky, and how a
human correction can be preserved without rewriting provider history. The
Qwen/Gemini review created ground truth for comparing and calibrating future
adapters such as Vidi and ASR. In normal use, deterministic facts and
high-confidence evidence should flow automatically; review should concentrate
on conflicts, risky claims, and creative choices.

### 2A. Adapter and job boundary

1. Add a versioned provider interface for visual, speech, and planning models.
2. Add an asynchronous `analyze project` job to the API and UI.
3. Persist raw provider responses separately from normalized analysis.
4. Validate every normalized observation against source duration and schema.
5. Record provider, model ID, prompt version, timestamp, confidence, and evidence
   type without recording secrets.

### 2B. Speech and visual evidence

1. Run OpenStoryline with the Qwen baseline on one representative clip.
2. Repeat the exact smoke test with Qwen as planner and Gemini as VLM.
3. Analyze the existing benchmark and compare results against reviewed ground
   truth rather than trusting fluent captions.
4. Add local timestamped ASR using Faster-Whisper/WhisperX.
5. Add a dialogue-heavy unseen sample to evaluate speech, captions, filler
   removal, and A/V continuity.
6. Test the quality-first planner split only after the baseline is reproducible.

### Phase 2 acceptance gates

- A new folder moves from technical ingest to grounded semantic analysis.
- Visual and spoken claims contain valid source time ranges.
- Unsupported brands, emotions, chronology, and intent are not stated as fact.
- The same inputs and provider configuration produce schema-valid artifacts.
- Provider failures leave reusable technical analysis intact and can be retried.
- No API key appears in files, API responses, logs, Git history, or UI payloads.

## Phase 3 — Automatic Planning and Generic Rendering

1. Combine user prompt, visual observations, transcripts, and technical facts.
2. Generate at least two genuinely different concepts with hooks, structures,
   evidence, weaknesses, and concrete missing-shot advice.
3. Compile the selected concept into a validated `edit-plan.json`.
4. Remove the benchmark-only rendering restriction.
5. Render arbitrary projects and export OTIO/XMEML from the same plan.
6. Implement prompt-driven plan revision without rerunning unchanged media
   analysis.

The phase is complete when a new folder plus a prompt can produce concepts,
missing-shot advice, a selected edit, a review render, and editable exports
without manually inserting semantic JSON.

## Phase 4 — Editor and Format Interoperability

1. Import the current XMEML into DaVinci Resolve and compare clip order, trims,
   titles, rotation, audio linking, and duration against the review render.
2. Complete OpenTake GUI/MCP/compositor tests.
3. Connect MediaMolder's tested HTTP processor to the real Vidi service and
   correct its asset-relative timing boundary before considering it reusable.
4. Render the canonical edit plan with MediaMolder and compare observability,
   output fidelity, and maintenance cost with direct FFmpeg.
5. Test NarratoAI and video-autopilot-kit for CapCut draft compatibility.
6. Decide whether native CapCut export is supportable or should remain best
   effort because of version-sensitive project formats.

## Phase 5 — Broader Benchmarks and Hardening

- Dialogue/talking-head footage
- Mixed photos and videos
- Horizontal footage requiring subject-aware vertical reframing
- Larger folders with redundant or conflicting shots
- Multiple output formats, durations, pacing styles, and caption templates
- Performance, caching, retry, cost, and privacy controls
- Local-only model alternatives
- Exact license review at every incorporated upstream commit

## Remaining Phase 1 Acceptance Checks

These can be completed while Phase 2 begins:

1. Human creative review of the 31-second benchmark render.
2. Actual DaVinci Resolve import of the generated XMEML.
3. One natural-language revision proving that only the plan, timelines, and
   render rebuild while media analysis remains unchanged.

## Immediate Next Actions

1. DONE 2026-08-05 — DaVinci Resolve 21.0.3 installed; XMEML import verified
   frame-accurate (6/6 clips, 937/937 frames, 1080x1920@30). Exporter now
   emits DNxHR proxies + a proxy XMEML because the free Linux edition does
   not decode H.264.
2. Run the walking skeleton on a fresh, real day of vlog footage and review
   output quality, latency, and provider cost.
3. Add prompt-driven plan revision that rebuilds only concepts/plan/render
   while media analysis stays cached.
4. Add workbench UI controls for the new pipeline (analyze, concepts, select,
   compile, render, export) on top of the existing API.
5. Add sideways-clip detection (no rotation metadata) so the compiler can set
   `rotation_degrees` instead of leaving manual review.
6. Later: CapCut draft export via the OSS CapCutAPI ecosystem, Resolve
   scripting auto-import (mazsola2k pattern), dialogue-heavy footage benchmark.

Deprioritized until a concrete need appears: Vidi retrieval spike,
MediaMolder-to-Vidi bridge, Crayotter comparison, OpenTake GUI/MCP tests.

## Durable Sources of Truth

- `PROJECT_INTENT.md` — enduring product charter
- `FEASIBILITY_AUDIT.md` — Phase 1 research and candidate audit
- `poc-morning-routine/IMPLEMENTATION_RESULTS.md` — completed benchmark result
- `poc-morning-routine/SEMANTIC_BACKEND_COMPARISON.md` — semantic evidence
- `poc-morning-routine/OPENTAKE_SPIKE.md` — OpenTake verdict
- `poc-morning-routine/CUTSCRIPT_EVALUATION.md` — CutScript verdict
- `poc-morning-routine/MEDIAMOLDER_EVALUATION.md` — MediaMolder/Vidi-adapter verdict
- `app/VALIDATION.md` — application verification
- `repos/README.md` — pinned upstream source inventory

Large generated evidence and private source media are retained locally but are
excluded from the public Git repository by policy.
