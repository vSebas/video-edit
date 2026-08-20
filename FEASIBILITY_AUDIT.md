# AI-Assisted Video Editing — Preliminary Feasibility Audit

**Status:** Phase 1 audit, completed 2026-07-17. Current decisions and remaining
work are tracked in `STATUS_AND_ROADMAP.md`.
**Scope:** Existing local experiments, supplied repositories and papers, additional open-source candidates, and a recommended first proof of concept.

## Preliminary Conclusion

We should not build a full editor from scratch yet, and we should not commit to a single existing project before testing its results on real footage.

The strongest near-term direction is a small, modular pipeline that:

1. Analyzes and indexes the user's real footage.
2. Produces several grounded story/edit recommendations with exact source timecodes.
3. Reports weak coverage and recommends specific additional shots or narration.
4. Converts an approved recommendation into a neutral, validated timeline.
5. Renders a review video.
6. Exports an editable DaVinci-compatible timeline/project.

The current leading building blocks are:

- **FireRed-OpenStoryline** for an existing conversational planning/execution workflow and reusable editing nodes.
- **OpenTake** as a promising Linux-compatible, agent-controlled timeline/editor with real OTIO, EDL, and XMEML export code.
- **MediaMolder or direct FFmpeg** for deterministic media processing, rendering, and technical validation.
- **Vidi/Vidi2.5 ideas**, or another replaceable multimodal model, for temporal retrieval and higher-level video understanding.
- **CutClaw ideas** for long-footage deconstruction, music-aware pacing, smart cropping, and the planner/editor/reviewer pattern.

This is a preliminary shortlist, not a final architecture decision.

### Hands-on update: OpenTake tested

The morning-routine proof of concept now includes a native OpenTake project and a bounded hands-on round trip at commit `acf07e55082112c0361b961d058b81059d832c87`. OpenTake's project and editing crates compiled successfully; 395 targeted upstream tests passed; the 930-frame project opened, accepted a command-layer edit, saved, reopened without duration/track drift, and exported independently parseable OTIO, XMEML, FCPXML, and EDL.

The verdict is **partial**, not because the native project path failed, but because current OpenTake exports OTIO/XMEML and does not import either format. Its XMEML also omits text clips, while modern FCPXML preserves them. The exact comparison and raw evidence were in `poc-morning-routine/OPENTAKE_SPIKE.md`, removed with that directory on 2026-08-19; it survives in git history.

### Hands-on update: CutScript inspected

CutScript was inspected at commit `e5c47e31b39a4178ff3e86a9bd69eb908b7b8bb5` and pinned under `repos/CutScript`. Its Python sources compile and its React production build succeeds. It is a credible transcript-first editor: WhisperX word alignment, optional diarization, text-based deletions, transcript-driven short suggestions, captions, audio cleanup, and FFmpeg export are all represented in code.

Its AI clip suggestions consume transcript words and timestamps only; no video-frame or multimodal understanding is connected to that path. Its project schema is also single-source and transcript/deletion oriented, with no multi-asset creative timeline or OTIO/XMEML/DaVinci export. The current checkout has no test suite, and its frontend dependency audit reports four fixable advisories. Verdict: useful ASR/audio/text-editing component and UI reference, but not the central multimodal planner or editable-project layer.

## What the Local Experiments Show

Although the local `Crayotter/` and `FireRed-OpenStoryline/` folders are incomplete, they contain enough evidence to be useful.

### Crayotter

Useful evidence:

- Multimodal clip analysis JSON
- Material-gap reports
- Narrative, visual, pacing, and narration research artifacts
- Structured editing plans and blueprints
- Cut clips, intermediate renders, subtitles, transitions, and final videos
- Execution traces and experience records

The surviving completed render is a valid 1080×1920 H.264/AAC video lasting 133.7 seconds. Sampled frames show that the system found a coherent “morning routine” sequence and produced usable vertical framing and subtitles.

Important weaknesses visible in the artifacts:

