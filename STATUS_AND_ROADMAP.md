# AI-Assisted Video Editing — Status and Roadmap

**Updated:** 2026-09-01
**Current phase:** Full daily loop operational and phone-first. Footage
reaches the tool from the iPhone (browser upload with live progress,
Tailscale from anywhere, or a Google Drive VlogInbox that carries title and
nota/prompt); analysis is audiovisual (gemini-3.6-flash) with GPU Spanish
ASR; stories are written by deepseek-v4-pro (blind video-screening
verdict, retained blind against gpt-5.6-sol); cuts are frame-exact with
word-snapped edges, SRT captions, and verified DaVinci exports with DNxHR
proxies. The ratified execution direction is hybrid: OpenTake edits over MCP,
the owned renderer produces final pixels, and Resolve remains the escape
hatch. That direction is not yet integrated into the daily app because
timeline-to-plan sync is still missing. The morning-routine POC
fixture and all Phase 2A archive machinery were removed at the user's
request (2026-08-18), and the POC directory itself on 2026-08-19 — its
render, export, and validation scripts now live in `app/pipeline/` and its
schemas in `app/schemas/`, where the app actually uses them.

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
                 timeline->plan sync (NEXT; not built)
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
  placement and transcript-driven cleanup are live-trial verified, while app
  integration and timeline-to-plan sync remain pending.
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

Explicitly *not* priorities despite being feature-count wins: avatars, voice
cloning, generated video, multicam, motion graphics, object removal,
advanced colour, and even full semantic search.

Acted on: the owner asked whether OpenTake should become the base, hosting
our planner and ported Palmier features. That proposal was drafted, reviewed
adversarially, and recommended against — see `EXECUTION_LAYER_PLAN.md` for
the analysis, the two factual errors the draft contained, and the seven-phase
owned-compiler plan. The owner then chose, risks in view, to trial the
OpenTake path first (fork vSebas/OpenTake, based on v1.0.0-beta.5, five
Linux patches). The trial ran 2026-08-20 to 2026-09-01 and closed with a
HYBRID verdict — OpenTake as editing surface, our renderer for final
pixels; evidence in `TRIAL_OPENTAKE.md`.
The audit's owned-compiler fallback order was: Spanish dialogue cleanup (2-3
weeks) → atomic edit-command layer with undo and readback (4-6) →
multi-track execution with B-roll, J/L cuts, VO and ducking (4-6) → dialogue
audio treatment (2-3) → styled captions in the render (1-2) → learn from the
finished Resolve timeline (2-4) → rotation and static framing (3-7 days) →
beat detection (4-7 days). It is no longer the active ordering; the hybrid
roadmap below supersedes it, while `EXECUTION_LAYER_PLAN.md` retains the
canonical fallback estimates and gates.

Integration was initially costed at ~7-13 weeks and recommended against; the
owner knowingly superseded that recommendation with the bounded trial in
`TRIAL_OPENTAKE.md`. The estimate remains a warning about productionizing the
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

## Immediate Next Actions

The trial gate closed 2026-09-01: **hybrid** (see `TRIAL_OPENTAKE.md`).
OpenTake is the editing surface over MCP; our renderer produces final
pixels; Resolve remains the editor-handoff escape hatch. Items 1-3 of the
original list (timeline→plan sync, productionized adapter, cleanup review
UI) all shipped 2026-09-01 — see the P0-P6 log above.

1. **Real-footage acceptance runs (needs the user).** A fresh day of footage
   end-to-end through the new loop (place → edit in OpenTake → cleanup →
   sync → instruction edits → captioned render); the first REAL B-roll
   session (create a V2 track in the OpenTake GUI, drop clips, sync); and
   the sidecar's second sealed-blind A/B on that same day.
2. **Act on the P1-P6 Codex cross-review** (auto-fires 16:54 2026-09-01).
3. **Fork tasks A-E LANDED (2026-09-01 evening; supersedes the queue note
   below):** Codex did A before hitting its usage limit; Claude implemented
   B-E directly. A d66b6f6 state-divergence root cause (external MCP binds
   the authoritative core); B 80bad10 project lifecycle tools (list/open/
   save over MCP — unattended editing unlocked, scoped to the open bundle's
   folder); C 0676d06 window close exits the process on Linux; D 9bb7248
   add_track (+ discovery: core prunes empty tracks on the next edit
   command, so add_clips must immediately follow); E 4aa0a26 explicit
   link-divergence (silent partner mutation replaced by refuse-or-flag;
   divergent pairs survive save/load; playback has no link assumptions).
   840 fork tests pass. Build note: release binaries need
   `--features custom-protocol` or the webview shows "Could not connect to
   localhost". App-side integration LANDED the
   same evening: placement creates V2/A2 via add_track, places voiceovers
   (volumes set, geometry verified), and authors J/L cuts by re-tiling the
   linked audio lane per boundary (no fork change needed — moves never
   propagated to partners). Both placement refusals are gone; only
   divergent-timeline SYNC remains fail-closed (clear message), the last
   known round-trip gap.
   Original (superseded) note: OpenTake learns to
   REPRESENT render-side polish so placement stops refusing it — add_track
   + existing-index placement on external MCP (also removes the manual V2
   step for B-roll), then explicit link-divergence semantics for J/L cuts
   (replacing the silent linked-partner mutation, issue 5). Runs chained
   after the batch-1 Codex tasks; app-side placement/sync learn the new
   vocabulary once the fork commits land and are reviewed. Ducking stays
   render-side.
5. **Fork-only patch policy (owner decision 2026-09-01): no PRs or issues
   to the upstream OpenTake repo.** All fixes live in the vSebas/OpenTake
   fork; `UPSTREAM_ISSUES.md` stays as internal documentation of the bugs.
   Consequence: rebasing onto future upstream releases is our own cost —
   the fork pins v1.0.0-beta.5 until an upstream release offers something
   worth the rebase. Remaining fork-queue items in value order: project
   lifecycle tools on external MCP (unlocks unattended editing),
   progress-event staleness, window-close lingering process.
4. Standing items: trending-audio matching; reference-vlog style learning;
   the full content-addressed state redesign; writer seat stays
   deepseek-v4-pro (rematch 2026-09-01: retained blind over gpt-5.6-sol);
   sidecar dormant with its harness one command away, but its next judge packet
   must remove treatment metadata and keep the blind key separate.

## Durable Sources of Truth

- `README.md` — what the project is, how to run it, and the principles
- `app/README.md` — the daily loop and its endpoints
- `app/VALIDATION.md` — what has actually been verified, with dates
- `bench/RESULTS.md` — the evidence behind every model choice
- `EXECUTION_LAYER_PLAN.md` — the execution-layer analysis and owned build plan
- `TRIAL_OPENTAKE.md` — the OpenTake trial: setup, steps, and its gate
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
