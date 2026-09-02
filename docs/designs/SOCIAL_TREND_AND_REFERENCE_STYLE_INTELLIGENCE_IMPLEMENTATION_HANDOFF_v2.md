> **Final verdict (Codex, 2026-09-02, after six review rounds): APPROVED.**
> All code-level reservations were implemented and verified discharged.
> Remaining reservations are ONLY run-#1-gated: real-choice weight
> calibration, the blind A/B execution, and multi-reference validation
> with genuinely independent videos.

> **Design review (Codex, 2026-09-02): APPROVE WITH RESERVATIONS.**
> (concept, style) is the correct ranking unit; measurement-first
> extraction, deterministic MVP matching, and the social/trend gate are
> sound. Binding reservations: (1) the heuristic score is scaffolding —
> never present it as a calibrated "likely to work" number, and surface
> which dimensions were actually known; (2) style-conditioned concept
> sets are NOT unbiased matching evidence — rank baseline concepts;
> (3) success requires style-application.v1 to become structured COMPILER
> input (planner prompt alone leaves measurable style non-binding) and a
> fixed-concept blind A/B showing measurable change in the rendered cut;
> (4) split the §49 experiment: matching validity (concepts fixed) vs
> application validity (concept fixed, baseline vs styled plan) —
> a styled arm that regenerates the story confounds the A/B; (5) biggest
> risk is the self-confirming label loop (model labels reference → model
> labels concept → heuristic agrees → prompt echoes labels) — antidote is
> closing the loop at the pixels: re-measure the rendered cut's grammar.
> Aggregate 3-5 references before treating a grammar as reusable.

> **Review (Claude, 2026-09-02):** Accepted; supersedes v1 where they
> differ. Its central correction — measurement-first reference analysis
> ("measure geometry and timing; infer semantics") — was independently
> confirmed the same day: an adversarial review flagged our VLM-window
> segmenter as a fabricated pacing source, and re-measuring the first real
> reference moved it from 2.07s/23.5 cpm to 0.72s/55 cpm. The shipped MVP
> slice now complies for shot structure and audio activity. Adopted from
> v2 so far: raw shot measurement, closed-set classifications, fail-closed
> numerics, schema enforcement, tiered trust in matching. Built same
> day: per-field evidence tiers (grammar_tiers at the consumption
> boundary), the audio beat grid with cut-to-beat offsets, and a light
> style-application.v1 on styled concept documents. Still open:
> caption/motion extraction, expected-style-gain on missing shots. Everything
> social/trend stays gated behind the §49-51 experiment, which is the
> same gate as roadmap item 1 (fresh-footage runs) — run it with 3-5
> references, not 1.

# Social Trend + Reference Video Style Intelligence — Implementation Handoff v2

**Project:** `video-edit` + `OpenTake`  
**Audience:** Claude / Codex / engineering agents reviewing or implementing the subsystem  
**Prepared:** 2026-09-01 (America/Los_Angeles)  
**Status:** Architecture/implementation handoff; **not** authorization to blindly implement every phase  
**v2 change:** reference analysis is explicitly **measurement-first**. A multimodal model interprets semantics; it does not serve as the sole extractor of editing grammar.  
**Supersedes conceptually:** `SOCIAL_TREND_AND_VIDEO_STYLE_INTELLIGENCE_DESIGN(1).md` where this document is more current or more specific

---

## 0. Read this first

This document is intentionally written as an engineering handoff rather than a product brainstorm.

Before changing code:

1. **Read the current repositories.**
2. **Check whether the commit snapshots below still match HEAD.**
3. **Do not re-implement capabilities that have already landed.**
4. **Do not make OpenTake the hidden canonical representation.**
5. **Do not start by integrating TikTok/Instagram providers.**
6. **Do not build a giant scraper or download corpus.**
7. **First prove that reference-style learning can improve a real edit.**
8. **Preserve the existing grounding model:** style determines *how* a real story is presented; it never authorizes inventing *what happened*.
9. **Do not ask a VLM to estimate what deterministic code can measure.**
10. **Every reference-style field should carry provenance/confidence appropriate to how it was obtained.**
11. **Treat inferred editorial intent as a hypothesis, not ground truth.**

If repository state has moved materially, treat the architecture in this document as intent and reconcile it against the new implementation before coding.

---

# 1. Repository snapshot reviewed for this handoff

The design below was reconciled against these repository states.

## `video-edit`

Repository:

```text
https://github.com/vSebas/video-edit
```

Reviewed branch:

```text
main
```

Reviewed HEAD:

```text
95d9939498af63c34e8857e83f0b8c8aef9e3f9e
```

Relevant recent changes already present by this point include:

- four-workspace UX: `Historia / Edición / Metraje / Publicar`
- reduced pipeline-facing UI
- mobile/phone UX pass
- natural-language atomic plan edits
- conversational B-roll add/remove/replace/move
- OpenTake placement
- OpenTake → canonical-plan synchronization
- voiceover placement and sync
- J/L-cut placement and round-trip sync
- dialogue cleanup flow
- revision restore/history
- canonical `edit-plan.v1`
- owned FFmpeg renderer
- Resolve export escape path

Important current paths:

```text
app/video_app/planning.py
app/video_app/projects.py
app/video_app/plan_ops.py
app/video_app/opentake_bridge.py
app/video_app/opentake_mcp.py
app/video_app/opentake_sync.py
app/video_app/context.py
app/video_app/providers.py
app/video_app/semantic.py
app/video_app/speech.py

app/schemas/creative-concepts.schema.json
app/schemas/edit-plan.schema.json
app/schemas/media-inventory.schema.json
app/schemas/semantic-evidence.schema.json
app/schemas/source-context.schema.json
app/schemas/validation-report.schema.json

app/pipeline/render_edit.py
app/pipeline/validate_edit.py

app/static/index.html
app/static/app.js
app/static/styles.css
```

The current project architecture should be treated as:

```text
user footage
    ↓
technical/media inventory
    ↓
visual + speech evidence
    ↓
grounded creative concepts
    ↓
selected concept
    ↓
canonical edit-plan.json
    ↓
 ┌──────────────┬────────────────┬────────────────────┐
 ▼              ▼                ▼                    │
owned render    OpenTake MCP      Resolve export       │
                  │                                    │
                  ▼                                    │
            timeline readback                          │
                  │                                    │
                  └──────→ canonical plan revision ────┘
```

This subsystem must fit into that architecture rather than replace it.

---

## `OpenTake`

Repository:

```text
https://github.com/vSebas/OpenTake
```

Reviewed branch:

```text
trial
```

Reviewed HEAD:

```text
d98634bdb1a98bcd7f55ac5e3b617afc3e9fa2e8
```

Relevant fork capabilities currently include or expose:

```text
list_projects
open_project
save_project

add_track
add_clips
insert_clips
remove_clips
remove_tracks
move_clips
set_clip_properties
set_keyframes
split_clip
ripple_delete_ranges
undo

add_texts
add_captions

detect_beats
auto_cut_to_beats

tighten_silences
remove_filler_words

set_color_grade
chroma_key
set_mask
apply_effect
```

The fork also supports explicit linked A/V divergence for J/L cuts.

Important observation:

> OpenTake can currently express more editing vocabulary than `edit-plan.v1` can canonically preserve.

That is an architectural constraint for style work. Do not bypass it.

---

# 2. Executive decision

The desired feature should be implemented as **two separate but composable subsystems**:

```text
A. Reference Style Intelligence
B. Social Trend Scout
```

They solve different problems.

## A. Reference Style Intelligence

Question:

> Given a small set of reference short-form videos, what reusable editing/storytelling grammar do they exhibit, and can that grammar be applied to one of the grounded stories in the user's current footage?

This is the **core subsystem**.

It must work without TikTok/Instagram provider integration.

Initial input can be:

```text
3–5 manually selected reference videos
```

or even:

```text
1 reference video
```

for an observation-only mode.

---

## B. Social Trend Scout

Question:

> Which current creators/videos/styles/sounds are worth sending to the Reference Style Intelligence system?

Trend Scout is a **reference supplier**.

It should use metadata-first discovery and deep-analyze only a few exemplars.

It should be built **after** the reference-style system proves useful.

---

# 3. Product target

The long-term product should answer:

> What current short-form editing language is relevant to the kind of content I make, which grounded story in today's footage is compatible with it, and how should the system compile that pairing into an editable video?

Primary content neighborhood:

```text
academia
graduate student life
research
engineering
AI
robotics
campus life
project progress
technical explainers
academic/personal storytelling
conference/demo/event content
```

The product is **not**:

```text
generic viral trend browser
```

and it is **not**:

```text
TikTok template copier
```

The differentiating system is:

```text
current/niche references
        +
editing grammar extraction
        +
grounded story understanding
        +
footage feasibility
        +
personal preference
        ↓
story × style pairing
        ↓
canonical edit plan
```

