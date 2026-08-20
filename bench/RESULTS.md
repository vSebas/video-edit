# Temporal-grounding model bake-off — 2026-08-16

12 independently verified moments from the morning-routine benchmark
(`bench/ground-truth.json`); each model receives the full downscaled clip and
the event description, and must return the time range. Raw predictions per
model are in `bench/results/`.

Metrics: **mean IoU** (standard academic grounding score), **mean recall**
(fraction of the true moment covered — the editing-aligned metric, since
padding is trimmable but missed action is unrecoverable), **midpoint-hit**
(prediction contains the moment's midpoint ±0.5 s).

| Model | Mean IoU | Mean recall | Midpoint-hit | Median latency |
|---|---|---|---|---|
| **gemini-3.6-flash + audio track** | **0.614** | **0.855** | **12/12** | 4.0 s |
| gemini-3.6-flash (video only) | 0.593 | 0.808 | 12/12 | 4.0 s |
| qwen3.7-plus (previous default) | 0.386 | 0.761 | 10/12 | 15.8 s |
| qwen3.8-max | 0.422 | 0.705 | 10/12 | 20.9 s |
| gemini-3.7-flash | 0.482 | 0.668 | 10/12 | 4.5 s |
| qwen3-vl-plus | 0.473 | 0.620 | 9/12 | 7.2 s |
| qwen3-vl-flash | 0.314 | 0.607 | 9/12 | 3.3 s |
| gemini-pro-latest | 0.347 | 0.522 | 8/12 | 5.2 s |
| gemini-3.5-flash | 0.323 | 0.499 | 8/12 | 13.2 s |
| twelvelabs-pegasus1.2 | 0.374 | 0.570 | 8/12 | 9.1 s |
| twelvelabs-marengo3.0 (specialist search) | 0.227 | 0.555 | 7/12 | 0.3 s |
| kimi-k2.5 | 0.297 | 0.415 | 6/12 | 7.0 s |
| gemini-3.1-pro-preview | 0.288 | 0.380 | 7/12 | 5.2 s |
| Vidi1.5-9B (self-hosted, A6000) | 0.241 | — | 3/12 hits@0.3 | ~20 s + infra |

## Conclusions

- **Audio audit (2026-08-17):** every model except TwelveLabs was originally
  benched on silent clips because the pipeline strips audio from segments.
  The full audio-enabled rerun (bench/results-audio/) shows
  **gemini-3.6-flash leading under BOTH conditions** (recall 0.808 silent →
  0.855 with audio, 12/12 containment both times) — the only model with a
  stable top position across runs. Most other deltas sit inside the ±0.1
  single-run noise band and should not be over-read. A hearing probe with
  known Spanish dialogue showed no workspace model processes audio — and
  qwen3-vl-plus and kimi-k2.5 CONFABULATED speech ("welcome back to the
  channel" / invented Russian), vindicating the unverified-speech risk gate.
  The upgraded adapter must send audio-carrying segments to Gemini.

- **gemini-3.6-flash wins on every metric** including a perfect containment
  rate; it is the visual-adapter upgrade target.
- **Pro tiers lose to flash tiers at grounding** across both vendors —
  reasoning strength does not help "when did X happen".
- **Vidi1.5-9B (the best *released* Vidi; 2.5 weights were never published)
  is not competitive** on this footage, retiring the cloud-hosting idea.
  VideoChat3-4B was abandoned after two integration failures; its published
  best (56.1 mIoU) is below the measured hosted leader anyway.
- `qwen3.7-plus` predicts wide-but-covering windows (high recall, low IoU) —
  consistent with the observed "right moment, loose edges" cut quality.
- Caveat: n=12 single-run; ranks separated by <0.05 are within noise. The
  leader's margin is not.

- **TwelveLabs (2026-08-17):** the moment-retrieval specialist Marengo 3.0
  scored near the bottom (0.227 IoU) and Pegasus mid-table; neither
  approaches the leader. Their platform also rejects clips <4 s or <360 px —
  a practical mismatch with vlog footage. The specialist hypothesis is
  retired; the visual-model question is closed: **gemini-3.6-flash**.
- **Incidental ASR discovery:** Marengo's transcription revealed real
  Spanish dialogue in IMG_0996 that our pipeline had gated as noise — both
  whisper small and large-v3 transcribe it at ~0.69–0.73 confidence, just
  under the 0.75 auto-approve threshold. The gate, not the model, was
  discarding true speech; the threshold is now per-evidence-type.

## Planner track — blind video screening (2026-08-17)

The writer question cannot be scored by a metric, so it was settled by
screening. Four candidates each wrote a concept from the *same* evidence pack
over a real day of footage; each concept was compiled and rendered to an MP4;
the user watched the four cuts with model identities withheld and ranked
them. Anonymised labels A–D were held in a key file the user could not see
until after ranking.

**Verdict: deepseek-v4-pro.** Its cut and fable's were preferred over both
Qwen tiers; deepseek is the default because it is reachable on the existing
DashScope workspace key. The preferences the screening surfaced are encoded
in the planner prompt: intent-first concepts over theme-first ones, duration
earned by content rather than padded to a round number, and blooper/outtake
framing treated as material rather than waste.

The run artifacts (concepts, compiled plans, render commands) contained the
user's transcribed speech and are deliberately **not** in this repository —
they live in `runtime/` alongside the projects they describe. What is
reproducible from here is the method above, not the personal evidence.

Bench cost: ~$2 RunPod (pod terminated) + a few cents of API calls + free
TwelveLabs tier. Optional external accounts (OpenAI, Anthropic, Reka) remain
unrun.
