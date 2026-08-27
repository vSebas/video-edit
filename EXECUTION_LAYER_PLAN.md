# Execution-layer plan — decision record

> **Decision update (2026-08-20, later the same day).** After reading this
> document, the owner chose to trial the OpenTake path anyway: our pipeline
> stays the brain, OpenTake is driven as the execution layer over its
> external MCP surface, and Palmier Pro behaviours are implemented in our
> brain rather than ported (we compute cut ranges from our own transcript
> and have OpenTake apply them via `ripple_delete_ranges`). The analysis
> below stands as written — its risks were accepted knowingly, not
> overlooked — and the owned-compiler plan in it remains the fallback if
> the trial gate fails. Trial record: `TRIAL_OPENTAKE.md`.

**Decided:** 2026-08-20
**Question:** should OpenTake become the base, hosting our planner and ported
Palmier Pro features, replacing DaVinci Resolve as the finishing tool?
**Answer: no.** Build the missing capabilities in our own compiler. Keep
Resolve. Use OpenTake and Palmier as behavioural references and test oracles.

## How this was decided

The owner proposed adopting OpenTake as the base. A migration plan was drafted
and put to an adversarial Codex review, which rejected it and found two
factual errors in the draft. Both errors were verified against the source
before accepting the rejection.

**Error 1 — the central claim was false.** The draft argued that adopting
OpenTake only made sense if it *replaced* Resolve, because multi-track,
effects and captions would not survive OTIO/XMEML into Resolve. Untrue.
OpenTake's XMEML preserves every video and audio track, placement and trims,
speed, static and keyframed volume/opacity/transform/crop, fades, and linked
A/V (`crates/opentake-project/src/fcpxml.rs:14`, `:142`, `:447`). Only styled
captions and proprietary grades/masks are lost. The properties this project
cares about are exactly the ones it carries, so OpenTake in *front* of Resolve
is coherent and replacement was never required. The plan's hinge was wrong,
so the strategy built on it was wrong.

