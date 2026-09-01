> **Review status (2026-09-01, Claude):** verified against the repo — the
> cited commits are real and correctly described; the analysis is current
> and consistent with our decision records. Adopted: P0 (live edited
> round-trip proof) into the roadmap. Held: durability stays trigger-based
> rather than "SQLite first". Scores are summary rhetoric, not measurements.

# ChatGPT Engineering Assessment and Agent Handoff

**Last external review:** 2026-09-01  
**Reviewer:** ChatGPT / GPT-5.6 Sol  
**Scope:** `vSebas/video-edit` (`main`) + `vSebas/OpenTake` (`trial`)  
**Reviewed through `video-edit` commit:** `59a7892f95c45348e056334ae8d7ac2d31d1c0d0`  
**Purpose:** persistent external-review / agent-to-agent handoff for Claude, Codex, or other implementation agents.

> This file is advisory, not canonical project state. Before implementing anything here, inspect the current code, recent commits, `STATUS_AND_ROADMAP.md`, `TRIAL_OPENTAKE.md`, `EXECUTION_LAYER_PLAN.md`, `bench/RESULTS.md`, and validation records. The repository is moving quickly. If newer code or measured results conflict with this file, prefer the newer evidence and document the reason.

---

# 0. Important update from the newest commits

Several commits landed after the previous external assessment and they materially change the recommended plan.

The previous recommendation treated these as upcoming work:

1. implement timeline → plan synchronization,
2. expose synchronization through the application,
3. integrate OpenTake placement/sync into the normal daily workbench.

Those are now substantially implemented.

The relevant new commits are:

- `3b227f1` — **OpenTake sync endpoints; hybrid loop operational**
  - `POST /api/projects/{id}/opentake/sync`
  - `POST /api/projects/{id}/opentake/sync/apply`
  - full candidate-plan validation
  - revision archive
  - revision-bound, single-use replay protection
  - end-to-end fixture test using the real cleanup readback
  - stale-plan/replay rejection
  - 68 tests passing at that point

- `1d4a4dd` — **OpenTake becomes workbench buttons**
  - production `opentake_bridge.py`
  - server-side placement through MCP
  - `POST /opentake/place`
  - UI buttons for **Enviar a OpenTake** and **Traer cambios de OpenTake**
  - user-visible sync diff and explicit apply action
  - host networking so the app container can reach OpenTake's loopback MCP listener
  - live verification: container placement replaced a 46-clip timeline with the verified 22-clip plan; immediate sync preview reported zero changes
  - 68 tests passing

- `59a7892` — **placement concurrency + networking security hardening**
  - destructive placement is serialized with a non-blocking lock
  - concurrent placement attempts fail clearly instead of interleaving timeline deletion/addition
  - documentation now explicitly states the security tradeoff of Docker host networking

These changes mean the old priorities **P0: build sync** and **P1: integrate sync into the app** are no longer correct.

The next milestone is narrower and more concrete:

> **Prove the complete live edited round trip:** place a real canonical plan into OpenTake from the workbench, make real timeline edits, pull those edits back through the new workbench sync flow, apply the new plan revision, render it with the owned renderer, and verify that the final render exactly reflects the OpenTake changes.

That should now be treated as the immediate finishing proof for the hybrid architecture.

---

# 1. Executive assessment

The project is now in a strong architectural position.

The central division of responsibility looks correct:

> `video-edit` remains the semantic/directorial brain and canonical state owner. OpenTake is the interactive editing surface and granular timeline execution layer. The owned renderer remains authoritative for final pixels. Resolve remains the conventional-editor escape hatch.

This is no longer just a proposed architecture. Important pieces are already demonstrated:

- phone-first media intake
- audiovisual evidence generation
- large-v3 ASR with word timings
- grounded concept generation
- frame-exact plan compilation
- deterministic rendering
- Resolve-editable export
- OpenTake MCP connectivity
- verified plan placement
- transcript-driven ripple cleanup
- OpenTake crash recovery
- explicit plan ↔ OpenTake bridge identity
- fail-closed timeline → plan reconstruction
- preview/apply sync APIs
- revision-bound sync application
- workbench placement/sync UI
- destructive placement serialization

