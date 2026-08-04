# Live OpenStoryline Provider Benchmark

**Date:** 2026-07-18  
**Footage:** seven morning-routine source files, 170.240 seconds  
**Ground truth:** 77 independently reviewed frames plus ffprobe durations  
**Purpose:** test whether current hosted models can safely produce semantic
editing evidence through OpenStoryline

## Result

OpenStoryline is operational with the protected project credentials, but
neither tested VLM is safe as an unvalidated edit authority. Both recover the
broad actions and produce useful coverage/pickup advice. Both also make
unsupported specific claims, lose original filename provenance in the final
answer, and inherit out-of-bounds shot endpoints from OpenStoryline.

The live semantic acceptance gate therefore **fails for raw provider output**
and **passes for feasibility behind deterministic normalization and evidence
review**.

Both complete sessions are now imported into the local workbench at
<http://127.0.0.1:8787>. The comparison view restores original `IMG_*.mp4`
filenames, clamps source ranges, flags risky claims, and presents Qwen and
Gemini captions side by side. Each observation can be approved, corrected, or
rejected through a separate review audit trail; neither provider is approved by
default.

The human review is now complete: Gemini has 24 approvals; Qwen has 19
approvals and one rejection. Those decisions were finalized into separate
versioned candidate evidence sets and a provider scorecard. An independent
cross-check found two approved material conflicts—the Gemini foam claim and the
Qwen bicycle/sign claim—so the artifact preserves the approvals but marks both
candidate sets ineligible for automatic planning. The scorecard also declines
to select a winner because the runs used different shot boundaries.

## Configurations

| Label | Tool-calling LLM | Visual model | Result |
|---|---|---|---|
| Qwen | `qwen3.7-plus` | `qwen3.7-plus` | Operational; full run completed |
| Gemini VLM | `qwen3.7-plus` | `gemini-3.5-flash` | Operational; full run completed |
| Pure Gemini | `gemini-3.5-flash` | `gemini-3.5-flash` | Failed after first tool call |

Pure Gemini is not currently a valid OpenStoryline agent configuration. Its
first `load_media` call succeeded, but the next turn returned HTTP 400 because
Gemini requires its provider-specific thought signature to accompany function
calls. OpenStoryline's current ChatOpenAI/LangChain path does not replay that
field. This does not prevent Gemini from serving as the VLM or from being
called directly by the owned adapter.

Credentials were loaded from the ignored, mode-600 root `.env`. No secret is
stored in this report, Compose, TOML, canonical artifacts, or Git history.

## Runs and Timing

| Run | Session ID | Captions | Visual node | End to end |
|---|---|---:|---:|---:|
| Qwen, `IMG_0996.mp4` smoke | `8c77aec658634622a66778728fa66b4b` | 7 | 240 s | 293 s |
| Gemini VLM, `IMG_0996.mp4` smoke | `592e1f95b224489690da6e105a37a6e4` | 7 | 129 s | 185 s |
| Gemini VLM, seven-file full run | `9b213ae1154445e18741764bb8fda60a` | 24 | 356 s | 485 s |
| Qwen, seven-file full run | `dd7be9c08d234d7da24c16e9d62c5508` | 20 | 607 s | 728 s |

Gemini's visual stage was about 46% faster on the one-file smoke test and about
41% faster on the full run despite receiving four more split clips.

Timing is wall-clock behavior of this exact workflow and network path, not a
general model benchmark. Provider load, prompt length, and retry behavior can
change it.

## Reproducibility Finding

The same local splitter produced different shot counts because the agent chose
different arguments:

- Gemini-VLM full session: `min_shot_duration=1000`,
  `max_shot_duration=10000` -> 24 shots.
- Qwen full session: requested `min_shot_duration=500`, which the node replaced
  with its 1000 ms default, and `max_shot_duration=15000` -> 20 shots.

Shot splitting is technical evidence and must not be selected implicitly by a
planner. The owned pipeline must call it with versioned parameters before any
provider comparison.

## Duration Boundary Validation

OpenStoryline's last split exceeded the ffprobe source duration for six of
seven assets in both full runs:

| Media mapping | Source file | Overshoot |
|---|---|---:|
| `media_0001` | `IMG_0991.mp4` | 62 ms |
| `media_0002` | `IMG_0993.mp4` | 67 ms |
| `media_0003` | `IMG_0994.mp4` | 52 ms |
| `media_0004` | `IMG_0995.mp4` | 55 ms |
| `media_0005` | `IMG_0996.mp4` | 45 ms |
| `media_0006` | `IMG_0997.mp4` | 0 ms |
| `media_0007` | `IMG_0999.mp4` | 67 ms |

This exactly confirms the earlier stored FireRed finding. Every provider range
must be clamped to the immutable ffprobe duration before it can enter an edit
plan.

## Grounding Comparison

### What both backends recovered well

- Refrigerator-interior footage and door/light transitions
- Bed/resting and bedroom movement
- Long, repetitive shoe-lacing coverage
- Eating from a bowl and waving
- Hallway movement and the final scooter reveal
- The shortage of cutaways, establishing shots, connective movement, and meal
  coverage
- Several useful concept and pickup-shot directions

### Qwen-specific failures

- Described the hallway scooter sequence as bicycle footage in one caption and
  invented a readable `NAVIGATING THE FUTURE` sign.
- Named yogurt and shoe brands despite the explicit no-brand instruction.
- Inferred multiple recording sessions from clothing changes.
- Claimed source-relative ranges were verified even though six final endpoints
  exceeded source duration.
- The 20-shot full run cannot be compared directly with the 24-shot run without
  first fixing splitter parameters.

Qwen did avoid Gemini's large false event in the bed footage and produced more
granular trim/pickup advice in the one-file stress test.

### Gemini-specific failures

- Invented a white-foam prank/hit and startled reaction in `IMG_0994.mp4`; the
  reviewed footage shows a person under a blanket getting up, with no foam
  event.
- Named Chobani and New Balance despite the no-brand instruction.
- Added intent/emotion language such as searching, curious, peaceful, and
  confused.
- Asserted that the same person appears in every file with high confidence.
- Replaced original filenames with generic `media_000N` labels in its final
  answer.
- Reported the same invalid extended durations as the splitter.

Gemini correctly retained scooter semantics in `IMG_0999.mp4` and was
substantially faster.

## Acceptance Matrix

| Gate | Qwen | Gemini VLM |
|---|---|---|
| Credentials and OpenAI-compatible transport | Pass | Pass |
| OpenStoryline semantic workflow completes | Pass | Pass with Qwen planner |
| Broad action recognition | Pass | Pass |
| Original filename provenance in final answer | Fail | Fail |
| Source ranges stay within duration | Fail | Fail |
| No unsupported brands/intent/emotion | Fail | Fail |
| No material hallucinated event | Pass on reviewed stress cases | Fail (`IMG_0994`) |
| Deterministic provider comparison | Fail at agent-selected split parameters | Fail at agent-selected split parameters |
| Safe to compile directly into `edit-plan.json` | **No** | **No** |

## Decision

1. Keep OpenStoryline as an integration and workflow reference, not the owner of
   source truth.
2. Keep Qwen as the current OpenStoryline tool-calling LLM because its agent
   path works with this stack.
3. Compare Qwen and Gemini visual evidence through the owned adapter using the
   exact same precomputed scene ranges and frames.
4. Preserve original filename/asset IDs outside the model and reattach them
   deterministically.
5. Clamp all ranges to ffprobe durations and reject negative, inverted, empty,
   or unmapped evidence.
6. Strip or downgrade brands, emotions, intent, chronology, identity, and
   spoken-content claims unless a dedicated evidence source supports them.
7. Add Faster-Whisper/WhisperX separately for speech and cut boundaries; do not
   infer dialogue from mouth movement.
8. Do not automatically render a new model-authored edit until normalized live
   output passes the reviewed benchmark.

## Local Raw Evidence

The full session state, node artifacts, uploaded media copies, and captions are
retained locally under:

- `runtime/openstoryline/qwen/outputs/dd7be9c08d234d7da24c16e9d62c5508/`
- `runtime/openstoryline/gemini-vlm/outputs/9b213ae1154445e18741764bb8fda60a/`

These paths are intentionally ignored because they contain private media and
large runtime artifacts. This report contains only the durable, non-secret
verdict.
