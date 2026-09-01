# OpenTake trial — brain over OpenTake

**Started:** 2026-08-20 · **Closed:** 2026-09-01 — **verdict: HYBRID**
(OpenTake adopted as the editing surface over MCP; our renderer keeps final
pixels; Resolve remains the escape hatch — see the Gate section at the
bottom for the evidence).
**Owner's decision**, made with `EXECUTION_LAYER_PLAN.md`'s risks in view:
try OpenTake as the execution layer before committing 14-20 weeks to building
one. Our pipeline stays the brain; OpenTake's timeline is the hands. If the
gate below fails, the owned-compiler plan resumes unchanged.

## Architecture under trial

```
our Python service (brain)              OpenTake beta.5 (hands + finishing)
phone ingest, Gemini evidence,   MCP    multi-track timeline, ripple edits,
large-v3 Spanish ASR, grounded  ─────▶  undo, captions, effects — and the
story proposal, cut-range maths  :19789  final export (no Resolve, no DNxHR)
```

Key design choice: **Palmier Pro behaviours live in our brain, not in a
port.** We compute filler/silence/false-start ranges from our own word-level
Spanish transcript (Palmier's `remove_words` semantics: stale-transcript
rejection, retained-pause policy, linked-unit ripple) and have OpenTake apply
them via `ripple_delete_ranges`, which cuts linked A/V atomically. OpenTake's
own transcriber and filler tool are not used — their inputs would be worse
than ours.

## Setup (done 2026-08-20)

- Fork: <https://github.com/vSebas/OpenTake> (public — never commit personal
  media or transcripts there).
- Working tree: `~/Documents/OpenTake`, branch `trial` pinned to tag
  `v1.0.0-beta.5` (commit 7349241); `upstream` remote tracks appergb/OpenTake.
  Build from tags, never from upstream main (five betas in thirteen days).
- Source build on Arch: frontend via pnpm (through npx, nothing installed
  globally), then `cargo build` in `src-tauri`. Core crates were already
  verified to compile (Rust 1.97.1); the app build is the real test.

## No-fork surface (verified in source)

- External MCP: fixed `127.0.0.1:19789/mcp`, one-time pairing, persistent
  bearer credentials (`src-tauri/src/external_mcp.rs`, wired in `lib.rs`).
- Editing tools available externally: `add_clips`, `insert_clips`,
  `ripple_delete_ranges`, `split_clip`, `move_clips`, `get_timeline`,
  `get_transcript`, `import_media`, captions, effects (`names.rs:181`).
- NOT available externally: create/open/save/export project — GUI-only for
  now. During the trial a human does those clicks. If the trial passes, these
  become the first upstream PRs (preferred) or fork patches (fallback).
- Never modified: `ChatTurnGate` — it is the safety layer that stops an edit
  admitted against one project publishing into another.

## Findings log

- **2026-08-20 — first Linux build blocker, patched.** `whisper-rs-sys`
  regenerates its C bindings at build time, and current bindgen miscompiles
  them against Arch's new libclang (layout asserts fail; `_IO_FILE`
  collapses to 1 byte). Every whisper-rs version fails identically, so no
  version bump helps. Fix: fork commit `0675152` makes the whisper backend
  optional with a same-named stub — justified because this trial's
  transcripts come from our ASR anyway. 52 lines changed; re-enable with
  `--features whisper` when upstream builds again. This is also the first
  data point for the "Linux is source-only" risk: real, but so far cheap.

- **2026-08-20 — the GUI "event bug" diagnosed: one root cause, four
  symptoms.** `export_video` is a synchronous Tauri command; on Linux those
  run on the GTK main thread, and it runs the entire export before
  returning. For the whole export the thread that delivers UI events and
  receives clicks is busy compositing: progress stays at 0%, cancel clicks
  never reach Rust, the export button gives no feedback, and (separately
  observed while idle) the native menu missed its language rebuild.
  Confirmed live: during an export the main thread (tid==pid) was the
  process's top CPU consumer, sleeping inside the frame loop. Upstream
  plausibly never sees this because its packaged platforms thread IPC
  differently. Fix in progress on the fork (move blocking commands off the
  main thread); prime upstream-PR candidate. Meanwhile the engine itself is
  sound on Linux: decode, HLG→BT.709 tonemapping of iPhone footage, 4K
  GPU compositing, and encode all verified working.
