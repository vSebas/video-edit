# AI-Assisted Video Editing Workspace

An AI editing assistant for daily personal vlogs. Raw phone footage goes in;
a grounded story proposal, a rendered cut, and a DaVinci-editable timeline
come out. It is an orchestration layer over evidence — every cut traces to a
timestamped observation — not a new timeline engine.

**Repository:** <https://github.com/vSebas/video-edit> (private)

## Run it

```bash
cd /home/saveas/Documents/video-editing
docker compose up --build -d app
```

Open <http://127.0.0.1:8787>, or from any device on the tailnet at
`http://pacman.tailf9616b.ts.net:8787`. The port is bound to localhost by
default; `VIDEO_EDITING_BIND=0.0.0.0` opens it to the network and should be
paired with `VIDEO_EDITING_TOKEN` (see `app/README.md`). Override the port
with `VIDEO_EDITING_APP_PORT`.

The workspace is bind-mounted at its host path so generated files stay
visible on the host and media paths resolve identically inside DaVinci
Resolve. The container runs as UID/GID 1000; override `LOCAL_UID` and
`LOCAL_GID` if needed.

To validate a compiled plan and its render independently of the app:

```bash
docker compose run --rm app python pipeline/validate_edit.py \
  --plan      ../runtime/projects/<id>/plan/edit-plan.json \
  --inventory ../runtime/projects/<id>/plan/media-inventory.json \
  --media-root .. \
  --render    ../runtime/projects/<id>/outputs/review.mp4 \
  --report    ../runtime/projects/<id>/outputs/validation-report.json
```

## What lives where

| Path | Contents |
|---|---|
| `app/` | The application: FastAPI service, browser workbench, adapters |
| `app/pipeline/` | Deterministic render, OTIO/XMEML export, independent edit validation |
| `app/schemas/` | Versioned media, evidence, concept, edit-plan, and report schemas |
| `bench/` | Model bake-offs and their recorded results |
| `footage/` | Your source clips (never committed) |
| `runtime/` | Per-project state, evidence, renders, exports (never committed) |

Documentation is deliberately six files: this one, `app/README.md` (the
daily loop and its endpoints), `app/VALIDATION.md` (what has actually been
verified, with dates), `bench/RESULTS.md` (the evidence behind every model
choice), `STATUS_AND_ROADMAP.md` (current state and what is next), and
`EXECUTION_LAYER_PLAN.md` (why we are not adopting an external editor, and
the phased plan for building the execution layer ourselves).

## Principles

These have held since the project charter and still decide arguments:

- **Real footage first.** The system understands, organizes, and edits the
  user's own media. Generation is optional and never the foundation.
- **Grounded recommendations.** Every content claim, clip choice, and edit
  decision traces to source media and timecodes. Deterministic gates enforce
  this, because a prompt asking a model to stay grounded is not a guarantee.
- **Plan before execution.** Creative planning stays separate from timeline
  construction and rendering; `edit-plan.json` is the boundary between them.
- **Human control.** The plan, the selected clips, and the intermediate
  results are all visible and revisable.
- **Non-destructive.** Source files are preserved; edits are reversible.
- **Both outputs, always.** A rendered video to review *and* an editable
  timeline linked to the original media — never only one.
- **Practical quality checks.** Validate durations, geometry, frame
  alignment, audio levels, and captions instead of trusting a plausible plan.
- **Modular.** No single model, vendor, or editor is load-bearing; each sits
  behind a replaceable adapter.

## Third-party code

No third-party source is vendored here any more. OpenTake's reference
checkout was removed on 2026-08-20: it exports OTIO/XMEML/FCPXML/EDL but
still cannot import any of them, so it cannot consume what we produce.

Other candidates evaluated during the July 2026 survey — Crayotter,
FireRed-OpenStoryline, MediaMolder, NarratoAI, Palmier Pro, Vidi, CutScript,
OpenReels and others — were assessed and not adopted; their pinned commits
and the comparison that retired them are in git history
(`FEASIBILITY_AUDIT.md` and `repos/README.md`, removed 2026-08-20).
Their local experiment folders were deleted once nothing depended on them:
`FireRed-OpenStoryline/` on 2026-08-18 and `Crayotter/` on 2026-08-20. The
only thing the latter still held was the benchmark footage, which now lives
at `bench/media/` where its purpose is visible.
