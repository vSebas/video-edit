# AI-Assisted Video Editing — Status and Roadmap

**Updated:** 2026-09-03
**Current phase:** Full daily loop operational and phone-first. Footage
reaches the tool from the iPhone (browser upload with live progress,
Tailscale from anywhere, or a Google Drive VlogInbox that carries title and
nota/prompt); analysis is audiovisual (gemini-3.6-flash) with GPU Spanish
ASR; stories are written by deepseek-v4-pro (blind video-screening
verdict, retained blind against gpt-5.6-sol); cuts are frame-exact with
word-snapped edges and verified DaVinci exports with DNxHR proxies. The
ratified execution direction is hybrid: OpenTake edits over MCP, the owned
renderer produces final pixels, and Resolve remains the escape hatch. That
direction is fully integrated: placement, timeline-to-plan sync (including
B-roll, voiceover, and J/L divergent pairs), dialogue cleanup, and instruction
edits are all workbench buttons; OpenTake orchestration is end-to-end LIVE
(2026-09-03: open/create, auto-import, canvas-align, place, durable save, GUI
nav). **The edit-plan.v1 vocabulary was materially expanded (2026-09-03):**
first-class editable **captions**, background **music** (recommend + optional
burned bed with ducking), and light **transitions** (open/close fades and
per-cut dips) — the cut is no longer a flat hard-cut clip reel. See "edit-plan.v1
capabilities" below. The morning-routine POC fixture and all Phase 2A archive
machinery were removed at the user's request (2026-08-18), and the POC directory
itself on 2026-08-19 — its render, export, and validation scripts now live in
`app/pipeline/` and its schemas in `app/schemas/`, where the app actually uses
them.

**Product target (2026-08-04):** daily personal vlogs, a few minutes each,
from raw phone footage. Every edit must produce BOTH a rendered review video
and an editable export for a conventional editor. Routine evidence is
auto-approved under audited policies; human review concentrates on
risk-flagged claims and creative choices (Phase 2A's per-observation review
machinery was fully removed 2026-08-18 along with the benchmark fixture).

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
- OpenTake is the adopted editing surface, but `edit-plan.json` remains
  canonical and the owned renderer remains authoritative for final pixels.

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
Approved neutral edit-plan.json (canonical)
       /              |                 \
      v               v                  v
 Owned final render   OpenTake over MCP   OTIO/XMEML -> Resolve
                      editing + cleanup   escape hatch
                             |
                             v
                      timeline readback
                             |
              timeline->plan sync (LIVE: revision-guarded)