---

# 4. Core architecture

The recommended end-state architecture is:

```text
                    SOCIAL / TREND SOURCES
                              │
                              ▼
                         Trend Scout
                              │
                  rank / cluster / select
                              │
                              ▼
                  Reference Style Analyzer
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
      narrative            pacing              captions
       grammar             grammar              grammar
          │                   │                   │
          ├────────────── motion/audio ───────────┤
          │                                       │
          └───────────────────┬───────────────────┘
                              ▼
                         Style Library
                              │
                              │
USER FOOTAGE                  │
     │                        │
     ▼                        │
existing evidence pipeline    │
     │                        │
     ▼                        │
grounded story concepts ──────┘
     │
     ▼
Concept × Style Compatibility
     │
     ├── story match
     ├── footage feasibility
     ├── niche relevance
     ├── trend momentum
     ├── personal preference
     └── missing requirements
     │
     ▼
recommended story/style pairs
     │
     ▼
style-conditioned planner/compiler
     │
     ▼
canonical edit-plan
     │
     ├───────────────┬─────────────────┐
     ▼               ▼                 ▼
owned renderer     OpenTake          Resolve
                      │
                      ▼
                 manual edits
                      │
                      ▼
             canonical plan readback
                      │
                      ▼
             preference/style learning
```

---

# 5. Most important design correction: score `concept × style`

Do **not** make the primary sequence:

```text
raw footage
→ classify one archetype
→ select one style
→ force a story into it
```

The existing system already generates grounded candidate stories.

The same source footage can support several legitimate stories with different editing grammar.

Example source footage:

```text
walk to lab
robot setup
failed run
debugging
lunch
retry
successful run
walk home
reflection
```

Possible grounded concepts:

```text
Concept A:
"I thought this experiment was finally going to work."
problem → failure → debugging → retry → payoff

Concept B:
"What a normal robotics research day looks like."
morning → lab → work → lunch → experiment → home

Concept C:
"Why this robot kept failing."
problem → technical explanation → failed run → fix → result
```

Recommended style may differ:

| Concept | Style | Example fit |
|---|---|---:|
| Experiment failed then worked | Problem → struggle → payoff | 0.94 |
| Experiment failed then worked | Quiet academic diary | 0.72 |
| Normal research day | Quiet academic diary | 0.91 |
| Normal research day | Fast problem/payoff | 0.53 |
| Why robot failed | Technical explainer | 0.89 |

Therefore the central retrieval/ranking unit is:

```text
(concept, style)
```

not:

```text
(project, style)
```

---

# 6. Archetypes should be lightweight concept metadata initially

The previous design proposed a separate `content-archetype.v1`.

Do **not** build a standalone heavyweight archetype pipeline first.

Instead, enrich the existing grounded creative concept with editorial metadata.

Suggested fields:

```yaml
editorial:
  archetype: research_progress

  narrative_shape:
    - hook
    - setup
    - attempt
    - failure
    - retry
    - payoff

  hook_type: unexpected_result

  tone:
    - personal
    - technical
    - energetic

  dialogue_density: medium
  visual_density: high
  broll_need: medium_high

  payoff:
    present: true
    evidence_backed: true
    approximate_story_position: late
```

Possible archetypes:

```text
academic_day_vlog
research_progress
technical_explainer
personal_academic_story
project_build
event_recap
conference_day
deadline_week
study_or_class_day
failure_to_success_story
research_demo
```

These are retrieval/ranking labels, not permissions to fabricate structure.

### Implementation direction

Prefer either:

1. extend `creative-concepts.schema.json` with optional `editorial`, or
2. add a small derived sidecar keyed by `concept_id`.

Prefer option 1 if the writer can reliably produce the metadata as part of the same concept-generation pass.

Avoid a separate model call unless evaluation shows it improves accuracy enough to justify it.

---

# 7. Separate five concepts in the data model

Do not collapse these.

## 7.1 Creator profile

Persistent long-term identity/preference target.

Answers:

> What kind of creator are we trying to become?

Example:

```yaml
schema_version: creator-profile.v1

domains:
  - academia
  - engineering
  - artificial_intelligence
  - robotics
  - research

tone:
  - personal
  - intelligent
  - informal
  - curious
  - reflective

preferences:
  editing_intensity: medium
  authenticity: high
  information_density: medium_high
  cinematic_polish: medium
  meme_density: low_medium

avoid:
  - generic_influencer_style
  - excessive_transitions
  - unrelated_trend_chasing
```

This can be postponed until the core reference-style loop works.

---

## 7.2 Grounded story concept

Already exists.

Answers:

> What real story can this footage tell?

Must remain evidence-grounded.

---

## 7.3 Style

Reusable soft editing grammar.

Answers:

> How is a certain kind of short-form story typically presented?

Examples:

```text
fast_problem_payoff
quiet_academic_diary
dense_technical_explainer
reflective_personal_story
event_energy_recap
```

---

## 7.4 Template

More rigid timing/layout structure.

Answers:

> What specific timeline skeleton should be followed?

Example:

```text
0–1 s hook
1–4 s setup
4–8 s failure
8–14 s retry montage
14–18 s payoff
```

Templates should be rarer and explicitly marked as rigid.

---

## 7.5 Trend

Time-varying metadata attached to a style/reference cluster.

Answers:

> Is this language currently rising, stable, saturated, or fading?

Trend must not define style identity.

A style may remain useful after trend momentum falls.

---

# 8. Make styles compositional

Do not model a style only as one opaque preset.

Represent reusable components:

```text
Narrative grammar
Pacing grammar
Caption grammar
Motion grammar
Audio grammar
Framing grammar
```

Example:

```yaml
style_id: academic-research-problem-payoff-2026-09

components:
  narrative: problem_struggle_payoff.v2
  pacing: energetic_research_vlog.v1
  captions: bold_phrase_active_word.v3
  motion: subtle_punch_zoom.v1
  audio: beat_structural_medium_energy.v2
  framing: authentic_handheld.v1
```

Benefits:

- user can like pacing but reject captions;
- trend-specific hook grammar can be combined with personal visual preferences;
- preference learning becomes interpretable;
- style evolution does not require duplicating entire templates;
- component-level A/B evaluation becomes possible.

---

# 9. Recommended artifacts

The following are suggested **logical artifacts**. Do not create all files on day one merely because they are listed here.

## 9.1 `style-observation.v1`

One deeply analyzed reference.

Example:

```yaml
schema_version: style-observation.v1

observation_id: obs_ref_001

source:
  source_type: uploaded_reference
  platform: tiktok
  external_reference_id: null
  creator_id: null
  observed_at: 2026-09-01T22:00:00-07:00

content:
  domain:
    - academia
    - robotics

  probable_archetype: research_progress

  tone:
    - personal
    - humorous

narrative:
  hook_type: high_stakes_deadline
  beat_order:
    - hook
    - context
    - attempt
    - failure
    - retry
    - payoff
  payoff_position_ratio: 0.84

pacing:
  median_shot_seconds: 0.82
  p25_shot_seconds: 0.45
  p75_shot_seconds: 1.60
  cuts_per_minute: 52
  hard_cut_ratio: 0.94
  broll_ratio: 0.42
  dialogue_jump_cut: true

captions:
  mode: phrase
  words_per_chunk_median: 3
  max_lines: 2
  active_word_highlight: true
  position_y: 0.70
  font_class: bold_geometric_sans

motion:
  punch_zoom_frequency_per_minute: 4.2
  typical_zoom_scale:
    - 1.08
    - 1.15

audio:
  music_role: structural
  bpm: 128
  cut_to_beat_score: 0.68
  first_drop_seconds: 6.4

performance:
  raw_metrics: {}
  creator_relative_outlier_score: null

confidence:
  narrative: 0.88
  captions: 0.93
  pacing: 0.99
  audio: 0.95
```

---

## 9.2 `style-component.v1`

Reusable component.

Example:

```yaml
schema_version: style-component.v1

component_id: captions.bold_phrase_active_word.v1
kind: captions

constraints:
  mode: phrase
  words_per_chunk:
    median: 3
    allowed_range: [2, 5]

  max_lines: 2

  active_word_highlight:
    preferred: true

  position_y:
    preferred: 0.70
    range: [0.64, 0.76]

  font_class:
    preferred: bold_geometric_sans
```

---

## 9.3 `style-template.v1`

Composable style bundle.

Example:

```yaml
schema_version: style-template.v1

style_id: academic-fast-problem-payoff-2026-09

name: Fast research problem/payoff

provenance:
  sample_size: 8
  first_observed_at: 2026-08-21
  last_observed_at: 2026-09-01

components:
  narrative: narrative.problem_struggle_payoff.v1
  pacing: pacing.fast_research_vlog.v1
  captions: captions.bold_phrase_active_word.v1
  motion: motion.subtle_punch.v1
  audio: audio.medium_energy_structural.v1

applicability:
  domains:
    - academia
    - research
    - engineering
    - robotics

  archetypes:
    - research_progress
    - project_build

requirements:
  payoff_required: true
  minimum_distinct_visual_moments: 8
  dialogue: optional

trend:
  state: rising
  momentum_score: 0.84
  saturation_score: 0.33
```