My current assessment:

| Area | Assessment |
|---|---:|
| Product concept | 9/10 |
| Core architecture | 9/10 |
| Grounding / provenance | 9/10 |
| Footage understanding | 8.5/10 |
| Story generation | 8–8.5/10 |
| OpenTake integration | 8/10 for the supported v1 subset |
| Editing expressiveness | 5.5–6/10 |
| Daily-loop maturity | 7.5–8/10 |
| Reliability / persistence | ~6/10 |
| Evaluation discipline | 9/10 |

The main technical question is no longer:

> “Which editor should we use?”

The project has basically answered that.

The main frontier is now:

> **How do we safely expand the canonical edit vocabulary from a mostly paired hard-cut model into a real editing model with dialogue cleanup, detached A/V, B-roll, J/L cuts, richer audio, and localized conversational revisions?**

---

# 2. Product thesis to preserve

The strongest version of this product is not merely:

> “AI-assisted timeline editing.”

It is:

> “Give the system a dump of raw personal footage and optionally a creative direction. It should understand what happened, determine what compelling short-form story exists, create a grounded editable first cut, and let the user refine it conversationally or manually without losing provenance or structured intent.”

Representative workload:

- roughly 20–30 phone videos
- many clips short
- sometimes one or two clips several minutes long
- often an IG/TikTok/Reel-style result around ~90 seconds, but not a rigid duration gate
- vertical-first
- English and Spanish speech
- user may provide:
  - explicit story direction
  - broad creative guidance
  - no direction at all

The autonomous mode is important.

The product should eventually answer:

> “What worthwhile video is hidden in everything I recorded?”

rather than only:

> “Which clips match the idea I already gave you?”

---

# 3. Architecture: keep `video-edit` as brain, OpenTake as editing surface

Do not invert the ownership boundary.

## `video-edit` should own

- project identity and project state
- media identity
- technical inventory
- ASR and word timing
- grounded evidence
- source provenance
- source-context relationships
- concepts and story structure
- edit intent
- canonical edit plan
- plan revisions
- validation
- bridge identity
- timeline readback interpretation
- final-render contract

## OpenTake should provide

- interactive editing surface
- trim / split / move / ripple primitives
- linked A/V editing
- manual finishing
- eventual B-roll execution
- eventual detached audio/video execution
- captions/effects where useful
- editor-side project persistence

## Owned renderer should currently own

- authoritative final pixels
- deterministic composition
- output validation
- known-good color/aspect behavior
- the render path that currently beats OpenTake on real footage

## Resolve should remain

- conventional-editor escape hatch
- independent handoff path
- useful reference/fallback

The intended flow is now:

```text
RAW MEDIA + PROMPT
        ↓
video-edit analysis / evidence / ASR
        ↓
concept + story planning
        ↓
canonical edit-plan.json
        ↓
OpenTake placement through MCP
        ↓
manual / agent timeline edits
        ↓
OpenTake readback
        ↓
fail-closed sync preview
        ↓
validated candidate plan revision
        ↓
explicit apply
        ↓
owned final renderer
```

That is a good architecture.

---

# 4. The OpenTake trial decision still looks correct

The trial generated a useful empirical result rather than an architectural opinion.

OpenTake succeeded as an editor:

- external MCP worked
- real plan placement worked
- source trims were verified
- linked A/V pairing was verified
- transcript-driven ripple cleanup worked
- recovery after process termination worked
- manual finishing was viable

But OpenTake lost as the renderer on the tested real project:

- output color looked worse to the owner
- 16:9 footage was incorrectly squeezed
- the trial cut lacked the title because the adapter did not place it
- export was dramatically slower than the owned renderer

Therefore:

```text
OpenTake = editing surface
owned FFmpeg renderer = final pixels
```

is the right current split.

Do not spend near-term effort trying to make OpenTake the canonical project representation or authoritative renderer unless a later real output bake-off shows a clear win.

---

# 5. The new timeline → plan synchronization is the correct keystone

This is the strongest architectural progress since the old handoff.

Without readback sync, the hybrid architecture would eventually have two truths:

```text
edit-plan.json
        ≠
actual finished OpenTake timeline
```

That would break:

- rendering
- provenance
- later revisions
- user trust
- learning from finishing edits

The current direction avoids that.

`opentake_sync.py` reconstructs a candidate canonical plan from supported OpenTake timeline states and **fails closed** when it cannot do so safely.

This philosophy should be preserved aggressively:

```text
state is representable and identity is unambiguous
        ↓
convert + validate

state is ambiguous / unsupported
        ↓
STOP and explain
```

Never silently guess how an editor operation maps back to canonical state.

---

# 6. Good properties of the current sync design

The current implementation validates or rejects important invariants such as:

- plan revision mismatch
- FPS mismatch
- project-dimension mismatch
- unknown media refs
- missing linked audio
- ambiguous linked audio
- linked A/V geometry mismatch
- source ranges escaping the original bridged envelope
- unsupported speed/reverse fields
- ambiguous descendant attribution

Supported differences currently include at least:

- unchanged
- moved
- trimmed
- deleted
- split

This is a sensible v1 synchronization subset.

The system also now has revision-bound application and replay protection, which is exactly what should exist for a destructive canonical-state update.

Keep this safety bias.

---

# 7. Persisted `opentake-bridge.v1` is important

The bridge records explicit identity between the canonical plan and the OpenTake timeline.

Conceptually:

```text
asset_id ↔ mediaRef
event_id ↔ clipId
link_group_id
source envelope
timeline start
plan revision
```

That is much safer than later trying to reconstruct identity using filename, position, or source geometry.

Future bridge versions may eventually need additional identity such as:

- OpenTake project id
- timeline id
- track ids
- descendant lineage
- accepted revision id
- source content fingerprint

But do not add fields speculatively. Add them when a concrete ambiguous state appears.

---

# 8. New immediate milestone: live edited round trip

The newest commits change the priority substantially.

The system has now live-verified:

- placement from the container/workbench path
- correct replacement of an existing OpenTake timeline
- zero-diff sync preview immediately after placement

It has fixture/end-to-end tested:

- real cleanup readback → split detection
- 2314-frame reconstructed candidate
- revision archive
- stale-plan rejection
- replay rejection

What still deserves a deliberate proof is:

> **live OpenTake edits → workbench sync preview → apply → owned render**

Use a real project.

## Suggested live test

1. Place canonical plan revision N from the workbench.
2. Confirm zero-diff readback.
3. Make controlled edits in OpenTake:
   - one transcript-driven ripple deletion
   - one start trim
   - one end trim
   - one split
   - one deletion
   - one move
4. Use **Traer cambios de OpenTake**.
5. Confirm the workbench diff matches the actual edits.
6. Apply the candidate revision.
7. Confirm plan revision N+1 is archived/installed.
8. Render with the owned renderer.
9. Compare the final render against the intended OpenTake timeline.
10. Verify source identity, audio alignment, total duration, trims, and ordering.

If this passes cleanly, I would consider the supported v1 hybrid loop **operational rather than experimental**.

---

# 9. The workbench integration is now implemented; do not keep treating it as future work

Earlier recommendations said to move OpenTake placement and sync out of trial scripts and into the normal application.

That is now done.

The workbench exposes:

- **Enviar a OpenTake**
- destructive replacement confirmation
- **Traer cambios de OpenTake**
- visible diff list
- explicit apply action

The production bridge now lives in `app/video_app/opentake_bridge.py`, not only trial tooling.

Future agents should therefore stop spending roadmap effort on “integrate OpenTake into the app” as a generic task.

The useful questions are now narrower:

- what OpenTake edit subset is supported by round-trip sync?
- how does the UI communicate unsupported edits?
- can synchronization be extended safely when new edit semantics are added?
- how should richer plan semantics map into OpenTake?

---

# 10. Placement serialization was a good necessary hardening

The latest commit identified a real destructive race:

```text
placement A: remove clips
placement B: remove clips
placement A: add clips
placement B: add clips
```

That could shred the active OpenTake timeline.

A global non-blocking placement lock is a reasonable current fix.

Keep destructive editor operations serialized until there is a stronger transactional model.