- **2026-08-20 — release builds fail closed on ffmpeg.** By design, release
  builds only accept ffmpeg/ffprobe sitting beside the binary (no PATH
  lookup). Our from-source build had none → every spawn failed ("No such
  file or directory"). Fixed by symlinking the system binaries beside the
  executable — the one-line step our future Linux packaging must do.
- **2026-08-20 — window close leaves the process running** (reproduced
  twice); harmless but goes on the upstream list.

## Trial steps and gate

1. [x] App builds from source and launches on Wayland/NVIDIA (2026-08-26;
       release build + embedded frontend; run.sh carries the workarounds).
2. [x] Paired as external MCP client (2026-09-01): bearer pairing receipt,
       streamable-HTTP handshake, 54 tools listed, and get_timeline read the
       real open project (3 linked A/V clips, 2531 frames, 1080x1920@30).
       The ChatTurnGate correctly refused calls until a saved project was
       open. Note: the listener stays "paused" until the first client pairs
       — the UI's "temporarily unavailable" is that resting state.
3. [x] PASSED (2026-09-01, verification hardened after cross-review):
       deterministic adapter (`app/scripts/opentake_adapter.py`) placed the
       full 22-cut spring-quarter plan over MCP. The first verdict checked
       only geometry; the Codex cross-review caught that source trims and
       A/V pairing were unverified (and that get_timeline reports trims,
       omitted when zero). Re-verified against the live timeline with the
       full check: 22/22 clips, 2346/2346 frames, 21/22 nonzero source
       trims exactly as planned, every audio partner matching on all
       fields; raw readback persisted to the project dir for audit.
       Findings: media must enter via the GUI picker once
       (MCP_PATH_AUTHORITY_REQUIRED — agent paths refused by design; folder
       import works, and the picker's type filter hides uppercase .MOV, so
       use All-files or folder import); omit trackIndex so tracks
       auto-create; some tool successes return plain text, errors are
       redacted JSON with an errorId.
4. [ ] Dialogue cleanup driven by our transcript: Spanish filler lexicon
       (eh, este, o sea, como que), silences, false starts → reviewed ranges
       → `ripple_delete_ranges`.
5. [x] Export + comparison + recovery done (2026-09-01).
       Owner verdict on the side-by-side: content is (correctly) identical —
       same plan, frame-exact both ways — but OpenTake's RENDER looks worse:
       (a) colors — its HLG tonemap (mobius, desat=2) reads flatter than our
       untonemapped pass-through; a tuning knob, not a defect, but today the
       owner prefers our color; (b) wide 16:9 clips (Meta glasses footage)
       come out horizontally squeezed — an aspect-fit defect in the
       compositor's default handling of non-portrait sources, real bug for
       the fork list; (c) our render burns the title hook at the start
       (the "one caption") — the adapter deliberately placed only the video
       track, so OpenTake's cut lacks it. Export wall time ~1h for 77s
       (single-core tonemap bound; profiled). Duration parity confirmed
       (77.1s vs 78.2s, the delta being the dead-air ripple).

**Gate: CLOSED 2026-09-01 — verdict: HYBRID, ratified by the owner.**

The evidence: stability passed (SIGKILL mid-session; timeline recovered
byte-identical including an MCP-applied edit; ~1 min to resume), automation
passed (frame-exact placement, atomic transcript-driven cleanup), but the
render lost — OpenTake's output looked worse than ours on the same cut
(flat tonemap, horizontally squeezed 16:9 sources, no title track), and its
export took ~1 h against our ~5 min.

**Decision: OpenTake is adopted as the EDITING surface — plan placement,
dialogue cleanup, manual finishing, all over MCP — while final pixels keep
coming from our renderer (and the Resolve path stays as the editor-handoff
escape hatch).** Nothing is lost in this split: the daily loop keeps its
fast, better-looking render, and gains a real timeline for the finishing
pass. Revisit OpenTake-as-renderer only after its aspect bug and tonemap
tuning are fixed.

The architectural consequence to build next: the edit-plan stays the source
of truth for rendering, so edits made on the OpenTake timeline (cleanup
ripples, manual changes) must flow BACK into the plan — a timeline→plan
readback sync. That same mechanism is the foundation for "learn from the
owner's finishing pass" later.

## Standing risks accepted by the owner

Beta stability as the finishing surface; source-build maintenance on Linux;
five-betas-in-thirteen-days upstream cadence; project lifecycle requires GUI
interaction until upstreamed. See `EXECUTION_LAYER_PLAN.md` for the full
analysis.