---

## 9.4 `style-match.v1`

One `(concept, style)` evaluation.

Example:

```yaml
schema_version: style-match.v1

project_id: project_123
concept_id: concept_03
style_id: academic-fast-problem-payoff-2026-09

scores:
  story_compatibility: 0.93
  visual_coverage: 0.88
  dialogue_compatibility: 0.86
  payoff_compatibility: 0.97
  niche_relevance: 0.94
  trend_momentum: 0.84
  personal_preference: null

  overall: 0.91

missing_requirements:
  - type: reaction_shot
    importance: medium
    duration_seconds: [3, 4]
    expected_match_gain: 0.06

recommendation:
  recommended: true

explanation:
  - clear failure-to-success progression
  - 11 useful cutaways
  - enough dialogue for a spoken hook
```

---

## 9.5 `trend-snapshot.v1`

Historical trend observation.

Example:

```yaml
schema_version: trend-snapshot.v1

style_id: academic-fast-problem-payoff-2026-09

observed_at: 2026-09-01T12:00:00Z

metrics:
  usage_velocity: 0.81
  acceleration: 0.66
  niche_relevance: 0.94
  novelty: 0.71
  saturation: 0.33

state: rising
```

---

## 9.6 `style-application.v1`

Recommended for the MVP.

This is the **resolved style request** applied to a particular concept and plan revision.

It prevents prematurely forcing all style semantics into `edit-plan.v1`.

Example:

```yaml
schema_version: style-application.v1

project_id: project_123
concept_id: concept_03

style_id: academic-fast-problem-payoff-2026-09
style_match_id: match_009

resolved_constraints:

  narrative:
    payoff_position_ratio:
      preferred: 0.80
      range: [0.72, 0.88]

  pacing:
    shot_duration_seconds:
      median_target: 0.90
      p25_target: 0.45
      p75_target: 1.70

  broll:
    ratio_target: [0.25, 0.45]

  captions:
    component_id: captions.bold_phrase_active_word.v1

  audio:
    beat_alignment: preferred

  motion:
    punch_zoom: optional

execution_capabilities:
  canonical_now:
    - hard_cut_pacing
    - broll
    - jl_cut
    - voiceover

  requires_future_plan_schema:
    - animated_zoom
    - rich_caption_style
    - music_role
    - audio_ducking_envelope

plan_revision:
  input_revision: 4
  output_revision: null
```

This sidecar should be versioned and tied to exact plan revision/content identity.

---

# 10. Do not rush `edit-plan.v2`

There is a genuine capability mismatch:

## Canonical `edit-plan.v1` currently supports approximately

```text
video/audio/caption/title track kinds
primary/broll/voiceover roles
source/timeline geometry
playback rate
reframe metadata
basic transition_out
text
static volume
```

## OpenTake can express additional style vocabulary

```text
styled captions
text styling
keyframed scale/position/rotation
keyframed opacity
keyframed volume
beat detection
beat-aligned movement
effects
richer multi-track authoring
```

### Rule

> Never author a style feature only into OpenTake if the project cannot represent/read back the intended canonical decision.

Otherwise:

```text
edit-plan says X
OpenTake visually contains X + Y + Z
timeline sync loses Y + Z
```

and OpenTake has silently become the real source of truth.

That contradicts the current architecture.

---

## Recommended staged approach

### Stage A — MVP

Keep `edit-plan.v1`.

Compile only style features already representable canonically.

Examples:

```text
shot selection
shot-duration distribution
hard-cut pacing
B-roll amount/placement
J/L cuts
voiceover
basic title content
static audio level
story/payoff placement
```

Store unresolved richer style intent in `style-application.v1`.

The UI may say:

```text
Style matched:
- pacing ✓
- B-roll ✓
- J/L cuts ✓
- active-word captions not yet executable
- punch zoom not yet executable
```

Do not silently pretend the style is fully reproduced.

---

### Stage B — prove value

Run blinded baseline-vs-style-conditioned comparisons.

Only after richer features matter empirically should the canonical schema be expanded.

---

### Stage C — `edit-plan.v2` or equivalent extension

Candidate additions:

```text
track roles:
  music
  sfx
  captions

caption styling:
  font family/class
  weight
  size
  color
  outline
  shadow
  background box
  placement
  chunk mode
  active-word behavior

animation/keyframes:
  scale
  position
  rotation
  opacity
  volume

music metadata:
  platform audio id
  source offset
  beat grid
  downbeat grid
  late-bound flag

audio:
  envelopes
  ducking relationships

provenance:
  style id
  component ids
  style application id
```

If implementing `edit-plan.v2`, define migration from v1 and update:

```text
schema validation
owned renderer
OpenTake bridge
OpenTake sync
Resolve exporters
plan_ops
tests
validation report
```

Do not change the schema in isolation.

---

# 11. Style compatibility

The subsystem should evaluate whether a style can actually be realized using a given grounded story and footage.

Conceptually:

$$
M(c,s) =
w_S S(c,s)
+
w_V V(c,s)
+
w_D D(c,s)
+
w_P P(c,s)
+
w_N N(c,s)
+
w_T T(s)
+
w_U U(s)
$$

where:

- $S$: story-structure compatibility
- $V$: visual/B-roll coverage
- $D$: dialogue compatibility
- $P$: payoff/ending compatibility
- $N$: niche relevance
- $T$: trend momentum
- $U$: personal preference

Do not tune weights theoretically at first.

Start with explicit heuristic components and expose them in saved output.

---

## 11.1 Story compatibility

Examples:

Style requirement:

```text
problem → struggle → payoff
```

Concept:

```text
clear failed attempt
clear retry
clear success
```

High score.

Concept:

```text
quiet class/lunch/walk chronology
```

Low score.

Do not infer a required narrative beat if the concept/evidence does not contain it.

---

## 11.2 Visual coverage

Measure features such as:

```text
number of distinct usable visual moments
number of candidate cutaways
B-roll diversity
shot-type diversity
coverage around key story beats
```

Do not equate raw clip count with usable coverage.

---

## 11.3 Dialogue compatibility

Examples:

Fast text-driven explainer may require:

```text
high intelligible speech density
```

Beat montage may prefer:

```text
low dialogue dependency
```

Reflective diary may tolerate:

```text
longer continuous speech
```

Use existing ASR/transcript information.

---

## 11.4 Payoff compatibility

If style requires:

```text
before/after
reveal
successful result
reaction
```

look for evidence-backed ending material.

If absent, lower fit or generate a pickup recommendation.

Never synthesize a payoff event.

---

# 12. Missing-shot recommendations should include expected style gain

This should become a first-class integration point with the existing `missing_shots` behavior.

Example:

```yaml
missing_requirements:

  - type: reaction_shot

    importance: medium

    recording_instruction:
      Record 3–4 seconds looking at the camera immediately after the successful test.

    current_match: 0.79
    expected_match_after: 0.90
```

Potential UI:

```text
This style fits 79%.

One extra shot would make it much stronger:
Record a 3–4 s reaction after the successful test.

Estimated fit after pickup: 90%.
```

The estimate does not need to be probabilistically calibrated initially; it should simply come from rescoring with the missing requirement treated as satisfied.

Mark it clearly as an estimate.

---

# 13. Style compilation

The Style Intelligence subsystem does **not** directly place clips in OpenTake.

Correct flow:

```text
style-template
      +
selected grounded concept
      +
style-match
      ↓
style compiler
      ↓
planner constraints / style-application
      ↓
existing grounding-aware planner
      ↓
canonical edit-plan
```

Style is a creative prior.

Grounding remains authoritative.

---

## 13.1 Soft constraints

Prefer statistical targets.

Bad:

```text
every shot = 0.82 s
```

Good:

```yaml
shot_duration:
  median_target: 0.9
  preferred_range: [0.45, 1.8]
```

Bad:

```text
B-roll = exactly 37%
```

Good:

```yaml
broll_ratio:
  target_range: [0.25, 0.45]
```

Bad:

```text
payoff must occur at 81.2%
```

Good:

```yaml
payoff_position:
  target_range: [0.72, 0.88]
```

---

## 13.2 Hard constraints

Use only for factual/technical feasibility.

Examples:

```text
do not fabricate a missing failure
do not use asset outside evidence bounds
do not overlap B-roll events illegally
do not exceed source duration
do not place unsupported media type
do not violate canonical plan schema
```

---

# 14. Mapping abstract style grammar to editing actions

Examples.

## `fast pacing`