If later multiple projects/OpenTake sessions are supported simultaneously, this may evolve from a global lock into a per-editor-project lock.

For now, the simpler global lock is appropriate.

---

# 11. Host networking introduces a real security/operational tradeoff

The app container now uses host networking so it can reach OpenTake's loopback-only MCP listener.

That makes the integration much smoother, but means the container loses some network isolation.

This does not invalidate the architecture, but agents should treat it as an explicit operational constraint.

Preserve:

- localhost default bind for the app
- token gate when exposing beyond localhost
- OpenTake MCP bearer authentication
- explicit destructive confirmations
- project identity / revision guards

Do not casually widen network exposure merely for convenience.

Eventually a cleaner OpenTake external endpoint or Unix-socket style bridge could reduce the need for host networking, but this is not a current product priority.

---

# 12. Do not rush `edit-plan-v2` before the v1 live round trip is fully proven

The previous external memo recommended moving quickly toward a richer plan schema.

Given the newest progress, I would now be more conservative.

`edit-plan.v1` is already more capable than a pure clip list. It contains:

- video tracks
- audio tracks
- caption tracks
- title tracks
- independent source/timeline times
- reframe data
- transitions
- volume

The current production behavior is still much narrower, mainly one paired video/audio sequence.

That is acceptable for the first complete round-trip proof.

Do not simultaneously debug:

- synchronization
- multitrack semantics
- B-roll
- detached A/V
- J/L cuts
- schema migration
- renderer changes
- editor mapping changes

First close the v1 loop on real edits.

Then expand representation intentionally.

---

# 13. Editing expressiveness is now the main product-quality bottleneck

Once the v1 loop is operational, the project needs to move beyond:

```text
video  ████ ████ ████ ████
audio  ████ ████ ████ ████
       always paired
```

into something like:

```text
A-roll audio
──────────────────────────────────

A-roll video
──────────             ───────────

B-roll video
          ─────────────

music
──────────────────────────────────

captions
──────────────────────────────────
```

This unlocks:

- B-roll over retained dialogue
- J cuts
- L cuts
- voiceover
- independent music
- better pacing
- visual replacement without losing the best spoken line

This is probably a larger quality gain than changing the story writer again.

---

# 14. Dialogue cleanup should become the next user-facing editing feature

The trial already proved the underlying mechanics:

```text
large-v3 transcript
      ↓
conservative cleanup candidates
      ↓
reviewed ranges
      ↓
OpenTake ripple_delete_ranges
      ↓
linked A/V cleanup
```

Now productize it.

Candidate categories:

- filler
- false start
- repeated phrase
- duplicated take
- long dead air
- obvious restart

Support Spanish and English.

Do not blindly delete every filler word or hesitation.

Natural speech quality matters more than seconds removed.

A useful interface could be:

```text
Suggested dialogue cleanup

[x] 00:31.2–00:31.7  "este"        filler
[x] 00:44.8–00:46.0  false start   high confidence
[x] 01:02.1–01:03.5  dead air      1.4 s
[ ] 01:18.4–01:19.1  "um"          optional

Estimated reduction: 4.1 s

[Apply cleanup]
```

Important architecture:

> semantic decision → explicit range operation → deterministic OpenTake edit → readback → canonical sync

not:

> semantic decision → regenerate the whole story

---

# 15. Atomic natural-language editing should follow

Once the round-trip and cleanup paths are trustworthy, small user requests should stop invoking global replanning.

Example:

> “Remove the second `este`.”

Desired flow:

```text
resolve transcript occurrence
      ↓
source range
      ↓
ripple deletion
      ↓
OpenTake readback
      ↓
sync preview
      ↓
validated plan revision
```

Example:

> “Make that shot shorter.”

Desired flow:

```text
resolve event
→ trim operation
→ readback
→ plan sync
```

Example:

> “Show the food while I’m talking about it.”

Eventually:

```text
retain A-roll audio
→ retrieve grounded food visual
→ place B-roll over the same timeline range
→ preserve underlying dialogue
```

Useful command vocabulary eventually includes:

- trim
- split
- delete
- ripple delete
- remove transcript range
- replace visual
- overlay B-roll
- move
- shorten
- extend
- reframe
- add/remove caption
- voiceover placement
- set gain
- duck music
- transitions

Small request → small reversible edit.

---

# 16. B-roll / detached A/V is the next major expressive milestone

This capability changes the quality ceiling of the editor.

Today, if the best spoken sentence comes from visually weak footage, the system often has to choose between:

- keep the good sentence and show bad picture
- lose the good sentence

A better editor can do:

```text
A-roll audio:
"The market was much more crowded than I expected."
─────────────────────────────────────────────

Video:
speaker ─── crowded market B-roll ─── speaker
```

The existing grounding system is especially valuable here.

The planner already knows things like:

```text
speech evidence:
"market was crowded"

visual evidence:
market wide shot with dense crowd at 02:14–02:18
```

That enables **story-conditioned visual support retrieval**, which is more useful than generic semantic search.

Prioritize this over feature-count additions such as avatars, generated video, object removal, or flashy transitions.

---

# 17. The ~8-second semantic chunk issue: revised position

The original concern remains real, but the diagnosis should be precise.

`visual.py` still uses:

```python
MAX_SHOT_SECONDS = 8.0
```

with deterministic scene detection, subdivision, and independent VLM calls.

Potential weaknesses:

- setup/payoff may cross boundaries
- question/answer may cross boundaries
- speech can refer to later visuals
- reaction context may come from earlier footage
- API overhead grows with long sources
- local captions are a lossy compression before story reasoning

But the planner already receives the full evidence set and transcript, so it is not simply “forgetting chronology.”

The more accurate concern is:

> **relationship extraction can be lossy at short semantic boundaries.**

That distinction matters.

---

# 18. The implemented `source-context.v1` experiment was the right response

Rather than deleting the proven short-window path, the project added a source-context sidecar.

That sidecar:

- prefers whole-source input when it fits
- otherwise uses long overlapping windows (currently up to ~180 s)
- extracts source-level events
- extracts explicit relationships:
  - `setup_payoff`
  - `question_answer`
  - `action_reaction`
  - `reference`
  - `before_after`
  - `speech_visual`
- anchors broad events back into fine evidence where possible

This is a better experiment than simply changing `MAX_SHOT_SECONDS = 8` to `60`.

The first live run produced broad context over all tested assets, but the owner preferred the baseline concepts in the first A/B.

Keeping `source-context` dormant by default is therefore correct.

---

# 19. Do not interpret the first source-context A/B too strongly

The correct conclusion is:

> “This first source-context implementation did not improve the selected concepts on this tested footage day.”

It is **not**:

> “Global context is useless.”

or:

> “8-second chunks are optimal.”

Continue testing source-context on footage where long-range relationships should matter:

- long dialogue
- delayed payoff
- callback
- question followed by later answer
- explanation followed by visual demonstration
- joke followed by later reaction
- recurring person/object reference
- event spanning many shots

Separate evaluation into three questions:

1. Did the context extractor recover the intended relationship?
2. Did the writer use that relationship correctly?
3. Did the final rendered video improve?

Those failure modes should not be conflated.

---

# 20. Near-term semantic architecture: fine evidence + broad relationship sidecar

Given current evidence, keep both resolutions:

```text
                  ┌── fine grounded shot/moment evidence
source footage ───┤
                  └── broad source/event/relationship context
                               ↓
                          story planner
```

Later, if useful:

```text
selected story
     ↓
targeted refinement
     ↓
exact editing ranges
```

Do not use broad-source timestamps as frame-accurate edit boundaries unless separately validated.

The broad pass should provide relationships and retrieval hints.

The fine path should continue providing precise grounded ranges.

---

# 21. Autonomous story discovery remains strategically important

The tool should support both:

## Directed

> “Make this about our first day in X.”

## Autonomous

> “I don't know what to make. Find the strongest story.”

Eventually autonomous concept search should be systematic enough to generate meaningfully different possibilities rather than superficial variations.

Possible concept families when actually supported by footage:

- chronological narrative
- thematic narrative
- strongest single event
- humorous/personality cut
- visual montage
- user-directed concept

Do not force every family.

