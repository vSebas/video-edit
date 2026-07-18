# OpenTake Native Project and Interchange Spike

**Verdict: PARTIAL**
**Tested:** 2026-07-17
**Repository:** `appergb/OpenTake`
**Commit:** `acf07e55082112c0361b961d058b81059d832c87`

## Question

Can current OpenTake receive this proof-of-concept timeline, preserve it as an editable project, apply a real edit through its native command layer, reopen the project without drift, and emit useful interchange files?

## What was tested

1. Cloned the current upstream repository into a disposable spike directory.
2. Compiled the `opentake-domain`, `opentake-ops`, and `opentake-project` crates on this Linux laptop.
3. Ran the upstream `opentake-ops` and `opentake-project` test suites: **395 passed, 0 failed**.
4. Generated `morning-routine.opentake` from the canonical `edit-plan.json`.
5. Opened it through OpenTake's own `Project::open` implementation.
6. Applied `SetClipProperties` through OpenTake's command layer, adding a six-frame fade to the final video clip.
7. Saved and reopened the edited project.
8. Exported OTIO, XMEML, modern FCPXML, and EDL through OpenTake's own exporters.
9. Parsed OTIO independently with OpenTimelineIO 0.18.1 and parsed both XML variants independently.

## Validated behavior

- Native project opened as 930 frames / 31.000 seconds.
- Three native tracks opened: six text clips, nine video clips, and nine audio clips.
- Seven external media assets resolved in the manifest.
- The edit command committed as timeline version 1.
- The edited clip reopened with `fadeOutFrames = 6`.
- Project duration and track counts did not drift after save/reopen.
- OpenTake OTIO independently parsed at 31.000 seconds with clip counts `[6, 9, 9]`.
- OpenTake XMEML independently parsed with nine video and nine audio clip items.
- OpenTake modern FCPXML independently parsed with six titles.

Raw results are under `artifacts/opentake-spike/exports/`.

## Limitations found

### No timeline importer

The tested source implements OTIO, XMEML, FCPXML, and EDL **export**, but no OTIO/XMEML timeline importer. OpenTake therefore cannot simply open our neutral OTIO or DaVinci XML. We generated a native `.opentake` bundle instead.

### Format fidelity differs

- **XMEML:** preserved nine video clips, nine audio clips, and the orientation transform, but emitted zero title generators. The `90.00` XMEML rotation is OpenTake's FCP-coordinate conversion of the native −90° correction.
- **OTIO:** preserved three tracks and all clip timing, but portable OTIO has no semantic OpenTake title generator or text styling here.
- **Modern FCPXML:** preserved all six titles and is OpenTake's highest-fidelity interchange output, but is less universal than XMEML.
- **EDL:** selected the first visual text track and emitted six `Offline` events, so its EDL is not useful for this layered project.

### Not covered by this bounded spike

- Full Tauri GUI launch
- MCP-driven interactive editing session
- OpenTake compositor/video export of this exact 31-second project
- Actual DaVinci Resolve import

Those are separate, heavier tests. The core project/edit/export path was the important interoperability question for this milestone.

## Decision

Keep `edit-plan.json` as the canonical source of truth. Use:

- the deterministic FFmpeg render for review/delivery;
- our OTIO plus XMEML exporters for neutral/DaVinci interchange;
- the generated native `.opentake` bundle when OpenTake is the manual or agent-controlled editor.

OpenTake remains promising for an interactive editor/MCP layer, but its missing timeline import means it should not be the only interchange boundary yet.
