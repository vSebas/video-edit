# OpenTake trial — brain over OpenTake

**Started:** 2026-08-20
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

1. [ ] App builds from source and launches on Wayland/NVIDIA. *(in progress:
       frontend and opentake-media build clean; full app compiling)*
2. [ ] Pair our service as an external MCP client; list tools.
3. [ ] Import one real day's clips; adapter reproduces an existing
       `edit-plan.json` in its timeline; read back and verify every clip
       (same discipline as the 937/937 Resolve check).
4. [ ] Dialogue cleanup driven by our transcript: Spanish filler lexicon
       (eh, este, o sea, como que), silences, false starts → reviewed ranges
       → `ripple_delete_ranges`.
5. [ ] Owner finishes in the OpenTake GUI and exports; compare against the
       current pipeline's render of the same footage; also export XMEML and
       check what survives into Resolve as the escape hatch.

**Gate:** the finished result must be clearly better than the current
pipeline's cut, the app must be stable enough to trust with a day's edit, and
a failure must be recoverable in under 30 minutes. Any miss → stop, keep the
fork bookmarked, resume `EXECUTION_LAYER_PLAN.md`.

## Standing risks accepted by the owner

Beta stability as the finishing surface; source-build maintenance on Linux;
five-betas-in-thirteen-days upstream cadence; project lifecycle requires GUI
interaction until upstreamed. See `EXECUTION_LAYER_PLAN.md` for the full
analysis.