Do not add a mandatory expensive critic/ranker stage purely for architectural elegance.

If one is added, benchmark whether it actually improves chosen rendered outputs enough to justify latency/cost.

---

# 22. Audio production should follow detached A/V

Once the timeline can represent independent audio/video, useful audio improvements include:

1. clip-level speech leveling
2. basic denoise where clearly beneficial
3. music track
4. speech-aware ducking
5. voiceover placement
6. gain envelopes

Avoid overprocessing personal phone audio.

Audio intent should be represented in the neutral plan, not hidden as renderer-specific magic.

---

# 23. Captions should eventually be first-class timeline objects

Current timeline-aligned SRT output is useful.

For polished social editing, captions should eventually support:

- editable text
- transcript-grounded timing
- styling
- safe-area placement
- phrase grouping
- later emphasis/highlighting if justified

This is below round-trip, dialogue cleanup, and B-roll in priority.

---

# 24. Geometry / framing remains practical quality work

Phone-first footage needs robust handling of:

- rotation metadata
- portrait/landscape sources
- crop/fill
- center framing
- manual overrides
- later subject-aware reframe if useful

The OpenTake trial already exposed a real 16:9 aspect-fit bug in its renderer.

Do not prioritize sophisticated subject tracking before basic geometry is reliable everywhere.

---

# 25. Reliability / persistence still lags the semantic stack

The job layer is still in-process and memory-backed.

That is increasingly out of proportion with the sophistication of the rest of the project.

A service restart should not erase expensive job state or force repeated analysis.

A reasonable next durability layer remains:

```text
SQLite
+
content-addressed artifacts
+
idempotent/resumable operations
```

Likely durable entities:

- projects
- jobs
- assets
- analysis runs
- transcripts
- semantic evidence
- source context
- concepts
- plan revisions
- OpenTake bridge state
- sync previews/diffs
- render outputs

Do not jump to Redis/Celery/Kubernetes unless real workload demands it.

---

# 26. Caching / dedup should remain medium-high priority

The same raw footage may be reused for multiple story ideas.

Unchanged footage should not be re-understood repeatedly.

Useful cache identity dimensions:

- source content hash/identity
- source range
- model/provider
- prompt/schema version
- analysis configuration

Cache broad source context and fine observations independently.

The recent telemetry/source-context work is already moving in the right direction.

---

# 27. Cloud migration remains low priority

Do not move the whole application to cloud just because cloud sounds faster.

Current reality:

- primary VLM is already remote
- writer is remote
- source-context inference is remote
- normal ASR path already uses local large-v3 CUDA
- owned renderer is currently fast and preferred
- OpenTake must remain local/interactively visible anyway

A cloud rewrite would add operational complexity without obviously solving the main product bottleneck.

Cloud may later help with:

- durable jobs
- remote storage/intake
- multiple concurrent projects
- fallback compute
- optional render workers

But the current engineering return is much higher in editing semantics.

---

# 28. OpenTake fork strategy should remain narrow

The fork modifications so far are appropriately tied to real blockers:

- optional Whisper backend on Arch
- GTK/main-thread export fix
- optional NVENC
- persistent frame decode server replacing process-per-frame resolver decoding
- follow-up frame-server hardening

Keep this pattern.

Do not turn the fork into the location for product-specific semantics.

Story logic, cleanup policy, grounding, user intent, and provenance belong in `video-edit`.

Prefer upstreamable OpenTake fixes when the defect is generic.

---

# 29. OpenTake project-lifecycle automation remains a later gap

External editing tools are usable, but create/open/save/export lifecycle operations have historically been GUI-oriented.

The current workflow can tolerate this.

For full “upload footage → automatically prepare first edit” automation, controlled lifecycle APIs may eventually be useful, particularly:

- open/create scratch project
- save

OpenTake export is not urgent because the owned renderer remains authoritative.

Do not weaken project-safety boundaries casually.

---

# 30. Preserve the project's empirical development culture

One of the strongest aspects of this repo is the emerging loop:

```text
hypothesis / candidate model / candidate architecture
        ↓
implementation or benchmark
        ↓
real output
        ↓
controlled / blind evaluation
        ↓
keep, revise, or reject
```