```

### Best-of component strategy

The owned workbench is the product boundary; everything else is a
replaceable adapter behind it. What actually carries load today, after the
bake-offs in `bench/RESULTS.md`:

- **Owned workbench:** project state, asset identity, evidence schemas,
  provenance, validation, approvals, planning, and the neutral edit plan.
- **gemini-3.6-flash:** audiovisual shot and moment description, on
  audio-carrying segments.
- **faster-whisper large-v3 (local, CUDA):** Spanish/English word timings
  that also snap cut edges to word boundaries.
- **deepseek-v4-pro:** concept and revision writing.
- **FFmpeg:** deterministic rendering behind the edit plan.
- **OpenTake beta.5 fork:** adopted editing surface over external MCP;
  placement (incl. track creation, voiceover, J/L re-tiling), sync,
  and transcript-driven cleanup are integrated workbench features.
- **OTIO and FCP7 XMEML:** Resolve escape-path handoff, import-verified in
  DaVinci Resolve.

Retired after evaluation rather than kept as options: Vidi (not competitive
on our grounding bench), TwelveLabs Marengo/Pegasus (specialist retrieval
scored below the general leader, and their platform rejects short or
low-resolution vlog clips), and OpenStoryline/Crayotter, which contributed
workflow and artifact ideas but no code. The reasoning is in
`bench/RESULTS.md` and git history.

Every component connects through an adapter, which keeps incompatible
licenses and optional research models from becoming inseparable from the
core.

### OpenTake orchestration — end-to-end LIVE (2026-09-03)

"Colocar en OpenTake" now drives the fork over external MCP with no manual
steps: it opens (or creates) the vlog's own OpenTake project, auto-imports
the footage the cut needs (under a user-granted path root), aligns the
canvas to the plan, places the cut, saves it durably, and the GUI
navigates straight to the loaded timeline. Reaching this took nine fork
defects fixed and reviewed (identity turn guard, inner-dispatch guard,
error redaction, gate admission, path authority, canvas settings, cover
authority, the save-project deadlock, HEVC cover decode) plus the GUI
navigation, and a personal-path leak purged from the public fork history.
Placement is idempotent (open-not-create, import-only-missing,
clear-then-place) — repeated Colocar never duplicates. The `.opentake`
bundle IS the project store (project.json = timeline, media.json =
library); there is no separate database.

### edit-plan.v1 capabilities (current surface, 2026-09-03)

`edit-plan.v1` is no longer the flat hard-cut compiler the 2026-08-20 audit
described. It stayed at v1 and grew additively (no v2 — a breaking migration is
reserved for genuinely incompatible changes). What the canonical plan can express
and what actually renders today:

- **Tracks:** video (primary + optional B-roll overlay), audio (primary +
  optional voiceover + optional music), caption, title. Consumers select tracks
  by ROLE, and the validator enforces exactly one primary audio, first.
- **Cuts:** frame-exact source trims, word-snapped edges, per-clip `volume_db`,
  auto-detected rotation.
- **Captions** (first-class, editable): seeded from ASR, grouped into readable
  lines, burned via `subtitles`, correctable per line through `/plan/op`
  (`edit_caption`/`remove_caption`) — corrections are `user_authored` and the LLM
  cannot author caption text. Captions ripple with edits, regenerate on
  OpenTake sync-back, and carry `caption_source` so a correction survives a
  revision only on proven same-footage identity.
- **Music** (recommend + bed, default recommend): a `recommended` annotation
  (vibe / measured BPM / energy) to add natively when posting, or a burned `bed`
  looped and ducked under speech. Ops `set_music_bed` / `remove_music`.
- **Transitions** (geometry-preserving): plan-level open/close fades
  (`transitions`, video-only) defaulted on every cut, and per-cut `fade_black` /
  `fade_white` dips at real seams. Ops `set_transition` / `set_fades`. `dissolve`
  (true crossfade) is deferred — it needs timeline overlap — and the renderer
  fails loudly on it rather than faking a cut.
- **B-roll / voiceover / J-L cuts:** overlay a cutaway over held audio; place a
  voice note on a ducked voiceover track; desync a scene's audio from its picture.
- **Editing:** a closed set of 17 atomic ops (`/plan/command` picks one via the
  LLM; `/plan/op` applies a deterministic one directly), each computed and
  bounds-checked in `plan_ops.py`; the LLM only chooses the op.

Style hooks present in the schema but not yet PRODUCED (dormant, not wired):
event-level `caption_style` (the renderer has a full per-cue ASS path, but no
producer sets the field yet), and `reframe` fill/scale for true
subject-fill vertical framing (only `fit`/letterbox is produced today). Both are
schema-ready for when a producer needs them.

Known consumer gaps from the 2026-09-03 deep check (honest, not yet fixed):
OpenTake sync-back does not re-anchor the **title track** after a rearrange
(the same class fixed for captions), and does not re-fit the **music bed /
voiceover ducking** on a duration change. OpenTake placement and OTIO/XMEML
export carry cut geometry only — transitions, music, and reframe stay render-side
(the MP4 has them); these drops are by design but silent.

## Standing Today

### Third-party handoff memo, reviewed (2026-08-31)

`video_edit_consolidated_engineering_handoff.md` (a ChatGPT-written
assessment) was fact-checked by Codex at max reasoning against the code and
decision records. Verdict: a useful hypothesis memo, not a handoff — its
central diagnosis (planning stronger than execution) is right and already
ours; it is materially behind the repository (proposes as new what is
already decided or fixed, misses the grounding gates, the blind writer
bench, the live OpenTake trial and its fork patches, and the Aug-19
hardening), and overstates the planner's blindness — the planner sees the
full evidence set plus the complete transcript in one context, so the real
gap is lossy relationship *extraction* at chunk boundaries, not lost
chronology.

Adopted from it: (1) a `source-context.v1` experiment — a derived
source/event/relationship evidence sidecar anchored to existing evidence
ids, benchmarked as *current + sidecar* against current-only before any
retrieval-gated redesign; (2) VLM telemetry (unique-seconds and token
accounting); (3) relationship-annotated long-dialogue footage in the
acceptance corpus; (4) range/model/prompt identity folded into the planned
content-addressed artifact design. Rejected: replacing the short-window
path unmeasured, whole-source Gemini timestamps as edit boundaries (bench:
coarse containment good at ≤63 s, editing precision variable; 1 FPS
sampling), a blocking critic stage, cloud GPU ASR, the memo's P0-P11 order,
OpenTake as sole canonical editor, and any fixed ~90 s acceptance gate.
Full review preserved at the top of the memo file.

The source-context pass and telemetry landed on 2026-09-01. Its first live
run covered 39/39 video assets in 39 calls, producing 110 events and 50
relationships with 109 events anchored to fine evidence; telemetry recorded
one retry, 1,778.666 unique source seconds, and 888.348 aggregate call-seconds.
The owner preferred the baseline concepts in the first A/B, so the sidecar is
dormant by default. The retained A/B JSON exposes the treatment flag and keeps
the key beside the sets, so that n=1 result is directional rather than a
sealed blind result; the next run must use a sanitized judge packet.

### External editors: capability audit (2026-08-20, supersedes the first pass)

A first pass concluded "adopt neither" on portability and interchange
grounds. That was right about adoption and wrong about weighting: it barely
examined what the two tools can actually *do*. A second, deliberately
adversarial Codex audit ("do not defend the incumbent") re-derived the
answer from both source trees.

**Verdict: better as editors, worse at the actual job.** OpenTake and
Palmier Pro are materially better editing systems than ours — "not close".
Our editor is a flat hard-cut compiler: one video track, one linked audio
track, one title, fit/pad framing, global loudness normalisation. Neither,
however, has any story layer or phone intake, and neither runs the daily
job: Palmier is macOS-only, OpenTake has no shipped Linux build, a much
smaller ASR (whisper.cpp `ggml-base`, ~142 MB, against our large-v3 on
CUDA), and no grounded proposal artifact.

Corrections to claims made earlier in this project, from reading their
source rather than their docs:

- OpenTake advertises 60 agent tools; only 38 are unconditional, and
  `smart_reframe` is explicitly gated off because its backend does not
  exist.
- Its SigLIP2 semantic search ships placeholder model URLs and hashes, so a
  fresh install cannot enable visual search. Palmier's equivalent is real.
- `auto_cut_to_beats` does not build a montage; it moves existing clips.
- Palmier's tool list includes protocol tokens (`end_turn`, `tool_use`);
  the real count is ~50.

**The diagnosis worth keeping:** the flaw is not that we compile a plan. It
is that the plan's *vocabulary* is impoverished — a plan that can only say
"play these linked excerpts consecutively" necessarily compiles to a clip
reel. The granular tool model should execute the planner, not replace it.

Where we are genuinely behind, in the order it costs daily quality:

1. **The production workbench does not edit speech.** Word snapping stops cuts
   landing mid-word; it removes no filler, false start, repeated take, or dead
   air. The OpenTake trial proved one transcript-driven dead-air ripple, but
   the review/app workflow is not built. For a spoken Spanish vlog this is the
   largest single quality defect.
2. **The flat timeline forces bad choices.** When the best line has weak
   picture, we must show the weak shot or lose the line — no B-roll over
   held audio, no J/L cuts.
3. **Loudness normalisation is not audio production.** No denoise, no
   per-clip levelling, no ducking, no voiceover placement.
4. **Revision is replanning, not editing.** "Cut the second 'este'" can
   regenerate the whole clip list; there is no localised, undoable command.
5. **Captions are an export artifact,** not visible or editable in the cut.
6. **Nothing learns from the finishing pass** in OpenTake or Resolve — the
   same manual corrections recur forever.
7. **Rotation and framing are brittle** — sideways clips stay sideways,
   framing is fit/pad rather than subject-aware.
8. **Daily reliability is underbuilt** (in-memory jobs, no dedup).

**Progress on this list since the audit (updated 2026-09-03):** the
"impoverished vocabulary" diagnosis (line above) has been substantially closed —
see "edit-plan.v1 capabilities" above. Specifically: **#2 closed** (B-roll over
held audio and J/L cuts ship); **#3 largely closed** (voiceover placement with
−9 dB ducking, and background music with sidechain-style ducking under speech —
denoise/per-clip levelling still open); **#5 closed** (captions are first-class,
editable per-line, and burned — no longer an export-only artifact); **#7
partially closed** (rotation is auto-detected and applied; subject-aware framing
is still `fit`/letterbox — the `reframe` fill path exists in schema+renderer but
has no producer). Still open as stated: **#1** (no filler/false-start/repeated-
take removal — the single largest remaining daily-quality gap), **#4** (partly:
atomic ops exist for many edits, but a story rewrite still replans), **#6**
(nothing learns from the finishing pass), **#8** (in-memory jobs, no dedup).

Explicitly *not* priorities despite being feature-count wins: avatars, voice
cloning, generated video, multicam, motion graphics, object removal,
advanced colour, and even full semantic search.

Acted on: the owner asked whether OpenTake should become the base, hosting
our planner and ported Palmier features. That proposal was drafted, reviewed
adversarially, and recommended against — see `docs/history/EXECUTION_LAYER_PLAN.md` for
the analysis, the two factual errors the draft contained, and the seven-phase
owned-compiler plan. The owner then chose, risks in view, to trial the
OpenTake path first (fork vSebas/OpenTake, based on v1.0.0-beta.5, five
Linux patches). The trial ran 2026-08-20 to 2026-09-01 and closed with a
HYBRID verdict — OpenTake as editing surface, our renderer for final
pixels; evidence in `docs/history/TRIAL_OPENTAKE.md`.
The audit's owned-compiler fallback order was: Spanish dialogue cleanup (2-3
weeks) → atomic edit-command layer with undo and readback (4-6) →
multi-track execution with B-roll, J/L cuts, VO and ducking (4-6) → dialogue
audio treatment (2-3) → styled captions in the render (1-2) → learn from the
finished Resolve timeline (2-4) → rotation and static framing (3-7 days) →
beat detection (4-7 days). It is no longer the active ordering; the hybrid
roadmap below supersedes it, while `docs/history/EXECUTION_LAYER_PLAN.md` retains the
canonical fallback estimates and gates.

Integration was initially costed at ~7-13 weeks and recommended against; the
owner knowingly superseded that recommendation with the bounded trial in
`docs/history/TRIAL_OPENTAKE.md`. The estimate remains a warning about productionizing the
bridge, not an open decision. Both projects are GPLv3 — reimplement behaviour,
do not copy source.

### Dual review and hardening (2026-08-19)

An external full-project review (Codex, gpt-5.6-sol at max reasoning) and an
internal five-dimension review were cross-checked, then every claim was put to
an adversarial verifier before any code changed. Twelve of fourteen confirmed;
Codex withdrew one outright once shown the verification.

Fixed: grounding accepted a cut whose midpoint merely grazed its evidence and
revisions applied no grounding gate at all; word-snapped source starts were
left off the frame grid so render and NLE could disagree by a frame; audio and
image assets could compile into the video track; captions, word snapping, and
language detection picked their ASR run by sorting random UUIDs, so
re-analysis silently used stale transcripts about half the time;
`project.json` read-modify-write was unserialized across threads; the
auto-approve hedge filter was English-only on Spanish footage. Details and
what was deliberately left alone are in `app/VALIDATION.md`.

`poc-morning-routine/` was retired the same day: its render, export, and
validation scripts moved to `app/pipeline/` and its schemas to `app/schemas/`,
where the app actually uses them.

### Phone-first capture and project management (2026-08-18/19)

- **Uploads from the iPhone**: browser upload with live percent on the
  sending device and a receiver-side banner in every open tab (ASGI-level
  byte counting); a per-clip endpoint for iOS Shortcuts' ~60 s timeout;
  Tailscale (tailnet `seblearns@`) makes the same UI reachable from
  anywhere at `pacman.tailf9616b.ts.net:8787`.
- **Google Drive VlogInbox** (the preferred async path): upload a day from
  the Drive app into `VlogInbox/<title>` with an optional `nota` text as
  the prompt; the UI banners waiting folders and one click imports —
  strictly read-only toward Drive (no delete code path exists).
- **Media management**: clips can be added later (uploads or folder sync)
  and removed with the file deleted from the laptop folder; per-clip
  value scores (0-100, esencial/en uso/reserva/descartable) computed
  deterministically from cut usage, story citations, moments, and speech.
- **Metadata-aware narrative**: capture time (local tz), GPS, and device
  are probed into the inventory and presented to the writer as the real
  chronology.
- **Writer verdict (blind video screening)**: the user watched four
  rendered cuts of identical evidence; deepseek-v4-pro and claude-fable-5
  beat both Qwens; **deepseek-v4-pro is the default writer**
  and retained the seat in a sealed 2026-09-01 rematch against gpt-5.6-sol.
- **Output polish**: frame-exact plan quantization, timeline-aligned SRT
  captions generated with every export, transcript-corroborated speech
  claims skip review, project clone (shared analysis, 0.15 s) / reset /
  start-from-zero controls.


### Evidence-driven model stack (2026-08-16/17)

Every model choice is now empirical (full data in `bench/RESULTS.md`):

- **Perception: `gemini-3.6-flash` with audio-carrying segments** — won the
  temporal-grounding bake-off (13 models incl. self-hosted Vidi1.5-9B and
  TwelveLabs Marengo/Pegasus) on every metric across repeated runs
  (0.855 recall, 12/12 containment with audio). Specialist hypotheses
  (Vidi cloud-hosting, TwelveLabs) empirically retired.
- **ASR: faster-whisper large-v3 on CUDA** (CUDA libs baked into the image;
  small/CPU fallback). An incidental TwelveLabs finding exposed real
  Spanish dialogue that the old 0.75 auto-approve gate discarded; gates are
  now per-evidence-type (speech 0.55 + no-speech check), and visual speech
  mentions corroborated by the transcript auto-approve (corroborate-v1).
- **Writing: `deepseek-v4-pro`, current holder by blind rendered-cut
  screening.** It and claude-fable-5 beat both Qwen tiers in the August
  bracket; on 2026-09-01 deepseek retained the seat against gpt-5.6-sol in a
  sealed two-video rematch. Details are in `bench/RESULTS.md`.
- **Language-aware narrative**: dominant speech language (detected by ASR)
  drives titles/hooks/on-screen text (Spanish-first for this user); quotes
  stay verbatim. Editorial preferences encoded: intent-first concepts,
  IG ~90s as loose guide with content-derived durations, scene count from
  content quality, missing-material recommendations may include voiceovers.
- **Flow improvements**: analysis parallelized (6 concurrent calls, 2h→~20min
  for 40 clips), just-in-time claim confirmation at story-pick time,
  evidence uses only the newest run per adapter, and provider adapters now
  cover OpenAI as well as DashScope and Gemini. `providers.py` contains a
  native Anthropic client, but concept generation and plan revision still
  instantiate the OpenAI-compatible client directly, so Anthropic is not an
  app-ready provider yet.

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

### Deliberately incomplete (reviewed 2026-09-01)

The bullets that used to sit here — no live visual provider, no live speech,
no automatic planning, no generic render, unverified Resolve import — were all
closed between 2026-08-05 and 2026-08-19. What is genuinely still open:

- **Artifact identity.** v1 landed 2026-09-01: visual analysis runs carry a
  content key (media sha256s + adapter + model + prompt version) and a
  repeat run with an unchanged key returns the existing artifact instead of
  paying again ("Re-analyze footage" forces); renders were already cached by
  plan content. The full redesign — content-addressed state replacing the
  mutable `project.json` — remains open and is still the recommended shape.
- **Access control.** Hardened 2026-09-01: a non-loopback bind now refuses
  to start without `VIDEO_EDITING_TOKEN` (one was generated into `.env` —
  open `http://<ip>:8787/?token=…` once on the phone and a cookie keeps it
  logged in); the host's rclone Drive credentials mount read-only with an
  ephemeral in-container copy for OAuth refresh; `no-new-privileges` set.
  Remaining: the workspace itself is necessarily read-write (it is the
  app's job), and the Drive token's scope is still full — a narrower scope
  needs re-authorization on the rclone side.
- **Concept-stage trust.** Hardened 2026-09-01: every citation's description
  is cross-checked against the captions of the observations it overlaps;
  mismatches are flagged needs_review and surfaced on the story card ("⚠ N
  citas que no coinciden con lo observado").
- **Rotation.** Closed 2026-09-01: after each visual analysis, unchecked
  assets get a one-frame orientation question; confident answers land in
  the inventory and the compiler applies them with manual_review flagged.
- **Voiceover placement.** Built 2026-09-01: drop a recording into the
  footage folder, then one instruction ("pon la nota de voz en X") places it
  on a voiceover track; the renderer ducks production audio -9dB under it
  and exports carry an A2 track. Render-side polish — placement to OpenTake
  refuses voiceover plans like J/L cuts.
- A multi-day, dialogue-heavy comparison corpus remains an open acceptance
  check.

### Second external assessment, reviewed (2026-09-01 night)

`docs/history/CHATGPT_CURRENT_PROJECT_ASSESSMENT_2026-09-01.md` — reviewed same night;
verdict in its header. Net adoptions: §14 is the acceptance-run rubric
(top-level metric: "would the owner actually post this video?");
conversational B-roll ops LANDED same night
(add/remove/replace/move_broll, with evidence captions as content hints so
instructions can name footage by what it shows — live-proven: "pon un
cutaway de la comida del comedor" resolved to the right asset); periodically verify the trial fork still rebases onto
newer OpenTake releases; reconcile docs/UI text after each acceptance run.
Its doc-drift findings were exact and are fixed.

## Immediate Next Actions

Everything below the first item is either done or waiting on it. The
2026-09-01 marathon closed: the hybrid gate (placement, revision-guarded
sync, cleanup, instruction edits — all workbench features), P1-P6, the
fork work (state-divergence root cause, lifecycle tools, close fix,
add_track, link divergence — 840 fork tests), J/L placement and
divergent-pair round-trip (no known sync gaps), two adversarial Codex
reviews fully triaged (29 backend findings; 16 UX findings, 2 blockers
each — all fixed), conversational B-roll ops, UX Architecture v2
(Historia/Edición/Metraje/Publicar, Spanish, warm theme, phone pass), and
docs consolidation.

1. **Fresh-footage acceptance runs — needs the user.** The only remaining
   work no agent can do. One day of footage through the full loop
   (directed and autonomous), judged with the §14 rubric from
   `docs/history/CHATGPT_CURRENT_PROJECT_ASSESSMENT_2026-09-01.md` — top
   metric: would you post it? The same day covers: the first REAL B-roll
   and J-cut session, and the sealed P7 sidecar A/B (the judge packet is
   already waiting at `bench/results-context/last-spring-quarter-class/judge/`).
   As of 2026-09-03 the cut is materially more postable than at the last
   status: captions burn in, a music recommendation surfaces at publish time,
   and light open/close fades ship — so the acceptance run now judges a
   near-finished vlog, not a clip reel.
2. **Reference-style intelligence** — design accepted (v2 handoff is
   current: `docs/designs/SOCIAL_TREND_AND_REFERENCE_STYLE_INTELLIGENCE_IMPLEMENTATION_HANDOFF_v2.md`,
   measurement-first; v1 kept for history). MVP #1 slice is BUILT (2026-09-01): schemas
   (`style-observation/template/match.v1`), `video_app/style_intelligence.py`
   (deterministic shot/pacing/speech extraction + one-call semantic grammar
   read + template aggregation + deterministic concept×style matching),
   style-conditioned concept generation (`style_id` → `style_guidance`
   appended to planner guidance; concepts now carry `editorial` metadata),
   endpoints (`/api/styles`, `/api/styles/references`, `/api/styles/analyze`,
   `/api/projects/{id}/style-matches`), Historia style cards + Diagnóstico
   reference analyzer. Hardened through two adversarial Codex rounds
   (2026-09-02: measurement-first raw shot detection, consensus
   aggregation, fail-closed numerics, schema enforcement, deterministic
   style ids). v2-handoff expansion also landed: audio beat grid +
   cut-to-beat measurement, per-field evidence tiers, style-application
   provenance, styled titles (bundled fonts, font/size/position), and
   live VlogInbox status. First real reference analyzed: 0.72s median
   shot, 55 cuts/min, 68 BPM with cuts off-beat (voiceover-led). Suite
   198. FINAL Codex verdict 2026-09-02 after six rounds: APPROVED —
   everything remaining is run-#1-gated. Earlier design review
   (verdict filed in the v2 doc header). Experiment protocol per the
   reservations: run #1's style test splits in two — MATCHING validity
   (baseline concepts held fixed, judge the ranking) and APPLICATION
   validity (one concept held fixed, baseline vs styled PLAN — the styled
   arm must NOT regenerate the story or the A/B is confounded); success
   claims cover only the executable subset (structure, pacing, B-roll,
   beats, titles). Multi-reference style is LIVE (2026-09-02:
   five real references combined into style-675776bf at confidence 0.69
   — the disagreement penalty working on real pacing variance). Nothing
   remains before run #1. Before DECLARING the subsystem successful
   (not before running): style-application.v1 must become structured
   compiler input, and the rendered cut's grammar must be re-measured
   (close the loop at the pixels). Everything else in the design (Trend
   Scout, clustering, providers, music, preference-ML) stays gated behind
   run #1.
3. **Style engineering follow-ups (post-run-#1, from the design
   review's reservations):** make `style-application.v1` structured
   COMPILER input (shot-duration targets, B-roll ratio, beat
   quantization, title policy) so measurable style stops being
   non-binding prose; close the loop at the pixels — re-measure the
   rendered cut's grammar against the reference and report achieved vs
   target; calibrate match weights against blind choices and finished
   edits before any score is presented as more than a heuristic.
4. **Standing items:** full content-addressed state redesign; periodic
   check that the fork still rebases onto newer OpenTake releases
   (fork-only policy — no upstream PRs; rebase cost is ours);
   docs/UI reconciliation after each acceptance run; writer seat stays
   deepseek-v4-pro (retained blind 2026-09-01); trending-audio matching
   arrives via the accepted style design, not separately.
5. **OpenTake sync-back completeness (2026-09-03 deep check):** FIXED
   2026-09-03 (Codex verification pending — deferred at the user's request).
   `timeline_to_candidate_plan` now re-fits the **music bed span** and clamps
   the **title track** to the new duration via shared helpers
   (`_refit_music_bed`, `_clamp_titles_to_duration`) also used by the in-app
   `_ripple`. Voiceover ducking needed no change — the renderer derives it live
   from the rebuilt voiceover events, so it follows automatically. Unit-tested;
   full suite 282.

### Trust system (afirmaciones) — fail-closed at the claim level

Verified by six adversarial review rounds (2026-09-02, final verdict
YES). Authorization rides on evidence identity under a server-owned
lineage contract: claims cite observation ids, plans carry them,
revisions inherit them under coverage validation, and render/restore
re-check them against CURRENT approvals with envelope coverage.
Documented limitations (honest boundaries, not holes): pre-lineage
legacy artifacts use a risk-gated lexical fallback; auto-approval
confidence is a ratified routing policy; the regex risk lexicon is
defense-in-depth, not the authorization boundary.

## Durable Sources of Truth

- `README.md` — what the project is, how to run it, and the principles
- `app/README.md` — the daily loop and its endpoints
- `app/VALIDATION.md` — what has actually been verified, with dates
- `bench/RESULTS.md` — the evidence behind every model choice
- `docs/history/EXECUTION_LAYER_PLAN.md` — the execution-layer analysis and owned build plan
- `docs/history/TRIAL_OPENTAKE.md` — the OpenTake trial: setup, steps, and its gate
- This file — current state, what is deliberately incomplete, what is next

Superseded material lives in git history rather than in the tree: the July
proof of concept and its write-ups (removed 2026-08-19 with
`poc-morning-routine/`), and the product charter, Phase 1 feasibility audit,
tool-test matrix, initial model strategy, Phase 2-5 plans, upstream checkout
inventory, and completed-work changelogs (removed 2026-08-20, once they
described a system that no longer existed). The charter's enduring parts —
the goal and the principles — moved into `README.md`.

Generated evidence and personal source media stay local and out of the
repository by policy: `footage/`, `runtime/`, `bench/media/`, and
`bench/planner/` are all gitignored — the last because its concepts and plans
quote transcribed speech, and `bench/media/` because it is the real footage
the grounding leaderboard was measured on.
