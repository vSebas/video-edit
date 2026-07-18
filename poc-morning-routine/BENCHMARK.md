# Benchmark: Seven-Clip Morning Routine

## Objective

Turn the seven supplied morning-routine clips into a concise, grounded vertical video while preserving enough structured information to inspect every recommendation and manually refine the resulting timeline.

This benchmark tests the distinctive part of the proposed tool: understanding real footage and recommending a useful story before editing it.

## Source Set

- `IMG_0991.mp4`
- `IMG_0993.mp4`
- `IMG_0994.mp4`
- `IMG_0995.mp4`
- `IMG_0996.mp4`
- `IMG_0997.mp4`
- `IMG_0999.mp4`

The source files are currently located in `../Crayotter/crayotter-data/user_temp/` relative to the parent `video-editing` directory.

## Target Output

- Format: Instagram Reel / TikTok-style vertical video
- Canvas: 1080×1920, 9:16
- Frame rate: 30 fps
- Duration: 20–45 seconds
- Source policy: use only the supplied media for the initial benchmark
- Generated footage: disabled
- External stock media: disabled
- Repetition: allowed only when narratively motivated; never as duration filler

## Required Recommendation Output

Before rendering, the system must provide at least three meaningfully different concepts. Each concept must include:

- Topic and intended audience
- Opening hook
- Narrative structure and target duration
- Exact source clips and supporting timecodes
- Why each selected moment supports the concept
- Weaknesses or missing coverage
- Specific additional shots, photos, natural sound, or voiceover to record

Recommendations must distinguish observed facts from creative interpretation.

## Required Edit Output

- One approved `edit-plan.json`
- One validation report
- One review MP4
- One OTIO timeline
- One DaVinci-compatible XMEML/XML timeline
- A human-readable summary of media usage and omitted material

## Acceptance Criteria

1. Every selected source range exists in the corresponding source file.
2. The footage summary matches visible/audible content and does not invent events.
3. The concepts are materially different rather than cosmetic title variants.
4. Missing-shot recommendations are specific enough to record.
5. The timeline does not use filler repetition or unexplained slow motion.
6. Vertical cropping keeps the primary subject visible or flags shots requiring manual review.
7. Captions stay inside a platform-safe region and match spoken or approved scripted text.
8. Rendered audio avoids clipping and excessive loudness variation.
9. The XMEML timeline imports into DaVinci Resolve with the expected order and trims.
10. A natural-language revision can update the timeline without re-running unchanged media analysis.

## Baseline Comparison

The new result should be tighter and more defensible than the surviving automated experiments:

- Crayotter final render: 133.7 seconds at 1080×1920
- OpenStoryline render metadata: 88.979 seconds at 608×1080

The benchmark intentionally targets 20–45 seconds to force meaningful selection rather than broad inclusion.