This has already informed:

- perception model
- ASR model
- writer model
- OpenTake adoption as editing surface
- rejection of OpenTake as current final renderer
- source-context remaining off by default

Keep this discipline.

The important metric is not architectural elegance.

It is:

> **Does a real user's resulting video become better, faster, safer, or easier to finish?**

---

# 31. Updated priority order after the newest commits

The newest commits materially change the roadmap.

## P0 — Live edited round-trip proof

**Not:** build sync.  
**Not:** add workbench buttons.  
Those exist.

Now prove:

```text
workbench place
→ actual OpenTake edits
→ workbench sync preview
→ apply revision
→ owned render
→ parity check
```

This is the immediate milestone.

## P1 — Productize dialogue cleanup

Move transcript-driven filler/dead-air/false-start cleanup from trial capability into the normal daily workflow.

Include reviewability and conservative defaults.

## P2 — Define the next canonical edit semantics from actual requirements

Design the minimum extension necessary for:

- independent A/V
- B-roll overlay
- J/L cuts
- voiceover

Do not create an oversized `edit-plan.v2` speculative schema.

Prefer a small, testable extension driven by one real B-roll-over-speech use case.

## P3 — Execute richer semantics through OpenTake + owned renderer

For each new semantic primitive, require:

- canonical representation
- OpenTake placement
- OpenTake readback behavior
- fail-closed sync policy
- owned-render behavior
- regression tests

## P4 — Atomic natural-language edits

Small user request → explicit edit operation → timeline → sync.

Avoid whole-plan regeneration for local edits.

## P5 — Audio / caption / framing polish

Once the timeline vocabulary supports them cleanly.

## P6 — Durable jobs + artifact cache/dedup

SQLite-scale first.

## P7 — Continue source-context experiments on hard long-range footage

Keep fine evidence as the default until broader context proves a consistent win.

## P8 — Improve autonomous story search

Do this when the editor can actually execute richer story structures.

A smarter director has limited value while the editor vocabulary remains narrow.

---

# 32. What I would explicitly NOT prioritize now

Avoid major work on:

- full cloud rewrite
- replacing OpenTake with another editor without new evidence
- making OpenTake canonical
- making OpenTake final renderer before it wins an output comparison
- avatars
- voice cloning
- generated video as the default source
- object removal
- multicam
- elaborate motion graphics
- advanced color grading
- giant distributed infrastructure
- flashy transition libraries
- replacing the fine 8-second evidence pipeline merely because long context sounds better
- building a huge plan-v2 schema before the live v1 loop is proven

---

# 33. Concrete P0 acceptance test

Use one representative real vlog project.

## Baseline placement

1. Ensure the correct OpenTake scratch project is open.
2. From the `video-edit` workbench, click **Enviar a OpenTake**.
3. Confirm destructive replacement.
4. Require placement verification to succeed.
5. Immediately run **Traer cambios de OpenTake**.
6. Require zero semantic changes.

## Perform real edits in OpenTake

Perform at least:

- one intra-clip ripple deletion
- one start trim
- one end trim
- one split
- one full event deletion
- one moved event

Do not include speed/reverse/multitrack operations that v1 intentionally rejects.

## Pull changes back

1. Click **Traer cambios de OpenTake**.
2. Verify the UI diff exactly matches the edits.
3. Confirm unchanged events are not falsely reported as changed.
4. Apply the candidate revision.

## Canonical validation

Require:

- new revision number
- previous revision archived
- grounding validator passes
- replay protection works
- stale-plan apply fails
- bridge revision semantics remain coherent

## Final render

Render through owned FFmpeg path.

Check:

- clip order
- trim points
- deleted content
- split content
- shifted timeline positions
- audio continuity
- total duration
- title/captions unaffected where expected

If the render matches the intended OpenTake edit, the v1 hybrid loop has achieved the central architecture proof.

---

# 34. Concrete dialogue-cleanup acceptance test

Use a real dialogue segment containing:

- Spanish filler (`este`, `pues`, etc.)
- English filler (`um`, `uh`)
- false start
- repeated phrase
- natural pause that should be preserved
- dead air