- An earlier blueprint attempted to satisfy a 300-second target by stretching and repeating weak material.
- Some duration calculations and sequencing claims contradict the actual plan.
- The plan uses confident language for subjective or unsupported claims.
- The resulting pacing is long and repetitive for a typical short-form post.
- No DaVinci, OTIO, FCPXML/XMEML, EDL, or CapCut project was preserved in the experiment.

Conclusion: Crayotter is valuable as a workflow reference, especially for inspectable artifacts and revision, but it needs deterministic validation and an editable-timeline output. Its repository currently has no declared license, so its code should not be reused unless licensing is clarified.

### FireRed-OpenStoryline

Useful evidence:

- Shot splitting and clip understanding
- A sensible overall “morning routine” summary
- Grouping, script generation, ASR, BGM selection, transition planning, and timeline construction
- A structured video/subtitle timeline with source and timeline windows
- Render metadata for a vertical 608×1080, 88.979-second output

Important weaknesses visible in the artifacts:

- The final render path points into the original container cache and was not preserved with the copied experiment folder.
- The timeline is useful JSON, but no conventional editor project/interchange file was exported.
- The generated story and captions are serviceable but generic.

Conclusion: OpenStoryline is one of the best starting points because its workflow already resembles the desired product and it uses an Apache-2.0 license. The missing-shot recommendation and editable-project export would need to be added or connected externally.

## Candidate Comparison

