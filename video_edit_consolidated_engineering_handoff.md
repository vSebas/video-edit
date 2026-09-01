> **Review status (2026-08-31).** Fact-checked against the codebase by Codex
> (max reasoning) and Claude. Keep for its ideas, do not execute its roadmap:
> much of it proposes what is already decided, built, or fixed, and it
> predates the Aug-19 hardening and the live OpenTake trial. What was adopted
> and what was rejected is recorded in `STATUS_AND_ROADMAP.md` ("Third-party
> handoff memo, reviewed"). Its genuinely new contribution: the
> source-context/relationship sidecar experiment and its benchmark design
> critique.

# Consolidated Engineering Handoff for `video-edit` + OpenTake

## Purpose of this note

You already have context on the current project implementation and repositories. This document is intended as a consolidated engineering handoff covering the main architectural conclusions and proposed next steps.

It combines:

- the current product/architecture assessment
- the role of `video-edit` versus OpenTake
- the major weaknesses of the current editing representation/execution layer
- the current ~8-second semantic chunking concern
- the proposed hierarchical / coarse-to-fine video-understanding pipeline
- autonomous story discovery
- speech cleanup
- `edit-plan-v2`
- atomic revision operations
- persistence/caching
- local versus cloud execution
- a concrete development order
- acceptance criteria for a representative ~90-second Reel/TikTok project

The representative product target is:

- roughly 20–30 raw phone videos per project
- mostly short clips
- occasionally 1–2 clips up to approximately 5 minutes
- output normally around 90 seconds
- Instagram Reel / TikTok oriented
- vertical-first
- English and Spanish speech
- user may provide:
  - a specific direction
  - a broad theme
  - or no direction at all

The tool should be able to either follow user intent or autonomously discover compelling story concepts from the footage.

---

# 1. Core product thesis

The strongest direction for this project is NOT:

> “AI-assisted timeline editing.”

The stronger product is:

> “Give the system a dump of raw personal footage and let it determine what compelling short-form video can be made from it, then produce an editable, grounded first cut.”

That requires two distinct capabilities:

1. **Semantic/directorial intelligence**
   - understand footage
   - identify meaningful events
   - discover stories
   - rank possible concepts
   - choose moments
   - understand speech
   - decide how the story should be assembled

2. **Editing execution**
   - trim
   - split
   - ripple delete
   - preserve A/V sync
   - overlay B-roll
   - perform J/L cuts
   - place captions
   - manipulate gain
   - export
   - allow local revisions

The project already appears stronger at (1) than at (2).

That is important: the correct move is probably to improve the representation/execution layer rather than redesign the entire semantic stack.

---

# 2. Recommended architecture: `video-edit` as brain, OpenTake as hands

The general architecture should remain approximately:

```text
RAW FOOTAGE
    │
    ▼
video-edit
    │
    ├── ingest / technical metadata
    ├── ASR
    ├── scene/shot metadata
    ├── audiovisual understanding
    ├── source/event understanding
    ├── story discovery
    ├── concept ranking
    ├── source-range selection
    └── neutral edit plan
              │
              ▼
      execution adapter
              │
      ┌───────┴────────┐
      ▼                ▼
   OpenTake          FFmpeg
   timeline          preview/export
      │
      ▼
 editable project
```

The intended separation is:

## `video-edit`

Owns:

- raw-footage semantics
- grounded evidence
- source provenance
- transcripts
- story planning
- candidate concepts
- ranking
- edit intent
- canonical plan/revision commands

## OpenTake

Primarily provides:

- richer timeline semantics
- deterministic editing operations
- ripple operations
- linked media editing
- audio/video separation
- B-roll overlays
- J/L cuts
- captions
- gain manipulation
- interactive editing
- export

OpenTake should not become the source of truth for the semantic reasoning layer unless testing proves that is necessary.

Do not rewrite the entire product around OpenTake.

Prefer a neutral intermediate representation.

---

# 3. Why the current architecture is promising

The current concept is good because the “AI” is not treated as the editor itself.

Instead, the system conceptually produces:

```text
evidence
→ story
→ grounded plan
→ deterministic execution
```

That is a better architecture than letting a language model issue unconstrained arbitrary timeline instructions directly.

Important properties to preserve:

- source-time grounding
- deterministic execution
- traceability
- reproducibility
- ability to inspect why a clip was used
- ability to change render/editor backend later
- separation between semantic decisions and mechanical editing

This is the architectural foundation to keep.

---

# 4. Main current weakness: the plan is smarter than the editor

The current system can understand footage and generate concepts, but the editing vocabulary appears too limited.

The current-ish output is still close to:

```text
Clip A source range
Clip B source range
Clip C source range
Clip D source range
...
```

That is not enough for polished short-form social content.

A real Reel/TikTok edit often requires:

- continuing audio while visuals change
- replacing talking-head visuals with B-roll
- removing fillers/false starts
- splitting speech without breaking continuity
- cutting around pauses
- J/L cuts
- music ducking
- clip-level leveling
- captions
- framing/reframing
- timing adjustments
- local edits after user feedback

The project should evolve from an AI “playlist builder” into an AI editor.

---

# 5. Highest-priority quality feature: speech cleanup

For the target use case, automatic dialogue cleanup is likely one of the biggest quality improvements available.

Example raw speech:

> “So, uh… we came here because… este… actually, I think we first heard about this place from…”

Human-edited:

> “We came here because we first heard about this place from…”

Because the project already has word-level ASR, it should be possible to identify candidate removable transcript ranges.

Examples:

```json
[
  {
    "start_s": 1.31,
    "end_s": 1.68,
    "reason": "filler",
    "confidence": 0.94
  },
  {
    "start_s": 2.95,
    "end_s": 4.20,
    "reason": "false_start",
    "confidence": 0.86
  },
  {
    "start_s": 5.43,
    "end_s": 5.91,
    "reason": "filler",
    "confidence": 0.96
  }
]
```

Candidate operations:

- remove filler
- remove false start
- remove repeated phrase
- remove duplicated take
- remove unusually long dead air
- shorten hesitations
- preserve natural pauses where appropriate

Important principle:

> Let the semantic model decide what speech is expendable, but let deterministic timeline code execute the cuts.

Do not blindly delete every filler.

Support Spanish and English.

---

# 6. `edit-plan-v2` is the central representation upgrade

The current neutral edit-plan idea is correct.

The vocabulary is too limited.

A richer neutral plan should represent at least:

- A-roll
- B-roll
- independent audio and video ranges
- J cuts
- L cuts
- voiceover
- music
- captions
- titles
- clip gain
- music ducking / envelopes
- framing
- crop
- orientation
- transitions where useful
- source provenance
- evidence provenance

Example conceptual structure:

```text
Story
 ├── A-roll event
 │    ├── video source
 │    ├── audio source
 │    ├── transcript range
 │    └── cleanup removals
 │
 ├── B-roll overlay
 │    ├── video source
 │    ├── timeline range
 │    └── preserve underlying A-roll audio
 │
 ├── caption track
 │
 ├── music track
 │    └── gain envelope
 │
 └── title/graphics track
```

A plan should be able to express:

> Use the audio from source clip 13, 4.2–9.4 s.
>
> At timeline 6.3 s, switch the visual to source clip 7, 1.4–3.9 s.
>
> Return to the speaker visual at 8.8 s.
>
> Duck music by 8 dB under speech.
>
> Remove the filler from source 5.2–5.7 s.
>
> Caption the retained speech.

The neutral plan should remain editor-agnostic.

Do not leak OpenTake-specific structures into the plan schema unless absolutely necessary.

---

# 7. Atomic revisions instead of full regeneration

Small user requests should become local commands.

Example user request:

> “Remove the second ‘este’.”

Should result in something conceptually like:

```json
{
  "op": "remove_transcript_range",
  "event_id": "a_roll_03",
  "source_start_s": 5.21,
  "source_end_s": 5.72,
  "ripple": true
}
```

Not:

```text
regenerate entire story/timeline
```

Another example:

> “Show the food while I’m talking about it.”

Could become:

```json
{
  "op": "overlay_broll",
  "source_asset_id": "clip_07",
  "source_start_s": 1.4,
  "source_end_s": 3.9,
  "timeline_start_s": 21.2,
  "preserve_underlying_audio": true
}
```

Useful atomic command vocabulary:

- trim
- split
- delete
- ripple_delete
- remove_transcript_range
- replace_visual
- overlay_broll
- move
- shorten
- extend
- reframe
- add_caption
- remove_caption
- add_voiceover
- move_voiceover
- set_gain
- add_music
- duck_music
- add_transition

Benefits:

- inspectability
- undoability
- deterministic application
- smaller reasoning scope
- no unnecessary unrelated changes
- better mapping to OpenTake

---

# 8. OpenTake integration: continue the bounded trial

OpenTake appears useful because it offers richer timeline semantics than the current flat compiler.

However, it should first pass a bounded real-world experiment.

The test should answer:

> Can `video-edit` generate a grounded plan for one real project, map that plan into OpenTake, perform dialogue cleanup/B-roll/audio operations, accept localized AI changes, and produce a stable editable timeline?

If yes:

- continue building the adapter

If no:

- copy the useful timeline semantics/ideas
- extend the existing executor or use another backend

Do not spend weeks integrating OpenTake before proving this.

Check actual current branch behavior rather than relying on stale docs.

Verify:

- project creation/open/save lifecycle
- MCP operations
- timeline operation coverage
- external automation usability
- Linux runtime stability
- export stability
- A/V sync behavior
- repeated plan application
- ID mapping between neutral plan and OpenTake objects

---

# 9. The ~8-second chunk issue

This deserves architectural attention.

The current-ish semantic-analysis flow appears roughly:

```text
original source
     ↓
scene detection
     ↓
many short segments, often <=8 s
     ↓
independent VLM analysis
     ↓
flat/local observations
     ↓
planner reconstructs global meaning later
```

This is workable but probably suboptimal for the target product.

The problem is NOT scene detection itself.

The problem is making each small shot/chunk an independent semantic unit.

---

# 10. Why independent ~8-second semantic analysis is limiting

## 10.1 Loss of semantic context

Example:

```text
8 s:
person walks through gallery

8 s:
speaker says:
"this one was actually my favorite"

8 s:
close-up of painting
```

Independent descriptions may not capture:

> “The speaker compares several exhibits, identifies a favorite painting, explains why, then shows it.”

That broader event is much more useful for editing and story discovery.

---

## 10.2 Loss of narrative relationships

Important relationships can span tens of seconds:

- setup → payoff
- question → answer
- event → reaction
- explanation → visual demonstration
- joke → reaction
- before → after
- introduction → callback
- problem → resolution

Short windows can destroy those relationships.

---

## 10.3 API overhead

A 5-minute source:

```text
300 / 8 ≈ 38
```

That can mean approximately 38 semantic units/calls.

Two 5-minute sources:

```text
≈ 75
```

before accounting for the other 20–30 videos.

This increases:

- upload overhead
- request setup
- provider scheduling
- retries
- rate-limit exposure
- intermediate files
- bookkeeping
- wall-clock latency

---

## 10.4 Reconstructing context afterward is not equivalent

This:

```text
video
→ independent local summaries
→ text-only reconstruction
```

is not necessarily equivalent to:

```text
larger audiovisual context
→ temporal interpretation directly
```

Information can be lost during early summarization.

---

# 11. Scene detection should probably remain

Cheap/local shot detection is still useful.

Example metadata:

```text
00:00.0–00:07.2
00:07.2–00:19.4
00:19.4–00:27.1
00:27.1–00:42.6
```

But instead of:

```text
shot 1 → Gemini
shot 2 → Gemini
shot 3 → Gemini
```

consider:

```text
shot boundaries
     ↓
60-second audiovisual window
     ↓
model gets boundary metadata
     ↓
structured timestamped observations
```

Example instruction:

> Analyze this 60-second interval. Detected shot boundaries occur at 0.0, 7.2, 19.4, 27.1, and 42.6 seconds. Identify meaningful events, reactions, dialogue-related visuals, and candidate usable moments. Return source-relative timestamps.

This preserves segmentation without forcing semantic independence.

---

# 12. Proposed hierarchical / coarse-to-fine understanding

The better conceptual architecture is:

```text
ORIGINAL SOURCE
      │
      ▼
GLOBAL / SOURCE-LEVEL UNDERSTANDING
      │
      ▼
source summary
major events
candidate moments
topics / people / objects
      │
      ▼
COLLECTION-LEVEL STORY DISCOVERY
      │
      ▼
candidate stories
relevant sources
      │
      ▼
EVENT-LEVEL ANALYSIS
      │
      ▼
strong source ranges
A-roll/B-roll relationships
reactions
dialogue structure
      │
      ▼
EDIT-LEVEL FINE ANALYSIS
      │
      ▼
exact cut points
exact word ranges
exact B-roll insertion
speech cleanup
```

The principle:

> Preserve broad temporal context early. Increase temporal resolution only after relevance is established.

---

# 13. Pass 1 — source-level understanding

For each original source clip, generate a broad semantic representation.

For short clips:

- analyze whole clip

For longer clips:

- potentially analyze whole clip if provider supports it well
- otherwise use large windows while preserving source-level continuity

Example:

```json
{
  "asset_id": "clip_014",
  "duration_s": 284.2,
  "summary": "Walking through a street market, choosing a taco vendor, ordering, eating, and reacting to the food.",
  "topics": [
    "travel",
    "street food",
    "market"
  ],
  "major_events": [
    {
      "start_s": 14,
      "end_s": 42,
      "description": "Entering the market and looking at stalls"
    },
    {
      "start_s": 64,
      "end_s": 118,
      "description": "Choosing a vendor and ordering"
    },
    {
      "start_s": 129,
      "end_s": 171,
      "description": "Food preparation"
    },
    {
      "start_s": 178,
      "end_s": 232,
      "description": "Eating and reacting"
    }
  ],
  "candidate_moments": [
    {
      "start_s": 184,
      "end_s": 198,
      "reason": "strong genuine first reaction"
    }
  ]
}
```

This pass should answer:

- what is this source fundamentally about?
- which major events occur?
- what is potentially interesting?
- which sections are weak or irrelevant?
- which people/objects/topics recur?
- which moments may deserve refinement?

It should NOT choose frame-perfect final cuts.

---

# 14. Collection-level reasoning

After each source has a rich compact representation:

```text
clip01: airport
clip02: taxi
clip03: hotel
clip04: market walkthrough
clip05: taco order
clip06: food prep
clip07: reaction
clip08: downtown
...
```

Ask:

> What meaningful ~90-second videos can be made from this collection?

Possible concepts:

1. First day in the city
2. Trying street food
3. Funniest moments
4. Biggest surprise
5. Visual city montage
6. User-directed theme

This is especially important for autonomous mode.

The system should discover stories BEFORE exhaustively analyzing all footage at fine resolution.

---

# 15. Pass 2 — event-level analysis

Once a story is selected or shortlisted, identify the relevant source clips/events.

Example selected story:

> “Trying street food in X”

Relevant sources might be:

```text
clip04
clip05
clip06
clip07
clip11
```

Now analyze relevant regions in larger semantic windows, e.g.:

- 30 s
- 60 s
- overlapping event windows where appropriate

Questions:

- what is the setup?
- what is the payoff?
- which reaction is best?
- which speech is useful?
- which visual works as B-roll?
- what dialogue refers to visuals elsewhere?
- where does the event naturally begin/end?
- which parts are redundant?

---

# 16. Pass 3 — edit-level analysis

After strong candidate regions are identified, use fine resolution.

Typical range:

- 3–15 s
- exact word-level ranges where needed

Tasks:

- precise cut boundaries
- filler removal
- false-start removal
- dead-air trimming
- exact reaction selection
- B-roll selection
- J/L cut timing
- caption timing
- duplicate-take detection
- choosing between similar moments

Fine granularity is valuable here.

It is just wasteful and context-destroying as the first semantic pass over everything.

---

# 17. Three-resolution mental model

| Level | Purpose | Approximate scale |
|---|---|---|
| Global/source | Understand what the clip contains | ~1–5 min / whole clip |
| Event | Understand meaningful episodes | ~30–60 s |
| Edit | Select exact usable material | ~3–15 s / word-level |

These are conceptual scales, not hard-coded constants.

Benchmark exact windows against provider behavior.

---

# 18. Do not simply change 8 s to 60 s

The intended change is NOT:

```text
8 s chunks
→ 60 s chunks
```

It is:

```text
coarse understanding
      ↓
story/event selection
      ↓
targeted refinement
```

Large-window batching may be part of the implementation, but the hierarchy is the architectural improvement.

---

# 19. Benchmark the current strategy before replacing it

Compare at least:

## A. Current short-chunk mode

Approximately one VLM analysis per short shot/chunk.

## B. ~30-second semantic windows

Multiple shot boundaries in one request.

## C. ~60-second semantic windows

More context.

## D. Whole-source mode

For 1–5 minute clips where reliable.

Each should return structured timestamps.

Measure:

### Semantic quality

- event recall
- timestamp accuracy
- reaction recall
- setup/payoff recognition
- dialogue/visual relationship accuracy
- hallucination rate
- B-roll identification
- ability to distinguish central vs irrelevant content

### Performance

- wall-clock time
- number of API requests
- provider latency
- uploaded bytes
- retry count
- rate-limit waiting
- cost

### Downstream quality

Most importantly:

> Does the planner produce better concepts and edits?

---

# 20. Persistent hierarchical evidence

The source representation should be durable and reusable.

Suggested conceptual hierarchy:

```text
asset
├── technical metadata
├── transcript
├── shot boundaries
├── global summary
├── major events
├── candidate moments
└── fine analyses
     ├── event window A
     ├── event window B
     └── edit-specific refinement
```

Possible explicit evidence levels:

- source_summary
- event
- moment
- fine_observation

Every node should preserve:

- asset ID
- source start/end
- provenance
- parent relation
- confidence/importance where useful

Do not flatten everything if that destroys structure.

---

# 21. Cache aggressively

The same footage may be reused to create multiple Reels.

Example:

- “first day in the city”
- “best food moments”
- “funniest moments”

The source-level analysis should not be rerun.

Cache keys should include at least:

- media content hash / identity
- source range
- model/provider
- prompt/schema version
- analysis configuration

Fine-grained analysis should also be cached independently.

This can substantially improve iteration speed.

---

# 22. Autonomous story discovery should be a first-class system

The autonomous mode should not be just:

```text
all evidence
→ one LLM
→ two similar concepts
```

Prefer something like:

```text
collection understanding
       ↓
candidate themes/events
       ↓
several intentionally diverse concepts
       ↓
critic/ranker
       ↓
best small set
```

Candidate families may include:

- chronological narrative
- thematic story
- visual montage
- humorous/personality-focused story
- strongest discovered event
- user-directed interpretation

Do not force every category if unsupported.

The output should be meaningfully diverse.

---

# 23. Add an editor-in-chief / critic stage

Have a separate ranking/critique pass score concepts on:

- hook
- evidence strength
- visual diversity
- narrative coherence
- spoken material quality
- redundancy
- pacing potential
- missing-footage dependency
- fit to user direction
- expected short-form retention

Potentially later:

```text
story A → rough cut
story B → rough cut
      ↓
rendered-cut critic
      ↓
best candidate
```

This is more robust than trusting one generative pass.

---

# 24. Local vs cloud execution

The current system is already hybrid-cloud in an important sense:

- audiovisual understanding is remote/API-backed
- story generation is remote/API-backed
- local machine handles:
  - ASR
  - media preprocessing
  - scene detection
  - orchestration
  - rendering/export

Therefore:

> Moving the Python server to a VM does not automatically solve the major latency problem.

If the bottleneck is many tiny remote semantic requests, the better first fix is request granularity/hierarchy.

---

# 25. Where cloud compute can help materially

Potentially useful:

| Stage | Cloud benefit |
|---|---|
| VLM understanding | already cloud |
| concept generation | already cloud |
| ASR | potentially large |
| scene detection | small/moderate |
| proxy generation | moderate |
| preview rendering | moderate |
| final rendering | moderate |
| multiple concurrent projects | large |
| durable jobs | large |

ASR is especially relevant if local execution falls back from `faster-whisper large-v3` CUDA to a smaller CPU model.

A small GPU worker can provide:

- faster inference
- `large-v3`
- potentially higher transcription quality

---

# 26. Preferred hybrid architecture

Do not cloud-host OpenTake first.

Prefer:

```text
Phone / Drive / object storage
          │
          ▼
      CLOUD/API
          │
          ├── audiovisual understanding
          ├── optional GPU ASR
          ├── story planning
          └── optional FFmpeg preview
          │
          ▼
     edit-plan-v2
          │
          ▼
    LOCAL OPENTAKE
          │
          ▼
 editable timeline/export
```

Benefits:

- remote GPU ASR
- durable processing
- local interactive editing
- simpler architecture
- no need to turn a desktop editor into a headless cloud service

---

# 27. Persistence and job durability

The current in-memory job model is acceptable for experimentation but weak for long analyses.

A process crash should not require repeating expensive work.

Prefer a simple durable layer first.

SQLite is likely sufficient for a single-user/local-first tool.

Possible entities:

- jobs
- assets
- analysis_runs
- source_summaries
- events
- observations
- concepts
- plans
- revision_commands
- renders

Avoid adding Redis/Celery/Kubernetes before justified.

---

# 28. Audio and captions

After speech cleanup and richer timeline semantics:

## Audio

Prioritize:

- clip-level speech normalization
- basic denoise where beneficial
- music placement
- speech-aware ducking
- gain envelopes
- preserving dialogue continuity across B-roll

## Captions

Treat captions as first-class timeline elements.

Support:

- transcript-grounded timing
- editable text
- style
- safe-area layout
- word/phrase emphasis later if useful

Do not make captions only a final burn-in artifact.

---

# 29. Framing/orientation

Phone footage requires practical handling of:

- rotation metadata
- vertical/horizontal sources
- static crop
- center/reframe
- subject-aware framing later if justified

Basic fit/pad is not enough for a polished social-video workflow.

---

# 30. Development priority

Recommended order:

## P0 — finish bounded OpenTake smoke test

One real project.

Prove:

- plan → OpenTake
- dialogue cleanup
- B-roll
- audio behavior
- localized edit
- export

---

## P1 — instrument and optimize analysis

Measure:

- number of VLM requests
- source duration
- shot count
- chunk durations
- upload bytes
- per-call latency
- retry/rate-limit time
- total wall time

Benchmark:

- current short-chunk mode
- ~30 s
- ~60 s
- whole-source where possible

---

## P2 — hierarchical source/event/fine analysis

Implement source-level understanding and targeted refinement.

Do not immediately destroy the existing path.

Run both in comparison.

---

## P3 — caching

Content-addressed source and fine-analysis cache.

Unchanged footage should never be repeatedly understood.

---

## P4 — dialogue cleanup

Implement filler/false-start/dead-air candidate detection and deterministic execution.

---

## P5 — `edit-plan-v2`

Add the richer neutral representation.

---

## P6 — OpenTake adapter

Map `edit-plan-v2` to deterministic OpenTake operations.

Maintain stable IDs and round-trip validation.

---

## P7 — atomic revisions

Natural-language user requests → explicit edit commands.

---

## P8 — audio/captions/framing

Improve social-video polish.

---

## P9 — durable jobs/state

SQLite-backed persistence.

---

## P10 — autonomous concept search + critic

Improve “make something good from this footage” mode.

---

## P11 — optional cloud GPU ASR / remote rendering

Only after measuring actual bottlenecks.

---

# 31. Acceptance test

Do not use “more features” as the milestone.

Use one representative real project:

- ~20–30 clips
- at least one longer clip
- spoken English/Spanish if possible
- reactions
- B-roll
- redundant footage
- weak footage
- several possible story directions

The system should be able to:

1. ingest all footage
2. preserve correct technical metadata
3. transcribe it
4. understand each source at broad semantic level
5. identify meaningful events
6. propose useful autonomous story concepts
7. follow explicit user direction when provided
8. select the relevant sources/events
9. deepen only the relevant regions
10. generate a grounded ~90-second rough cut
11. clean obvious dialogue problems
12. preserve A-roll audio while showing B-roll
13. use J/L cuts where appropriate
14. apply useful captions
15. maintain sensible audio levels/music ducking
16. preserve source provenance
17. accept localized natural-language revisions
18. avoid regenerating unrelated parts
19. produce an editable OpenTake timeline or equivalent
20. export a valid vertical social video

---

# 32. Metrics to report

For old and new pipelines:

## Runtime

- total analysis wall time
- ASR time
- VLM time
- concept generation time
- plan generation time
- preview/export time

## API behavior

- request count
- uploaded bytes
- average/median/p95 latency
- retry count
- rate-limit time
- cost where measurable

## Semantic quality

- major-event recall
- important-moment recall
- timestamp quality
- hallucination rate
- setup/payoff recognition
- dialogue/visual linking
- autonomous story quality

## Editing quality

- speech naturalness
- pacing
- visual diversity
- B-roll relevance
- continuity
- audio quality
- caption quality
- amount of manual cleanup required

---

# 33. Important implementation questions to answer first

Before changing architecture, inspect current code and report:

1. Exactly where <=8-second segmentation is introduced.
2. Is it caused by:
   - scene detection,
   - max segment duration,
   - provider constraints,
   - prompt assumptions,
   - or multiple factors?
3. Are physical subclips generated?
4. How many model calls does each original source create?
5. How are source-relative timestamps preserved?
6. Are adjacent local observations merged?
7. Is there already any clip-level global summary?
8. Does the planner know which observations came from the same original source?
9. Is transcript context included during visual analysis?
10. How are long scenes subdivided?
11. Can the current provider reliably consume:
    - 30 s
    - 60 s
    - 5 min
12. How accurate are timestamps on longer requests?
13. What provider limits actually require current behavior?
14. Which stage dominates total wall-clock time?
15. What happens after restart/crash?
16. Which artifacts are already cached?
17. What OpenTake operations can currently be driven externally?
18. Can OpenTake create/open/save/export a project without manual GUI intervention?
19. Which current docs are stale?
20. Which proposed features already exist partially?

Answer these from source and experiments, not assumptions.

---

# 34. Potential failure modes of hierarchical analysis

The coarse-to-fine approach is not automatically superior.

Watch for:

## Lossy global summaries

Small but editorially valuable moments may disappear.

Mitigation:

- require candidate-moment extraction
- preserve coverage
- retain scene metadata
- compare against short-window recall

---

## Long-window timestamp degradation

A model may understand the source but give imprecise timecodes.

That is acceptable in Pass 1 if later refinement fixes it.

Use global timestamps as retrieval hints, not final cut points.

---

## Planner over-trusts summaries

Never let vague source summaries directly drive final timeline edits.

Final cuts must still be grounded to validated source ranges.

---

## Missing short reactions

A two-second reaction may be highly valuable.

Mitigation:

- ask explicitly for reactions/high-salience moments
- use audio/emotion cues
- preserve scene boundaries
- use targeted event-level refinement

---

## Over-engineering

Do not build an unnecessary complex hierarchy.

A simple useful first version may be:

```text
source summary
→ candidate events
→ fine refinement
```

Add complexity only if measurement justifies it.

---

# 35. What not to prioritize yet

Do not spend major effort on:

- avatars
- voice cloning
- text-to-video
- object removal
- elaborate motion graphics
- advanced color grading
- multicam
- flashy transitions
- large distributed cloud infrastructure

unless the core Reel workflow demonstrates a real need.

The biggest gains are likely from:

- better semantic context
- dialogue cleanup
- richer timeline representation
- B-roll/A-roll relationships
- atomic revisions
- caching
- durable execution
- audio/captions

---

# 36. Long-term opportunity: learn user editing style

After the core workflow is stable, a strong extension is:

```text
AI draft
    ↓
user changes in OpenTake / Resolve
    ↓
structured diff
    ↓
preference/style memory
```

Potential learned preferences:

- typical clip duration
- aggressive vs conservative filler removal
- preferred pacing
- frequency of B-roll
- title usage
- caption style
- jump-cut tolerance
- music loudness
- types of clips frequently removed
- framing preference

This could eventually become more valuable than continuously changing foundation models.

---

# 37. Immediate concrete task for Codex/Claude

Before large implementation work, deliver:

## A. Source audit

Report:

- what is genuinely implemented
- what is only planned/documented
- stale docs
- current OpenTake trial state
- current editing limitations
- current chunking implementation
- current persistence/caching behavior

## B. Pipeline instrumentation

Measure a representative project.

## C. Hierarchical analysis experiment

Implement:

1. current baseline
2. source-level global analysis
3. 30–60 s semantic batching with scene boundaries
4. targeted refinement for selected regions

Compare quality, requests, latency, and cost.

## D. One dialogue-cleanup prototype

Use word-level ASR to generate explicit removable ranges.

## E. `edit-plan-v2` design proposal

Do not fully implement until the schema is reviewed.

## F. OpenTake smoke test

Map one grounded plan into OpenTake and exercise:

- trim
- ripple delete
- B-roll
- linked A/V
- audio
- local revision
- save/export

## G. Recommendation

Based on actual measurements, recommend:

- whether hierarchical analysis should become default
- whether OpenTake should become primary execution backend
- whether cloud GPU ASR is worth adding
- what next implementation step offers highest quality gain

---

# 38. Final target architecture

```text
RAW FOOTAGE
    │
    ├── metadata
    ├── ASR
    └── shot boundaries
            │
            ▼
SOURCE-LEVEL UNDERSTANDING
"What is each original clip about?"
            │
            ▼
COLLECTION-LEVEL STORY DISCOVERY
"What good short videos exist in this footage?"
            │
            ▼
STORY / CONCEPT SELECTION
            │
            ▼
EVENT-LEVEL RETRIEVAL
"Which episodes support this story?"
            │
            ▼
FINE EDIT-LEVEL ANALYSIS
"Which exact seconds/words/shots are best?"
            │
            ▼
EDIT-PLAN-V2
A-roll / B-roll / dialogue cleanup /
J-L cuts / captions / music / framing
            │
            ▼
ATOMIC EDIT COMMANDS
            │
            ▼
OPENTAKE / RENDERER
            │
            ▼
EDITABLE + EXPORTED REEL
```

The most important architectural conclusions are:

1. Keep `video-edit` as the semantic/directorial brain.
2. Use OpenTake, if the smoke test succeeds, as the richer execution/timeline layer.
3. Upgrade the neutral plan before adding many editing features.
4. Make speech cleanup a high-priority quality feature.
5. Replace exhaustive independent ~8-second semantic analysis with a measured hierarchical coarse-to-fine approach if experiments validate it.
6. Discover stories before spending fine-resolution analysis on all footage.
7. Make natural-language revisions atomic.
8. Cache source understanding and fine analysis aggressively.
9. Add durable jobs before the workflow becomes operationally important.
10. Do not do a full cloud migration until measured bottlenecks justify it.
11. Cloud GPU ASR may be worthwhile much sooner than full cloud rendering/editor execution.
12. Evaluate success on the quality and editability of a real ~90-second Reel, not on feature count.