Require:

- source-timestamped candidate range
- exact transcript evidence
- cleanup category
- confidence/reason
- reviewability before applying
- word-boundary-safe cuts
- linked A/V preservation
- live OpenTake application
- timeline → plan sync
- owned render

Evaluate the resulting speech by listening.

The metric is not maximum duration reduction.

It is:

> Does it sound like a competent human editor cleaned the sentence without making it robotic?

---

# 35. Concrete B-roll milestone after plan extension

Once detached A/V exists, test one simple meaningful case.

A-roll speech:

> “The market was much more crowded than I expected.”

Grounded B-roll:

> a source range showing the crowded market

Desired timeline:

```text
A-roll audio
────────────────────────────────

speaker video
──────────                ───────

market B-roll
          ────────────────
```

Acceptance:

- A-roll dialogue remains continuous
- B-roll source audio is muted unless explicitly wanted
- both media ranges retain provenance
- OpenTake placement is correct
- owned render is correct
- sync behavior is explicitly defined

This one feature is a more meaningful quality milestone than adding many generic effects.

---

# 36. Long-term opportunity: learn from finishing edits

The timeline-readback architecture creates the foundation for learning user editing preferences later.

```text
AI initial plan
      ↓
manual/agent OpenTake finishing
      ↓
sync diff
      ↓
structured record of user corrections
      ↓
editing preference model / heuristics
```

Potential learned preferences:

- clip duration
- pacing
- filler-removal aggressiveness
- jump-cut tolerance
- B-roll frequency
- framing
- caption style
- title use
- music level
- categories of shots frequently removed

Do not build this until readback lineage is reliable.

But avoid throwing away the information needed to learn later.

---

# 37. Notes to Claude/Codex implementation agents

1. **Re-check recent commits before acting.** This document can become stale quickly.
2. **Do not treat old roadmap tasks as open if code already landed.** The newest commits already completed major parts of the OpenTake sync integration.
3. **Prefer measured project decisions.** If this review conflicts with a real bake-off, inspect the evidence.
4. **Preserve fail-closed synchronization.** Ambiguity should not mutate canonical state.
5. **Keep milestones narrow.** Do not turn “live v1 round-trip proof” into “also implement full multitrack v2.”
6. **Keep product semantics in `video-edit`.** OpenTake fork patches should stay generic where possible.
7. **Test real output, not only schemas.** Final rendered video quality is the product metric.
8. **When rejecting an external recommendation, document the reason with code/test/benchmark evidence.**
9. **Do not re-open settled architecture questions without new evidence.** The hybrid editor/renderer split is currently well supported.
10. **Use the new workbench integration as the production path.** Trial scripts are now mainly verification/debugging tools.

---

# 38. Current external-review conclusion

The project should continue along its present architecture rather than undergo another redesign.

The architecture has converged toward a strong division of labor:

```text
RAW PHONE FOOTAGE
       ↓
video-edit evidence + ASR + optional source context
       ↓
grounded story planning
       ↓
canonical edit plan
       ↓
OpenTake editing surface
       ↓
readback + fail-closed synchronization
       ↓
canonical plan revision
       ↓
owned final renderer
       ↓
review video + editable workflow
```

The newest commits remove two of the largest architectural uncertainties:

- timeline → plan sync exists,
- placement/sync are integrated into the actual workbench.

Therefore the next frontier is not more architecture work for its own sake.

It is:

> **prove the complete live edited round trip, then make the editor substantially more expressive.**

Updated practical order:

1. live edited OpenTake → plan → owned-render round-trip proof
2. productized dialogue cleanup
3. minimal detached A/V + B-roll semantics
4. J/L cuts and richer multitrack execution
5. atomic natural-language edits
6. audio/caption/framing polish
7. durable jobs and cache/dedup
8. continue source-context experiments on footage that actually requires long-range relationships
9. improve autonomous story search after the execution layer can realize richer stories

If those pieces land cleanly, the project will be close to the intended product: upload a day's footage, let the system understand what happened and what story is worth telling, get a credible grounded first cut, then refine it manually or conversationally without losing a safe, inspectable canonical representation of the edit.