**Error 2 — the fork justification was based on the wrong file.** The draft
claimed external automation was impossible without forking, citing
`src-tauri/src/mcp.rs:4` ("the old fixed-port external MCP listener is
disabled for Beta"). That comment is accurate about that file, but beta.5
ships a separate production path in `src-tauri/src/external_mcp.rs` (127 KB):
a fixed `127.0.0.1:19789/mcp` endpoint with client pairing, persistent bearer
credentials and a long-lived `LiveProjectMcpGate`, wired at startup in
`lib.rs:227`. Stable external control is supported *without* a fork.

Two further corrections: `ChatTurnGate`, which the draft proposed dropping, is
not per-turn plumbing — it gates every timeline read and mutation, owns
cancellation and session-scoped undo, and prevents a command admitted against
one project from publishing into another. Dropping it would produce stale
cross-project writes. And `denoise_audio`, listed as a Palmier port, already
exists in OpenTake (`crates/opentake-domain/src/audio.rs`); only an MCP
wrapper is missing.

## Why we still do not adopt it

With both errors corrected the case for adopting got *weaker*, not stronger,
because the honest reasons to fork evaporated while the costs stayed:

- The supported external seam cannot own the whole job: external MCP has no
  create/open/save/export project lifecycle tools (`names.rs:181`); those
  exist only as internal Tauri commands. Unattended daily runs are not
  possible without a fork after all — just not for the reason first claimed.
- MCP requires the GUI running, a saved project open, pairing intact,
  keychain access and port 19789 free.
- Release cadence is five betas in thirteen days (2026-08-01 to 08-14), with
  2,844 Rust tests. Maintaining a fork is ~20-40% of an engineer, with a
  year-one contingency of 3-6 engineer-months.
- Beta.5's packaged audit is macOS ARM64; Linux is source-only. GUI, WebKitGTK,
  Wayland, wgpu, FFmpeg sidecars and export are unproven on this machine —
  `cargo check` passing on three core crates proves very little about the app.
- Making a 33-star beta the only finishing and recovery surface risks losing a
  day's manual finishing state. Expect occasional 1-3 day disruptions after
  upgrades. That is unacceptable for a daily habit.
- It also violates a standing product principle: no single editor is
  load-bearing.

Verified locally in favour of OpenTake, for the record: `opentake-domain`,
`opentake-project` and `opentake-agent` compile cleanly on this Arch machine
(`cargo check`, Rust 1.97.1), and every Tauri system dependency is present.
The blocker is not buildability.

## What we build instead

Roughly 14-20 solo engineer-weeks, with the first useful release at Phase 2.
Each phase has a gate that must pass before the next begins.

**Phase 0 — reliability floor and acceptance corpus (1 week).**
Freeze the working render/XMEML path. Assemble five Spanish dialogue-heavy
vlog projects as a corpus. Persist job status, add duplicate-submit
protection, back up every plan revision.
*Gate:* kill and restart during analysis and render without duplicate spend or
lost approved state; the 937/937 Resolve check stays green.

**Phase 1 — edit-plan v2 and command kernel (1-2 weeks).**
The plan's vocabulary is the real constraint, not the schema: the schema
already permits multiple tracks (`app/schemas/edit-plan.schema.json:26`), but
planning hardcodes `v1`/`a1`/`t1` (`planning.py:541`), the renderer takes the
first track of each kind (`render_edit.py:23`), and the exporter pairs one
video and one audio track with `zip(..., strict=True)`
(`export_timelines.py:234`). Add stable event ids, track roles, link groups,
z-order, explicit source and record spans, gain envelopes, caption tracks.
Add localised commands — split, delete range, move, set gain, add overlay —
each requiring an expected revision so stale operations are rejected. Keep a
v1 loader.
*Gate:* every v1 plan renders and exports byte/frame-identically; command
replay is deterministic; undo restores the previous plan hash exactly.

**Phase 2 — Spanish dialogue cleanup (2-3 weeks). First useful release.**
Use the large-v3 words we already store. Propose filler, false-start, retake
and silence removals in our own UI with word-index provenance. Apply linked
A/V cuts with short click-suppressing crossfades. Never blanket-delete
ambiguous Spanish words such as "este" or "como" — require review.
*Gate:* 30 representative cuts with zero A/V desync, no audible clicks, no
clipped surviving phonemes, exact revision rollback.

**Phase 3 — multi-track compiler and exporter (4-6 weeks).**
B-roll over held A-roll audio, J/L cuts, voiceover and music lanes. Explicit
timeline placement, z-order and audio mixing. Emit every track and A/V link in
XMEML, with a synthetic Resolve fixture covering tracks, transforms, crop,
opacity, gain, fades and speed.
*Gate:* review render and imported Resolve timeline agree frame-for-frame and
track-for-track on every fixture.

**Phase 4 — dialogue audio production (2-3 weeks).**
Per-clip gain and loudness analysis, ducking envelopes for voiceover and
music, denoise chosen by A/B on real iPhone room noise.
*Gate:* loudness within ±1 LU, true peak under ceiling, no clipping or drift,
owner-approved denoise A/B.

**Phase 5 — captions (1-2 weeks).**
Visible in the review render, content and timing separate from style, exported
as SRT and as an editable Resolve representation, cue identity preserved
across dialogue revisions.
*Gate:* cue count, text and frame timing agree across plan, render, SRT and
Resolve import.

**Phase 6 — daily shadow and cutover (1-2 weeks).**
Ten consecutive daily projects exercising restart, low disk, relinked media,
VFR phone clips, rotation and failed FFmpeg jobs, with the old flat path still
available.
*Gate:* no unrecoverable plan state, no manual XML repair, fallback to the
previous working output in under 30 minutes.

## What to borrow

Behaviour and invariants, not runtime, and reimplemented rather than copied —
both projects are GPL-3.0:

- Palmier's `remove_words`: stale-transcript rejection, word-index selection,
  one-linked-unit restriction, atomic ripple, configurable retained pause.
- OpenTake's `ripple_delete_ranges` semantics: batch every cut in one call,
  cut linked A/V on the same span, shift sync-locked tracks together, refuse
  atomically rather than half-applying.
- OpenTake's `get_transcript` contract: report the transcript of the *current*
  timeline, so what remains after cuts is always what is audible.
- Palmier's layout anchors, for framing that is not fit/pad.

## Reopen this decision if

OpenTake ships a tested Linux package, adds project lifecycle and export tools
to its external MCP surface, and reaches a stable (non-beta) release — or if
Resolve itself becomes the problem.