Style intent:

```yaml
pacing:
  median_shot_seconds: 0.9
```

Planner/compiler responsibility:

```text
choose evidence-backed moments whose aggregate duration distribution approaches target
```

Do not simply trim every clip independently to 0.9 s.

Speech intelligibility and semantic completeness override pacing targets.

---

## `higher B-roll ratio`

Style intent:

```yaml
broll:
  target_ratio: [0.30, 0.45]
```

Execution:

```text
maintain primary dialogue/audio
overlay evidence-backed B-roll
prefer cutaways that support the current spoken/story beat
```

Current code already has B-roll operations and canonical support.

---

## `J/L transitions`

Style intent:

```yaml
audio_transition:
  jl_cut:
    preferred: true
    typical_seconds: [0.3, 1.2]
```

Execution:

```text
apply only where adjacent source audio permits it
```

Current `plan_ops.py`, OpenTake placement, and timeline sync already provide a foundation.

---

## `late payoff`

Style intent:

```yaml
narrative:
  payoff_target_ratio: [0.75, 0.90]
```

Execution:

```text
select/order grounded story beats so the verified payoff occurs near the range when feasible
```

Do not move chronology in a misleading way where chronology itself is important.

---

## `beat-aware montage`

MVP intent:

```yaml
audio:
  beat_alignment: preferred
```

Execution path may initially be:

```text
beat analysis
→ provide beat markers to planner
→ quantize non-dialogue cut candidates near high-confidence beats
```

Do not assume OpenTake's `auto_cut_to_beats` is a montage generator. It currently supplies beat/cut/placement guidance and can move selected clips; the project planner must still decide which moments make narrative sense.

---

# 15. Reference Style Intelligence

This is the first subsystem to implement.

## Input

Initial support:

```text
local video file
```

Optional metadata:

```yaml
platform: tiktok | instagram | youtube | unknown
creator: optional
reference_url: optional metadata only
notes:
  - "I like the pacing"
  - "I dislike the captions"
```

Do not require persistent storage of external platform media.

---

# 16. Reference-analysis pipeline — measurement first

The reference analyzer should **not** be designed as:

```text
reference video
    ↓
one multimodal prompt
    ↓
"editing style"
```

That is too dependent on capabilities that current general-purpose video-language models do not reliably provide.

The recommended pipeline is:

```text
reference video
      ↓
technical probe
      ↓
DETERMINISTIC / SIGNAL ANALYSIS
      ├── shot boundaries
      ├── shot durations
      ├── cut timestamps
      ├── audio energy
      ├── beat/onset grid
      ├── caption timing/layout where detectable
      ├── motion/zoom measurements where detectable
      └── other measurable temporal signals
      ↓
structured timeline representation
      +
representative frames / local video windows
      +
transcript
      ↓
MULTIMODAL SEMANTIC ANALYSIS
      ├── hook type
      ├── narrative beats
      ├── payoff/reveal
      ├── shot semantic roles
      ├── B-roll purpose
      ├── tone
      └── editorial hypotheses
      ↓
style-observation.v1
```

The fundamental rule is:

> **Measure geometry and timing; infer semantics.**

Current multimodal models are useful for editorial/narrative interpretation, but should not be treated as reliable measurement devices or perfect interpreters of editing intent.

---

# 17. Evidence tiers inside `style-observation.v1`

Not every field has the same epistemic status.

Each observation should be tagged, directly or through a shared metadata structure, with one of four evidence classes.

## Tier A — measured

Values computed from deterministic media/signal analysis.

Examples:

```yaml
median_shot_seconds:
  value: 0.82
  evidence_type: measured
```

Examples of Tier A fields:

```text
shot timestamps
shot-duration quantiles
cuts/minute
audio energy
detected beat timestamps
cut-to-beat distance
caption bounding-box location when directly detected
caption appearance/disappearance timing
```

These should generally be the most trusted style features.

---

## Tier B — detected / classified

Values produced by bounded classifiers or recognition systems.

Example:

```yaml
caption_mode:
  value: phrase
  evidence_type: classified
  confidence: 0.96
```

Possible Tier B fields:

```text
talking-head vs cutaway
caption mode
active-word highlighting
transition category
zoom present / absent
speech vs music regions
```

These are useful but should carry confidence.

---

## Tier C — semantic interpretation

Values inferred by a multimodal model from the transcript, visual timeline, and selected video windows.

Example:

```yaml
hook_type:
  value: high_stakes_deadline
  evidence_type: semantic_inference
  confidence: 0.82
```

Possible Tier C fields:

```text
hook type
narrative beat sequence
payoff/reveal identity
tone
shot semantic role
B-roll semantic function
```

These can meaningfully condition style matching, but should not override measured evidence or grounding.

---

## Tier D — editorial hypothesis

Higher-level claims about *why* the original editor made a choice.

Example:

```yaml
editing_intent:
  value: pacing accelerates to build tension before the reveal
  evidence_type: editorial_hypothesis
  confidence: 0.58
```

Possible Tier D fields:

```text
why a particular cut was chosen
why a zoom was used
why music intensity changes at a specific story beat
whether an editor intended suspense, comedy, intimacy, etc.
```

Tier D is optional.

Do **not** make core planning behavior depend heavily on Tier D.

---

# 18. Deterministic extraction responsibilities

Prefer deterministic or signal-processing methods for anything that can be measured directly.

## 18.1 Shot structure

Measure:

```text
shot boundaries
shot durations
median / p25 / p75
cuts per minute
hard-cut candidates
transition durations where detectable
```

Do not ask a VLM whether pacing is "fast" before computing the actual timing profile.

The semantic model may later interpret what the measured pacing is doing.

---

## 18.2 Audio structure

Measure where possible:

```text
speech regions
music regions
audio-energy envelope
onsets
beat grid
BPM estimate
downbeat candidates
silence intervals
cut-to-beat offsets
```

Example:

```text
cut at 13.50 s
nearest strong beat at 13.47 s
distance = 30 ms
```

That is a measured temporal fact.

The multimodal model can later reason whether beat synchronization is important to the style.

---

## 18.3 Caption structure

Use OCR/text detection only when necessary, but prefer direct visual/text detection over free-form VLM estimates for:

```text
caption bounding boxes
caption start/end times
words per visible chunk
line count
relative screen position
capitalization
color changes
background boxes
active-word behavior
```

Exact font identity is not required.

Map uncertain typography to semantic categories:

```text
bold_geometric_sans
rounded_sans
condensed_sans
serif_editorial
monospace_technical
```

---

## 18.4 Motion / zoom structure

Use measurable image transforms where practical:

```text
global scale change
pan/translation
rotation
motion magnitude
crop/reframe change
```

Do not attempt sophisticated effect recognition in the MVP if execution support does not exist.

---

## 18.5 Local cut-window analysis

Whole-video reasoning is not sufficient for many edit decisions.

For relevant cut boundaries, construct local windows such as:

```text
2 s before cut
+
cut boundary
+
2 s after cut
```

Provide the semantic model with:

```text
preceding transcript
following transcript
shot descriptions
audio continuity
visual continuity
measured timing
```

Then classify the probable editorial function using a bounded taxonomy:

```text
dialogue_cleanup
broll_illustration
reaction
temporal_compression
location_transition
emphasis
action_continuity
reveal
montage_rhythm
other
```

This is preferable to asking:

> "Explain every editing decision in this video."

---

# 18.6 Multimodal semantic-analysis responsibilities

Use the multimodal model for problems that require interpretation rather than direct measurement:

```text
hook type
story arc
narrative beats
payoff / reveal identity
tone
shot semantic role
B-roll purpose
whether a visual supports the spoken claim
transferability to academic/research content
```

The model should receive a structured representation of the video, not only raw pixels.

Recommended context:

```text
transcript
shot table
measured pacing statistics
audio/beat statistics
caption observations
representative frames
selected local video windows
```

This turns a vague task:

```text
"understand the editing style"
```

into smaller constrained inference tasks.

---

# 18.7 Do not require perfect editorial understanding

The system does not need to recover the original editor's internal intent.

The target is a reusable **statistical editing grammar**.

Across 3–10 references, useful recurring evidence may look like:

```text
median shot duration       ≈ 0.84 s
B-roll ratio               ≈ 38%
hard cuts                  ≈ 93%
caption chunks             ≈ 2–4 words
active-word emphasis       common
payoff                     usually final 20%
problem hooks              6 / 8
beat-synchronized montage  5 / 8
punch zoom                 occasional
```

Even if a few semantic labels are wrong, aggregation can still yield a useful style profile.

This is one reason the project should deep-analyze multiple representative references rather than trying to infer a perfect style from one video.

---

# 18.8 Reliability rules for the style compiler

The compiler should consume evidence differently by tier.

Suggested default:

```text
Tier A measured:
  strong influence

Tier B classified:
  strong/moderate influence depending on confidence

Tier C semantic:
  moderate influence, subject to grounding

Tier D editorial hypothesis:
  weak influence / explanation only
```

A Tier C or Tier D inference must never override:

```text
source evidence
measured timing
canonical validation
grounded story facts
```

---

# 18.9 Model strategy

Do not train a specialized editing model first.

Start with:

```text
deterministic extraction
+
strong general multimodal model
+
structured prompts
+
closed-set classifications where possible
+
small human-reviewed fixture set
```

Only consider a specialized model/adapter after collecting enough corrected observations to show repeated, material failure modes.

Possible long-term loop:

```text
general VLM observations
      ↓
human corrections
      ↓
project-specific editing-understanding dataset
      ↓
specialized classifier / adapter
```

But this is not part of the first implementation phases.

---

# 19. Hook taxonomy

Initial closed set:

```text
curiosity
confession
problem
challenge
before_after
unexpected_result
contrarian_statement
question
high_stakes_deadline
visual_reveal
outcome_first
```

Allow:

```text
other
```

with model explanation.

Examples:

```text
"I have three hours to fix this."
→ high_stakes_deadline

"I thought this experiment finally worked."
→ unexpected_result

"This is what a research day actually looks like."
→ curiosity/authenticity
```

Do not overfit the taxonomy before real references are analyzed.

---

# 20. Narrative grammar extraction

Possible beats:

```text
hook
setup
context
problem
attempt
failure
escalation
retry
reveal
payoff
reflection
CTA
loop
```

Store:

```yaml
narrative:
  archetype: problem_struggle_payoff

  beats:
    - hook
    - context
    - attempt
    - failure
    - retry
    - payoff

  payoff_position_ratio: 0.82
```

This is descriptive.

It is not a script to fabricate beats later.

---

# 21. Pacing extraction

Store distributions rather than one average.

Example:

```yaml
pacing:
  median_shot_seconds: 0.82
  p25_shot_seconds: 0.45
  p75_shot_seconds: 1.60

  cuts_per_minute: 52

  hard_cut_ratio: 0.94

  dialogue:
    median_visible_segment_seconds: 1.4
    jump_cut_density: medium

  broll_ratio: 0.42
```

Why:

```text
0.82 s average
```

cannot distinguish:

```text
all shots ≈0.82 s
```

from:

```text
many 0.3 s shots + several 4 s shots
```

which feel very different.

---

# 22. Caption extraction

Desired observations:

```text
phrase vs word mode
words per chunk
characters per line
max lines
position
alignment
active word highlight
font class
weight
capitalization
outline
shadow
background box
emoji usage
punctuation style
entrance/exit behavior
```

Prefer semantic font classes where exact fonts are unknown:

```text
bold_geometric_sans
condensed_sans
rounded_sans
serif_editorial
monospace_technical
```

Then map them to installed/allowed fonts later.

Do not make exact proprietary font identification a hard dependency.

---

# 23. Motion/effects extraction

Initial useful categories:

```text
punch zoom
scale drift
pan
static reframe change
speed change
freeze frame
flash frame
camera shake
text pop
match cut
```

Prioritize features that can eventually be represented by the canonical plan and OpenTake/owned renderer.

Do not spend early implementation effort extracting complex effects the product cannot execute.

---

# 24. Audio extraction

Desired observations:

```text
music present
music role:
  background
  structural
  emotional
  montage

BPM
beat grid
downbeats
energy curve
drop locations

speech/music balance
ducking behavior
impact SFX
silence usage
```

Where licensed platform audio is involved, separate:

```text
audio identity
```

from:

```text
audio structure
```

The structure can be used for editing even if the asset is late-bound.

---

# 25. Music should normally be late-bound

Do not default to downloading and embedding licensed trending audio.

Store:

```yaml
platform_audio:
  platform: instagram
  audio_id: ...
  title: ...
  artist: ...

  source_offset_seconds: 12.40

  bpm: 128

  beat_grid_seconds:
    - 0.00
    - 0.47
    - 0.94

  beat_zero_seconds: 0.00

  late_bound: true
```

Important:

> The sound's **source offset** must be known if the edit is built against a specific segment.

Otherwise the user may attach the same song but a different portion and all beat alignment is wrong.

Potential draft workflow:

```text
style chooses platform sound + segment
      ↓
planner cuts against stored beat structure
      ↓
owned preview uses click/guide/silence or permitted local reference
      ↓
export video without unauthorized embedded music
      ↓
publishing step tells user which native sound + offset to attach
```

Do not claim automated platform attachment until an allowed API actually supports it.

---

# 26. Aggregating observations into a style

One viral reference is **not** a trend.

But one reference can still define:

```text
reference style observation
```

For reusable cluster style:

```text
analyze multiple references
→ group structurally similar observations
→ aggregate robust statistics
```

Example:

```text
8 references

6/8 problem hook
7/8 payoff final quarter
6/8 2–4 word caption chunks
7/8 >85% hard cuts
5/8 occasional punch zoom
```

Result:

```text
Research Problem/Payoff v1
```

Use medians/quantiles rather than exact means when sensible.

---

# 27. Clustering

Do not begin with sophisticated unsupervised ML.

MVP can use:

```text
normalized structured feature vector
+
simple distance / rule grouping
+
semantic similarity
+
manual inspection
```

Only introduce more complex clustering if reference volume justifies it.

Candidate feature groups:

```text
narrative embedding/category
hook category
payoff position
shot-duration quantiles
B-roll ratio
caption mode/features
beat-alignment score
motion feature rates
dialogue density
```

Keep cluster provenance.

---

# 28. Social Trend Scout — later subsystem

Do not implement this before the manual-reference style loop produces useful results.

Its job:

```text
find relevant candidates cheaply
→ rank them
→ cluster them
→ choose a very small number of exemplars
```

Not:

```text
download social media at scale
```

---

# 29. Trend discovery pools

Maintain conceptually two candidate pools.

## 29.1 Niche pool

Examples:

```text
graduate students
PhD students
researchers
engineers
AI / robotics creators
university vloggers
science communicators
technical educators
academic lifestyle creators
```

Question:

> What are creators close to the target identity doing now?

---

## 29.2 Global transferable pool

Some useful formats originate outside academia.

Example:

```text
travel:
"I had 3 hours to fix this..."

academic adaptation:
"I had 3 hours before my research demo..."
```

Trend Scout should identify the underlying format and evaluate transferability.

This prevents niche discovery from becoming stylistically narrow.

---

# 30. Metadata-first funnel

Example:

```text
5,000 candidate posts
      ↓
500 niche/semantic matches
      ↓
100 high-signal candidates
      ↓
clusters
      ↓
5–10 representative exemplars
      ↓
deep Reference Style Analysis
```

Use cheap metadata such as:

```text
creator niche
caption
hashtags
sound id
duration
upload time
views
likes
shares
comments
growth
creator baseline
semantic text embeddings
```

Deep audiovisual processing should be rare.

---

# 31. Candidate scoring

Conceptual ranking:

$$
S_i =
w_R R_i +
w_G G_i +
w_E E_i +
w_N N_i +
w_O O_i
$$

where:

- $R_i$: niche/semantic relevance
- $G_i$: growth/velocity
- $E_i$: engagement quality
- $N_i$: novelty
- $O_i$: creator-relative overperformance

Do not tune the weights before real provider data exists.

---

# 32. Creator-relative outperformance

Raw views are misleading.

A 600k-view video from a creator whose recent median is 20k may be more informative than a 500k-view video from a creator who routinely gets 500k.

Do not use an unstable raw ratio blindly.

Safer MVP transform:

$$
O_i =
\log\left(
1+
\frac{P_i}{\tilde P_{\text{creator}}+\epsilon}
\right)
$$

where:

- $P_i$ = selected performance measure
- $\tilde P_{\text{creator}}$ = robust recent creator baseline

Later consider:

```text
creator percentile
MAD / robust z-score
velocity-normalized outlier
minimum sample support
follower-adjusted expectations
```

---

# 33. Trend state

Track:

```text
rising
established
peaking
falling
```

Potential conceptual score:

$$
T =
w_V V +
w_A A +
w_R R +
w_N N -
w_P P
$$

where:

- $V$: velocity
- $A$: acceleration
- $R$: niche relevance
- $N$: novelty
- $P$: saturation/peaked penalty

Store historical snapshots.

Do not overwrite trend history with one current score.

---

# 34. Provider architecture

Do not hardcode one provider into the style engine.

Suggested interface:

```python
class TrendProvider(Protocol):
    def search_creators(self, query, filters) -> list[CreatorCandidate]: ...
    def search_posts(self, query, filters) -> list[PostCandidate]: ...
    def get_post_metrics(self, refs) -> list[PostMetrics]: ...
    def get_audio_trends(self, query, filters) -> list[AudioCandidate]: ...
```