| Candidate | Best contribution | Main limitation | License/reuse note | Current role |
|---|---|---|---|---|
| [FireRed-OpenStoryline](https://github.com/FireRedTeam/FireRed-OpenStoryline) | Conversational editing, clip understanding, scripts, reusable style skills, structured timeline | No conventional editable-project export found | Apache-2.0 | Leading workflow/base candidate |
| [OpenTake](https://github.com/appergb/OpenTake) | Cross-platform agent-native editor, MCP timeline tools, headless core, OTIO/EDL/XMEML export | Very new and still incomplete; H.264 export path is early | GPL-3.0 | Leading editor/timeline candidate |
| [MediaMolder](https://github.com/MediaMolder/mediamolder) | Declarative JSON media graphs, validation, sequence editor, transitions, audio, observability | Young project; not a creative planner; Vidi adapter is not fully turnkey | LGPL-2.1 | Rendering/validation candidate |
| [Crayotter](https://github.com/idwts/Crayotter) | Artifact-grounded planning, plan review, execution traces, reflection | Overconfident plans need hard validation; no NLE export | No declared license | Workflow reference; code reuse blocked for now |
| [CutClaw](https://github.com/GVCLab/CutClaw) | Hours-long footage analysis, shot planning/selection, music sync, smart vertical cropping | Music-montage focus; no editable project found | No declared license | Research/reference candidate |
| [Vidi](https://github.com/bytedance/vidi) | Strong temporal/spatial grounding, highlights, chapters, video QA, editing-plan research | Public repo exposes older 7B/9B weights; Vidi-Edit execution is not a turnkey local stack | Model/repo terms require closer review | Optional understanding backend/research |
| [VideoAgent](https://github.com/HKUDS/VideoAgent) | Broad intent analysis, graph planning, retrieval, understanding/editing/remaking | Heavy academic stack and model setup; not focused on editable NLE projects | MIT | Secondary research candidate |
| [NarratoAI](https://github.com/linyqh/NarratoAI) | Mature commentary/recut workflow, active project, Qwen/TwelveLabs support, CapCut draft export | Optimized for film/drama commentary rather than arbitrary personal footage | MIT | Useful specialized components/reference |
| [video-autopilot-kit](https://github.com/Hao0321/video-autopilot-kit) | CapCut draft JSON, FFmpeg pipelines, delivery QA, subtitle/B-roll checks | CapCut path is version-sensitive and Windows-first | MIT | Valuable QA and CapCut compatibility reference |
| [Palmier Pro](https://github.com/palmier-io/palmier-pro) | Agent-controlled native timeline and mature FCPXML export for Resolve | macOS only | GPL-3.0 | Design/export reference |
| [OpenReelio](https://github.com/openreelio/openreelio) | Cross-platform editor, event sourcing, prompt editing, QC framework | Explicitly pre-alpha; current OTIO module is only a stub | MIT | Watchlist, not first base |
| [OpenReel Video](https://github.com/Augani/openreel-video) | Capable browser editor and importable project files | No deep agentic footage understanding | MIT | Possible future manual editor/UI |
| [OpenCut AI](https://github.com/Ekaanth/OpenCut-AI) | Local timeline, transcription, smart reframe, text commands, shorts features | Very broad claims from a young fork need hands-on verification | MIT | Experimental watchlist |
| [CutScript](https://github.com/DataAnts-AI/CutScript) | WhisperX word alignment, transcript editing, clip suggestions, captions, diarization, audio cleanup | Clip AI is transcript-only; single-source project; no NLE interchange or visual understanding | MIT | ASR/audio/text-editing component and UI reference |
| [OpenMontage](https://github.com/calesthio/OpenMontage) | Agent contracts, production stages, approvals, self-review, real-footage/stock paths | Primarily production/generation oriented rather than personal-footage editing | AGPL-3.0 | Architecture and QA reference |

### Lower-Priority Supplied Projects

- `claude-code-video-toolkit`, `codex-storyboard`, `short-video-maker`, and `OpenReels` are useful for programmatic composition, Remotion, storyboards, captions, voiceover, or generated/stock assets, but they are generation-first and do not currently match the core real-footage understanding requirement.
- `speclip-skills` contains reusable workflow guidance for commentary, talking-head edits, FFmpeg, and portrait subtitles, but it is a skill collection rather than an editing engine.
- `video-autopilot-kit` is more directly relevant than those generation-first projects because it contains practical CapCut draft manipulation and delivery QA.

## Additional Established Components

The end-to-end candidates should be combined with stable, narrowly scoped components where appropriate:

- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) for a neutral editorial timeline and adapters.
- [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) for maintained scene/cut detection.
- [auto-editor](https://github.com/WyattBlue/auto-editor) for silence/motion-based rough cutting and editor interchange patterns.
- FFmpeg/ffprobe for probing, proxies, precise trims, audio handling, rendering, and delivery checks.
- Whisper or a compatible timestamped ASR backend for speech transcription.
- Lightweight object/face/subject detection for reframing and shot-quality signals.

These components cannot supply creative direction by themselves, but they reduce the amount of fragile custom media code.

## Editable Timeline Findings

This requirement is feasible, but export formats differ in fidelity:

- **XMEML/FCP7 XML:** practical for DaVinci Resolve and preserves more conventional editing information. OpenTake already contains a substantial implementation.
- **FCPXML:** strong Resolve/Final Cut path. Palmier Pro contains a tested exporter, but its code is GPL and macOS-oriented.
- **OTIO:** excellent neutral internal representation for cuts, tracks, timing, and media references. Effects, text styling, reframing, and transitions may not round-trip without application-specific metadata or a richer adapter.
- **EDL:** useful fallback for simple cuts, but too limited as the main project format.
- **CapCut JSON:** possible and demonstrated by NarratoAI/video-autopilot-kit, but version-sensitive and more fragile than Resolve interchange.

Recommendation: use a neutral internal edit-plan/timeline schema, export both OTIO and XMEML for the first milestone, and treat CapCut export as a later compatibility experiment.

## Hardware Constraint

The current laptop has an NVIDIA RTX 2060 Mobile with 6 GB VRAM and 16 GB system RAM.

The available Vidi1.5-9B integration guidance recommends roughly 20 GB VRAM for FP16 weights and an approximately 18 GB model download. It is therefore not a sensible default local backend on this laptop. The first prototype should keep video understanding replaceable and use either:

- A hosted multimodal model/API for the high-level analysis stage, or
- A lighter local model combined with scene detection, keyframes, ASR, and selective analysis.

Vidi can still be benchmarked through its hosted demo/API path or on stronger hardware later.

## Proposed First Architecture

```text
Raw videos / photos / audio / prompt
                |
                v
       Media inventory and proxies
   (ffprobe, hashes, scenes, ASR, keyframes)
                |
                v
      Selective multimodal understanding
 (content, people/actions, quality, exact timecodes)
                |
                v
       Creative recommendation stage
 (topics, hooks, structures, evidence, missing shots)
                |
                v
       Validated neutral edit plan
 (asset IDs, source ranges, timeline ranges, intent)
          /                     \
         v                       v
  Deterministic render      Editable export
 (FFmpeg/MediaMolder/       (OTIO + XMEML,
      OpenTake)              later CapCut)
```

The central artifact should be a versioned `edit-plan.json`, not an LLM conversation. Each proposed timeline event should include:

- Stable source asset ID and path
- Source in/out timecodes
- Intended timeline position and duration
- What the clip contains
- Why it was selected
- Evidence/confidence
- Crop/reframe intent
- Audio/caption intent
- Dependencies or missing-shot alternatives

A deterministic validator should reject missing media, invalid time ranges, impossible durations, unsupported transitions, accidental repetition, unsafe crops, and ungrounded content claims before rendering.

## Phase 1 Proof of Concept — Completed

Use the existing seven morning-routine clips as a common benchmark.

The prototype should:

1. Inventory the source files and create reusable scene/keyframe/transcript artifacts.
2. Summarize the footage accurately.
3. Propose at least three genuinely different short-form concepts.
4. For each concept, show the hook, structure, target length, exact supporting clips/timecodes, and weaknesses.
5. Recommend concrete additional shots or voiceover only where the current material is insufficient.
6. Produce one approximately 20–45 second vertical edit without invented events or filler repetition.
7. Render a review MP4.
8. Export OTIO and XMEML and verify that at least the XMEML timeline imports into DaVinci Resolve with linked source media.
9. Apply one natural-language revision without rebuilding unrelated analysis artifacts.

### Success Criteria

- All source ranges are valid and traceable.
- The content summary matches the footage.
- The recommendation is useful before rendering, not merely descriptive.
- Missing-shot advice is specific and recordable.
- The edit is substantially tighter than the existing 88–134 second experiments.
- The rendered output has correct vertical framing, readable safe-area captions, and sane audio levels.
- The DaVinci timeline opens with the expected clip order and trims.
- Revisions are localized and reproducible.

## Original Phase 1 Implementation Order

1. Define the media inventory, analysis, recommendation, and `edit-plan` schemas.
2. Build deterministic validation and OTIO/XMEML exporters early.
3. Run the same analysis prompt through two or three candidate multimodal backends.
4. Generate and render the first short timeline with direct FFmpeg or MediaMolder.
5. Test OpenTake as the interactive/manual timeline target through its MCP and project export interfaces.
6. Decide after the benchmark whether to extend OpenStoryline, integrate with OpenTake, or keep a thinner independent orchestrator.

This sequence tests the hardest and most distinctive requirement—grounded creative judgment over real footage—before investing in a large UI or a tightly coupled fork.

## Licensing Notes

- FireRed-OpenStoryline (Apache-2.0), NarratoAI/video-autopilot-kit/VideoAgent/OpenReelio (MIT), OpenTimelineIO (Apache-2.0), and PySceneDetect (BSD-3-Clause) are comparatively straightforward reuse candidates.
- MediaMolder is LGPL-2.1 and should be integrated with attention to linking/distribution obligations.
- OpenTake and Palmier Pro are GPL-3.0; modifying or distributing a derivative would carry GPL obligations.
- OpenMontage is AGPL-3.0.
- Crayotter and CutClaw currently have no declared repository license, so their code is not safe to reuse without permission or a license clarification.
- Vidi code/model terms need a separate check before product integration.

Licensing should be rechecked at the exact commit/version chosen for a prototype.
