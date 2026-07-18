# Morning Routine Proof-of-Concept Results

**Completed:** 2026-07-17
**Selected concept:** `concept_chronological_routine` — “Out the Door in 31 Seconds”

## Outcome

The first end-to-end proof of concept is complete. It uses only the seven supplied recordings, chooses nine grounded source ranges, retains natural synchronized audio, corrects the sideways source clip, burns in a simple story structure, renders a vertical review video, and creates neutral plus editor-specific project outputs.

The edit structure is:

1. Wave/hook
2. Wake
3. Get ready
4. Shoes
5. Breakfast
6. Scooter/out the door

## Primary deliverables

- `artifacts/reference-edit/morning-routine-review.mp4` — final review render
- `artifacts/edit-plan.json` — canonical, machine-readable edit decision plan
- `artifacts/reference-edit/validation-report.json` — automated plan/render validation
- `artifacts/timelines/morning-routine.otio` — neutral editable timeline
- `artifacts/timelines/morning-routine-davinci.xml` — DaVinci-compatible FCP7 XMEML
- `artifacts/timelines/morning-routine.opentake/` — native OpenTake project
- `artifacts/timelines/IMPORT_NOTES.md` — import instructions and fidelity notes

## Verified render properties

- 1080 × 1920 vertical output
- 30 fps
- exactly 31.000 seconds
- H.264 video and AAC 48 kHz stereo audio
- decoded maximum audio level: −1.4 dBFS
- 31.0 seconds of unique source imagery
- zero repeated source frames
- all edit points aligned to whole 30 fps frames
- all nine video and nine audio events linked one-to-one
- the 1920 × 1080 wake clip corrected with a verified counterclockwise rotation
- visual spot-check completed across all story beats

## Editable export validation

The neutral OTIO file independently round-trips as a 31.000-second timeline with nine video clips, nine audio clips, and six timed title markers.

The DaVinci XMEML independently parses as a 930-frame sequence with:

- nine video clip items;
- nine audio clip items;
- six text generator items;
- the required orientation transform.

DaVinci Resolve is not installed on this laptop, so the final application-level import check remains manual. Structural parsing is successful, but title appearance can vary by Resolve version; the review MP4 is the visual reference.

## Where the comparisons are

- `../FEASIBILITY_AUDIT.md` — broad tool/repository comparison
- `SEMANTIC_BACKEND_COMPARISON.md` — Crayotter vs FireRed vs targeted independent review
- `OPENTAKE_SPIKE.md` — hands-on OpenTake result
- `CUTSCRIPT_EVALUATION.md` — hands-on CutScript transcript/audio component assessment
- `artifacts/opentake-spike/exports/independent-validation.json` — raw OpenTake round-trip evidence

## OpenTake result in one sentence

OpenTake is validated as a native editable-project and export target, but only partially validated as our main integration layer because the tested source exports OTIO/XMEML without importing them; its XMEML also omits text clips. The generated native `.opentake` bundle avoids the missing import path.

## Remaining acceptance checks

1. Watch the review MP4 for creative pacing and shot preference.
2. Import `morning-routine-davinci.xml` into DaVinci Resolve on a machine with Resolve installed and compare titles/rotation against the review MP4.
3. Apply one requested natural-language revision and confirm that only the plan, timeline exports, and render rebuild—not the media-analysis artifacts.

## Product workbench update

The first local application slice now lives under `../app` and runs at
`http://127.0.0.1:8787` through the root Compose file. It exposes the reviewed
project, media inventory, concepts, missing-shot advice, plan selection,
background rendering, and editable exports. It also indexes new media folders
honestly as technical-only projects while semantic adapters are unavailable.
See `../app/VALIDATION.md` for the container and end-to-end evidence.