Providers may expose only a subset.

Capability declaration:

```yaml
provider: example

capabilities:
  creator_semantic_search: true
  post_search: true
  post_history: false
  audio_trends: true
  instagram: true
  tiktok: true
  youtube_shorts: false
```

The discovery layer should route based on capabilities.

---

# 35. Provider candidates

The earlier design identified candidates such as:

```text
Modash
Exolyt
Shortimize
Pentos
ViralStat
TikTok Creative Center
official/native APIs where useful
```

Treat this as a **candidate list, not verified current capability truth**.

Before integration, Claude/Codex must verify current:

```text
API availability
pricing
rate limits
search capabilities
post metadata
historical metrics
audio/sound data
media access
platform coverage
terms
storage/retention restrictions
```

Create:

```text
TREND_PROVIDER_BAKEOFF.md
```

only when provider integration becomes the active phase.

Do not purchase/integrate multiple providers before a small benchmark.

---

# 36. Reference-media retention

Preferred flow:

```text
provider/reference
      ↓
temporary permitted access
      ↓
analysis
      ↓
derived structured observation
      ↓
discard temporary media when appropriate
```

Persist primarily:

```text
external reference id
creator id
observed timestamp
metadata metrics
derived semantic features
derived style features
style observation
```

Do not build a private mirror of social platforms unless explicitly permitted and genuinely necessary.

---

# 37. Safe style imitation

Learn/reuse:

```text
pacing
caption conventions
story grammar
transition category
shot ratios
music structure
hook patterns
B-roll function
motion categories
```

Do not copy:

```text
original footage
scripts
watermarks
logos
unique branded graphic packages
exact proprietary assets
```

Goal:

> reuse a visual/editing language, not duplicate a creator's work.

---

# 38. Current execution capability matrix

Claude/Codex should create a live capability table before implementing rich styles.

Initial understanding at reviewed commits:

| Style feature | Canonical `edit-plan.v1` | Owned renderer | OpenTake trial | MVP recommendation |
|---|---:|---:|---:|---|
| hard-cut pacing | yes | yes | yes | implement |
| shot-duration targets | indirect | yes | yes | implement |
| B-roll placement | yes | yes | yes | implement |
| B-roll ratio target | planner-level | yes | yes | implement |
| J/L cuts | represented via A/V geometry | yes | yes | implement |
| voiceover | yes | yes/current path | yes | implement |
| static audio volume | yes | yes | yes | implement |
| basic title text | yes | yes | yes | implement |
| basic burned captions | external/current render path | fixed style | styled support | partial |
| rich caption styling | insufficient | insufficient | richer | postpone canonical change |
| active-word captions | insufficient | insufficient | potentially richer | postpone |
| keyframed punch zoom | no | no/insufficient | yes | do not OpenTake-only |
| animated position/scale | no | no | yes | do not OpenTake-only |
| beat detection | no canonical beat artifact | external work | yes | analyze first |
| beat-aligned montage | planner work | possible later | partial helper | later |
| BGM track role | no explicit canonical role | incomplete | yes | later |
| SFX track role | no explicit canonical role | incomplete | yes | later |
| ducking envelope | no | no | yes keyframes | later |
| speed changes | playback_rate | yes | yes | possible |
| true speed ramp | no | no/limited | investigate | later |
| advanced effects | no | limited | richer | later |

The table must be revalidated against current code before acting.

---

# 39. Recommended module boundaries in `video-edit`

Do not create directories until a minimal slice is selected, but recommended eventual layout:

```text
app/video_app/

    styles/
        __init__.py
        schemas.py
        observations.py
        extractor.py
        narrative.py
        pacing.py
        captions.py
        motion.py
        audio.py
        aggregate.py
        library.py
        matching.py
        compiler.py
        capabilities.py

    trends/
        __init__.py
        models.py
        providers/
            base.py
            ...
        discovery.py
        scoring.py
        clustering.py
        snapshots.py
        store.py
```

Potential alternative:

```text
app/video_app/style_intelligence.py
```

for the first vertical slice.

Do not create 15 empty files before there is real implementation.

A reasonable first slice could be:

```text
app/video_app/style_intelligence.py
app/schemas/style-observation.schema.json
app/schemas/style-template.schema.json
app/schemas/style-match.schema.json
```

then refactor once functionality warrants it.

---

# 40. Integration points with current code

## `planning.py`

Responsibilities to add/refactor:

```text
accept optional resolved style constraints
condition concept-to-plan compilation on soft pacing/B-roll/narrative targets
preserve grounding gates
emit style provenance
```

Do not make the writer directly emit arbitrary low-level OpenTake actions.

---

## `projects.py`

Likely responsibilities:

```text
persist style artifacts
run reference analysis jobs
retrieve style library
compute concept × style matches
apply selected style to a concept
create style-conditioned plan revision
```

Be careful: `projects.py` is already large.

Do not keep expanding it indefinitely if this subsystem becomes substantial.

---

## `plan_ops.py`

MVP should not overload atomic edit ops with global style application.

Style application is generally:

```text
planner/compiler revision
```

not:

```text
one closed-set atomic op
```

However later local actions could include:

```text
change_caption_style
change_pacing_profile
apply_motion_component
```

only after canonical support exists.

---

## `opentake_bridge.py`

Add mappings only for canonical features.

Do not consume uncanonical `style-template` fields and directly mutate OpenTake behind the plan's back.

---

## `opentake_sync.py`

If the canonical plan later gains:

```text
caption style
keyframes
music/SFX roles
```

sync must preserve/read them back before those features are considered fully integrated.

---

## `render_edit.py`

Do not add style-specific special cases indefinitely.

If rich style lands, it should render from generic canonical plan fields.

Current hard-coded subtitle styling is a known limitation for style reproduction.

---

## `creative-concepts.schema.json`

Candidate place for lightweight editorial metadata.

---

## `edit-plan.schema.json`

Do not alter for MVP unless necessary.

If moving to v2, treat it as a coordinated architecture migration.

---

# 41. Suggested API surface for the first implementation

Exact routes should follow existing FastAPI conventions.

Possible semantics:

```text
POST /api/projects/{project_id}/styles/references
```

Register/analyze manually provided references.

```text
GET /api/styles
```

List learned/manual style templates.

```text
POST /api/projects/{project_id}/style-matches
```

Compute matches across current concepts.

```text
POST /api/projects/{project_id}/concepts/{concept_id}/styles/{style_id}/apply
```

Generate a style-conditioned plan revision.

```text
GET /api/projects/{project_id}/style-matches
```

Retrieve persisted results.

Do not expose a large public API until the internal artifact shapes stabilize.

---

# 42. UI integration

Do **not** add top-level tabs like:

```text
Trends
Styles
Intelligence
```

The recent UX deliberately simplified the app.

Keep current:

```text
Historia
Edición
Metraje
Publicar
```

---

## 42.1 `Historia`

Recommended placement:

Each story concept can show:

```text
Recommended style
Problem → struggle → payoff

↑ Rising in research / engineering

Fit: 93%

Why:
- clear failed attempt
- retry exists
- successful result
- 11 useful cutaways

One extra reaction shot would strengthen the ending.

[Hacer esta]
[Ver estilo]
[2 referencias]
```

Alternative styles can be collapsed.

Do not display a wall of social references.

---

## 42.2 `Edición`

Current quick actions include:

```text
Afinar diálogo
Subtítulos
Cambiar historia
```

A future additional action can be:

```text
Cambiar estilo
```

Only show it when the backend can actually generate a different style-conditioned revision.

Do not add placeholder controls.

---

## 42.3 References

Reference preview should answer:

```text
Why is this reference relevant?
Which parts of its grammar are being borrowed?
Which parts are not?
```

Example:

```text
Borrowing:
✓ pacing
✓ problem/payoff structure
✓ B-roll density

Not borrowing:
✗ meme overlays
✗ aggressive zooms
✗ exact caption font
```

This makes the system feel intentional rather than derivative.

---

# 43. Explain recommendations in editorial language

Bad:

```text
AI confidence: 0.91
```

Good:

```text
Recommended because this story contains a real failed attempt, retry, and successful result; there are 11 usable cutaways, and enough speech for a strong spoken hook.
```

Scores can remain visible secondarily.

The explanation should be derived from saved match components, not hallucinated after the fact.

---

# 44. Personal preference learning — later, but architect for it now

Eventually final score:

$$
S_{\text{final}} =
w_t T +
w_n N +
w_c C +
w_f F +
w_p P
$$

where:

- $T$: trend momentum
- $N$: niche relevance
- $C$: concept/story compatibility
- $F$: footage feasibility
- $P$: personal preference

Over time $P$ should become more influential.

---

# 45. Most valuable preference signal: finishing edits

The OpenTake round-trip architecture creates an unusually useful signal.

Example:

```text
AI proposal:
shot duration 2.3 s

user final:
1.2 s
```

Possible learned preference:

```text
user tends to shorten setup shots
```

Another:

```text
AI proposal:
no B-roll over this line

user:
adds lab cutaway
```

Possible learned preference:

```text
user prefers more visual coverage during technical dialogue
```

Another:

```text
AI:
active J-cut 0.4 s

user:
extends to 1.0 s
```

This is richer than explicit thumbs-up/down.

---

# 46. Preference-event model

Later persist differences between:

```text
generated canonical plan revision
```

and:

```text
user-finished synced revision
```

Potential derived event:

```yaml
schema_version: style-feedback-event.v1

project_id: ...
base_revision: 4
finished_revision: 7

component:
  kind: pacing

observation:
  generated_duration_seconds: 2.3
  final_duration_seconds: 1.2

context:
  story_beat: setup
  dialogue: false
```

Do **not** infer stable personal preferences from one edit.

Aggregate over repeated behavior.

---

# 47. Evaluation philosophy

This subsystem must inherit the project's existing evidence-first culture.

The main hypothesis is not:

> Did we successfully extract a style?

It is:

> Did applying reference-derived style grammar produce a better real video without harming grounding, authenticity, editability, or user control?

---

# 48. Evaluation layers

## 48.1 Extraction accuracy

Check measurable fields:

```text
shot boundaries
shot-duration quantiles
caption timing
caption geometry
B-roll ratio
beat alignment
zoom/motion rate where supported
```

Use deterministic expected values on small curated fixtures.

---

## 48.2 Semantic extraction quality

Human-review:

```text
hook type
story arc
payoff position
B-roll purpose
tone
```

Use small labeled reference set.

---

## 48.3 Style reproduction

For one source project:

```text
A = baseline plan
B = style-conditioned plan
```

Blind reviewer questions:

```text
Which better matches the intended reference grammar?
Which is more coherent?
Which feels more current?
Which is more watchable?
Which would you post?
```

---

## 48.4 Style usefulness

A style can be faithfully reproduced and still make the vlog worse.

Also rate:

```text
story clarity
authenticity
naturalness
B-roll relevance
caption readability
technical correctness
watchability
user preference
```

---

## 48.5 Grounding regression

Required:

```text
style-conditioned plan must pass all existing grounding/validation gates
```

A style cannot lower evidence requirements.

---

# 49. Recommended first experiment

Do this before provider integration.

## Inputs

Choose:

```text
1 fresh real vlog project
3–5 manually selected references
```

Prefer references reasonably close to the intended academic/research content.

---

## Experiment

### Step 1

Analyze references into structured observations.

### Step 2

Manually or semi-automatically aggregate one style.

### Step 3

Compute style compatibility against current grounded concepts.

### Step 4

Select best concept/style pair.

### Step 5

Compile only currently canonical/executable features:

```text
narrative emphasis
shot pacing
B-roll ratio/placement
J/L cuts where appropriate
voiceover if relevant
basic title/caption policy only if supported
```

### Step 6

Produce:

```text
baseline cut
style-conditioned cut
```

### Step 7

Blind compare.

---

# 50. MVP success gate

Proceed to automated trend discovery only if the first reference-style loop demonstrates at least one of:

```text
clear user preference for styled cut
clear blind-review preference
measurably closer reference grammar without quality loss
useful missing-shot guidance
```

If not:

```text
debug style extraction/compilation first
```

Do not assume discovery volume will fix a weak style compiler.

---

# 51. Recommended phased roadmap

## Phase 0 — current real-footage acceptance

Continue fresh vlog acceptance runs.

Do not let this subsystem hide current daily-loop problems.

Exit gate:

```text
existing baseline workflow is reliable enough that style experiments compare against a meaningful baseline
```

---

## Phase 1 — minimal reference analyzer

Implement:

```text
manual local reference input
technical probe
shot segmentation
pacing statistics
basic audio/beat analysis
structured shot table
semantic narrative analysis using transcript + measured timeline
evidence/provenance tiers
style-observation.v1
```

Caption/motion extraction can be partial if expensive.

Exit gate:

```text
observations are inspectable and broadly correct on 3–5 references
```

---

## Phase 2 — style aggregation

Implement:

```text
style-template.v1
simple/manual aggregation
component structure
provenance
```

Do not build complex clustering yet.

Exit gate:

```text
one reusable style can be derived from multiple references
```

---

## Phase 3 — concept editorial metadata

Add lightweight fields to existing story concepts:

```text
archetype
narrative shape
hook type
dialogue density
visual/B-roll need
payoff properties
```

Exit gate:

```text
metadata is stable enough for style retrieval/matching and does not reduce current concept quality
```

---

## Phase 4 — concept × style matching

Implement:

```text
story compatibility
visual coverage
dialogue compatibility
payoff compatibility
niche relevance
explanations
missing requirements
```

No trend score required yet.

Exit gate:

```text
ranking makes editorial sense across at least several concepts/styles
```

---

## Phase 5 — style-conditioned planner

Add:

```text
style-application.v1
soft constraints
current-capability compiler
style-conditioned plan revision
```

Do not author OpenTake-only style features.

Exit gate:

```text
baseline and styled plan both validate and render
```

---

## Phase 6 — blind A/B

Run:

```text
baseline vs style-conditioned
```

Record:

```text
preference
reference-grammar match
coherence
authenticity
watchability
```

Exit gate:

```text
evidence that style conditioning is useful
```

---

## Phase 7 — UX integration

Add minimal style recommendation into:

```text
Historia
```

and optionally:

```text
Cambiar estilo
```

under Edición.

No new main workspace.

---

## Phase 8 — richer canonical style vocabulary

Only if evaluation indicates value.

Potential:

```text
edit-plan.v2
rich captions
keyframed motion
BGM/SFX roles
ducking
beat structure
```

Must update render/bridge/sync/export/validation together.

---

## Phase 9 — provider bake-off

Now evaluate social/trend providers.

Create benchmark queries such as:

```text
graduate student day in the life
researcher vlog
robotics student
AI researcher
engineering campus life
technical explainer
PhD research progress
```

Score:

```text
relevance
coverage
freshness
API accessibility
price
rate limits
post metadata
historical data
sound data
cross-platform support
terms/retention clarity
```

---

## Phase 10 — Trend Scout

Implement:

```text
provider adapter
metadata normalization
relevance ranking
creator-relative overperformance
clustering
representative selection
trend snapshots
```

Deep-analysis budget:

```text
small exemplar sample
```

not full corpus.

---

## Phase 11 — current-style recommendations

Combine:

```text
trend momentum
niche relevance
concept match
footage feasibility
```

---

## Phase 12 — personal style learning

Use:

```text
chosen/rejected styles
manual OpenTake finishing edits
caption changes
B-roll modifications
music choices
final synced plans
```

---

# 52. Tests to add

Follow current test culture.

Possible files:

```text
app/tests/test_style_observation.py
app/tests/test_style_matching.py
app/tests/test_style_compiler.py
app/tests/test_style_grounding.py
app/tests/test_style_api.py
app/tests/test_style_roundtrip.py
```

Later:

```text
app/tests/test_trend_scoring.py
app/tests/test_trend_provider_contract.py
```

---

# 53. Deterministic unit-test ideas

## Style match

Given:

```text
concept with failure/retry/payoff
style requiring failure/retry/payoff
```

expect high narrative compatibility.

Given:

```text
quiet chronological concept
same style
```

expect lower compatibility.

---

## Missing requirement

Given style:

```text
payoff_required=true
```

and concept:

```text
no evidence-backed payoff
```

expect:

```text
lower payoff score
missing requirement
no fabricated payoff
```

---

## Pacing compiler

Given style target:

```text
median ≈0.9s
```

ensure planner/compiler target influences selection but does not:

```text
cut speech mid-word
exceed evidence/source bounds
violate minimum event length
```

---

## Capability gate

Given style component:

```text
active-word captions
```

and current canonical capability:

```text
unsupported
```

expect:

```text
marked unresolved
not silently authored only in OpenTake
```

---

# 54. End-to-end acceptance test

Example real footage:

```text
morning coffee
walk to lab
robot setup
failed run
debugging
lunch
retry
successful run
walk home
reflection
```

Expected concept:

```text
research_progress
```

Potential styles:

```text
A. Problem → struggle → payoff
B. Quiet academic diary
C. Fast technical explainer
```

Expected behavior:

```text
A scores highest for failure→success concept
B scores highest for chronological day concept
C scores highest for explanatory concept
```

If final selected pair is A, plan should roughly express:

```text
strong hook
lab/robot context
failed run
debugging cutaways
faster retry section
successful run late
reflection ending
```

All events must be grounded.

---

# 55. Failure modes to explicitly guard against

## 55.1 Style causes hallucinated story

Bad:

```text
style expects failure
→ planner invents failure
```

Correct:

```text
style expects failure
→ footage lacks failure
→ lower match / adapt style
```

---

## 55.2 OpenTake-only hidden state

Bad:

```text
style compiler adds zoom keyframes directly in OpenTake
canonical plan cannot represent them
```

Correct:

```text
feature remains unresolved until canonical support exists
```

---

## 55.3 Trend score dominates footage fit

Bad:

```text
viral meme format
+
poor footage compatibility
→ recommended anyway
```

Correct:

```text
high trend
low story/footage fit
→ low overall recommendation
```

---

## 55.4 Exact imitation

Bad:

```text
copy reference script
copy branded text package
copy original graphics
```

Correct:

```text
reuse abstract grammar
```

---

## 55.5 Too many references

Bad:

```text
download 500 posts
VLM-analyze all
```

Correct:

```text
metadata-first
cluster
5–10 exemplars
```

---

## 55.6 Premature provider coupling

Bad:

```text
core style object depends on Exolyt response fields
```

Correct:

```text
provider adapter normalizes to internal candidate model
```

---

## 55.7 UI regression

Bad:

```text
new Trends tab
new Styles tab
new Provider tab
new Analysis tab
```

Correct:

```text
surface recommendation where story/edit decisions already happen
```

---


## 55.8 VLM-as-oracle failure

Bad:

```text
send full reference to a VLM
→ ask "what is the style?"
→ trust every returned timing, technique, and intention
```

Correct:

```text
measure temporal/visual/audio structure deterministically
→ provide structured evidence to the model
→ ask constrained semantic questions
→ retain confidence/provenance
→ aggregate across multiple references
```

The implementation should remain useful even if semantic interpretation is imperfect.

---

# 56. Questions Claude/Codex should answer before coding

## Current architecture

1. Has `video-edit/main` moved beyond `95d9939`?
2. Has `OpenTake/trial` moved beyond `d98634b`?
3. Which capabilities in the matrix above are now stale?
4. Has rich caption or keyframe state become canonical anywhere in `video-edit`?
5. Are new OpenTake round-trip fields now available?

---

## Minimal implementation

6. What is the smallest vertical slice that can produce one `style-observation.v1`?
7. Can existing FFmpeg utilities handle shot/audio measurements without a large dependency?
8. Which semantic extraction should reuse the current multimodal provider stack?
9. Can concept editorial metadata be emitted in the existing writer call without degrading story quality?
10. Should `style-application.v1` be a project artifact under the existing runtime structure?
11. Which reference fields can be measured deterministically with current dependencies?
12. Which fields genuinely require a multimodal model?
13. Should local cut-window classification be part of Phase 1 or Phase 2?
14. How will evidence type and confidence be represented without making schemas cumbersome?

---

## Planner

15. Where should soft style constraints enter `planning.py`?
16. How do we prevent pacing goals from fighting word snapping and grounding?
17. Which existing B-roll selection logic can be conditioned by target ratio?
18. How should payoff-position targets affect concept-to-plan compilation without misleading chronology?
19. Which J/L choices can be generated deterministically from speech/source availability?

---

## Canonical representation

20. Can the MVP remain entirely on `edit-plan.v1`?
21. Which desired features force an `edit-plan.v2`?
22. Is it better to extend v1 compatibly or define v2 explicitly?
23. What migration/test surface would v2 require?
24. Can OpenTake readback preserve every proposed new canonical field?

---

## Evaluation

25. What is the fastest real-footage baseline-vs-style A/B harness?
26. Which current benchmark utilities can be reused?
27. How will blinded outputs avoid revealing which is style-conditioned?
28. What acceptance criterion is sufficient to proceed to Trend Scout?

---

## Trend providers — later

29. Which current provider actually offers the best semantic creator/post discovery?
30. Which provider gives historical velocity or enough snapshots to compute it ourselves?
31. Which gives sound/music identifiers and useful trend metadata?
32. What reference-media access is permitted?
33. Can analysis occur temporarily without long-term media storage?
34. Which terms restrict derivative feature storage?

---

# 57. Suggested implementation order for an autonomous coding agent

If asked to implement rather than only discuss:

```text
1. Verify repository HEADs.
2. Read current status/roadmap and relevant code.
3. Produce a short drift report against this handoff.
4. Propose the smallest vertical slice.
5. Implement only Phase 1 unless explicitly asked to go farther.
6. Add tests.
7. Run current full test suite.
8. Analyze 1–3 real/manual reference fixtures.
9. Save inspectable artifacts.
10. Report what the output gets right/wrong.
11. Do not integrate social providers yet.
12. Do not change edit-plan schema unless the vertical slice truly requires it.
```

If the owner explicitly asks for full implementation, still maintain phase gates and avoid speculative empty infrastructure.

---

# 58. Recommended first deliverable

A strong first coding deliverable would be:

```text
Reference Style Observation MVP
```

with:

```text
input:
  local reference video

output:
  style-observation.v1.json
```

Containing reliably:

```text
Tier A / measured:
  shot boundaries
  shot-duration distribution
  cuts/minute
  audio-energy profile
  beat grid/BPM where music permits
  cut-to-beat timing
  caption geometry/timing where detectable

Tier B / classified:
  basic A-roll/B-roll roles
  caption mode
  simple motion/zoom categories

Tier C / semantic:
  hook type
  narrative beat sequence
  payoff/reveal
  B-roll semantic purpose

Tier D / optional hypothesis:
  higher-level editorial intent
```

The artifact must make these evidence classes visible rather than presenting all fields as equally certain.

Plus:

```text
CLI/test harness
schema validation
small fixture set
human-readable debug report
```

That alone tests the heart of the idea.

---

# 59. Recommended second deliverable

```text
Concept × Style Matching MVP
```

with:

```text
one manually created/aggregated style-template.v1
+
existing project concepts
↓
style-match.v1 per concept
```

UI is optional at first.

The output should explain:

```text
why it fits
why it does not fit
what footage requirement is missing
```

---

# 60. Recommended third deliverable

```text
Style-Conditioned Edit MVP
```

Features limited to current canonical vocabulary:

```text
story emphasis
payoff placement
shot-duration distribution
B-roll ratio/placement
J/L cuts
voiceover if appropriate
```

Output:

```text
baseline plan/render
styled plan/render
blind comparison packet
```

If this is not meaningfully better, stop and iterate.

---

# 61. What should *not* be implemented yet

Unless explicitly requested:

```text
large-scale scraping
custom social crawler
social-media mirror
custom trend ML model
hundreds of downloaded references
provider subscriptions
complex unsupervised clustering
edit-plan.v2 for hypothetical features
separate top-level Trends UI
personal recommendation ML
fully automated posting
music downloading
```

---

# 62. Longer-term opportunity

If successful, this subsystem changes the product from:

> AI that understands and cuts my footage

into:

> AI that understands my footage, proposes grounded stories, understands the current editing language of my creative niche, knows which language actually fits today's material, and increasingly learns how I personally finish videos.

The most important competitive loop may eventually be:

```text
reference/trend intelligence
        ↓
grounded recommendation
        ↓
canonical edit
        ↓
user finishes in OpenTake
        ↓
timeline sync
        ↓
preference learning
        ↓
better next recommendation
```

That feedback loop is more strategically valuable than simply collecting more trend data.

---

# 63. Final architectural principles

Keep these non-negotiable unless evidence justifies a deliberate change.

## 1. Grounding determines content

```text
Evidence decides WHAT happened.
```

## 2. Style determines presentation

```text
Style suggests HOW to tell it.
```

## 3. Trend is only one ranking signal

```text
Popular ≠ appropriate.
```

## 4. Footage feasibility matters

```text
A style cannot demand footage that does not exist.
```

## 5. Missing requirements should become actionable pickup advice

```text
Low fit can sometimes be repaired by recording one small shot.
```

## 6. Reference Style Intelligence must work without social APIs

```text
Manual references first.
```

## 7. Trend Scout supplies references; it does not own style semantics

```text
provider layer is replaceable.
```

## 8. Deep-analyze a few exemplars

```text
metadata first, media second.
```

## 9. Canonical state remains canonical

```text
Do not create OpenTake-only invisible style decisions.
```

## 10. Validate value before expanding vocabulary

```text
baseline vs styled edit first;
rich captions/motion later.
```

## 11. Keep the UI centered on user goals

```text
Historia / Edición / Metraje / Publicar
```

not subsystem internals.

## 12. Learn from actual finishing behavior over time

```text
manual edits are preference evidence.
```

---

# 64. One-sentence target

> Build a reference-driven creative-intelligence layer that extracts reusable editing grammar from a few representative short-form videos, scores that grammar against each grounded story available in the user's real footage, compiles the best compatible story/style pairing into the canonical editing pipeline, and later uses social trend providers only to automate the discovery of worthwhile references.

