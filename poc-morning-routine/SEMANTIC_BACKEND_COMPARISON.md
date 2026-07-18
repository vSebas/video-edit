# Semantic Backend Comparison

## Result

For this seven-clip benchmark, FireRed-OpenStoryline is the best existing semantic baseline, but it should not be trusted as the sole source of trim boundaries. Crayotter is useful as a source of interface and artifact ideas, not as a reliable analysis backend in its current state. A lightweight reconciliation layer—technical probing, sampled-frame verification, boundary clamping, and schema validation—turns FireRed's useful captions into defensible planning evidence.

No additional package was installed for this pass. The existing FFmpeg index, the surviving outputs from both systems, and 77 targeted frames provided enough independent evidence. Installing Vidi 2.5's 9B model would be a poor fit for this laptop's 6 GB VRAM, and adding local ASR would not help the current silent/ambient-footage concept task.

## What Was Compared

- Seven probed source videos totaling 170.240 seconds
- FireRed-OpenStoryline: 24 shot ranges, 24 clip captions, and one overall narrative summary
- Crayotter: seven analysis files containing prose and, for six files, structured one-second semantic segments
- Independent check: 77 frames extracted at targeted timestamps, in addition to the indexer's 68 representative keyframes
- Audio facts from FFmpeg loudness and silence diagnostics; no spoken-content claims were made

The targeted verification sheets are under `artifacts/verification/`. Reconciled observations are in `semantic/verified-observations.json` and have been merged into each per-asset `analysis.json`.

## Findings

### FireRed-OpenStoryline

What worked:

- Correctly recovered the broad story: waking in bed, moving around the bedroom, tying a shoe, checking the refrigerator, eating from a bowl, and leaving on a scooter.
- Its 24 shot boundaries track real changes well enough to seed an edit plan.
- Captions are substantially more specific and accurate than the Crayotter output, especially for `IMG_0994`, `IMG_0996`, and `IMG_0999`.
- The structured `source_ref` mapping is close to what the proposed tool needs.

What needs guarding:

- The final FireRed shot for six assets extends about 45–67 ms beyond the ffprobe duration. Export code must clamp every source range to the probed media duration.
- Some captions add unnecessary certainty: brand names, shoe brands, room type, emotional state, and intent such as “searching.” Those details are not needed for the story and should be removed unless independently verified.
- The overall summary imposes a chronology; the files themselves do not prove capture order. “Morning routine” is a strong creative interpretation, not an observed timestamp fact.
- Shot captions still require crop and motion review before becoming final trims.

Verdict: keep as the first semantic adapter and planning baseline, with deterministic validation around it.

### Crayotter

What worked:

- `IMG_0997` correctly identifies eating and waving.
- `IMG_0999` broadly identifies a backpacked person moving through an indoor hallway.
- The idea of retaining reusable semantic segments and a retrieval index is directionally useful.

What failed:

- `IMG_0994` is 20.415 seconds, but its structured analysis runs to 311 seconds.
- `IMG_0996` is 63.188 seconds, but its analysis runs to 109.2 seconds and repeatedly describes hair-adjusting; the footage is mostly shoe-lacing.
- `IMG_0991` shows the person clearly from roughly 4.5 seconds onward, while much of Crayotter's prose repeats that the view is blocked and no person is visible.
- `IMG_0993` contains prose timecodes but no structured `segments` or `semantic_segments` at all.
- Many recommendations merely enumerate one-second ranges or increase a suggested duration on every line; they do not perform meaningful editorial selection.
- The analyses use confident narrative language even when the underlying descriptions are repetitive or contradicted by the frames.

Verdict: do not use these stored analyses to drive a timeline. Reuse only sound architectural ideas after checking repository licensing.

### Reconciled Frame Review

The independent review confirms the usable content without inventing speech or motivation:

- `IMG_0991`: inside-fridge setup, door close/open transition, reach, empty kitchen hold, camera retrieval
- `IMG_0993`: alternate inside-fridge take in a hoodie, door/light transition, camera retrieval
- `IMG_0994`: blanket/bed wake-up action, getting up, standing near the bunk; source needs rotation/reframing
- `IMG_0995`: locked wide bedroom movement, brief empty-room hold, return to camera
- `IMG_0996`: sneaker presentation, long shoe-lacing action, standing, hand-cover transition
- `IMG_0997`: eating from a bowl and waving
- `IMG_0999`: scooter selfie in a hallway and a final downward scooter reveal

This evidence supports three different stories rather than a single generic montage. They are encoded in `artifacts/creative-concepts.json`.

## Backend Decision for the Proof of Concept

Use a pluggable interface, with this initial order:

1. FFmpeg/ffprobe establishes immutable durations, streams, aspect ratio, audio facts, scene candidates, and hashes.
2. FireRed supplies candidate shot captions and coarse action boundaries.
3. The reconciliation layer strips unsupported specifics, clamps ranges, attaches confidence, and records whether a claim is observation or interpretation.
4. Targeted frame review checks only ranges the creative planner wants to use.
5. ASR is optional and should run only when speech detection or the user's prompt makes dialogue relevant.
6. Vidi-class grounding remains a future adapter for a larger GPU or hosted inference, not a local dependency for this proof of concept.

The next implementation phase should select one concept, convert its evidence into an `edit-plan.json`, render a review MP4, and export OTIO plus DaVinci-compatible XML.
