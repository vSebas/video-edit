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
| **gemini-3.6-flash** | **0.593** | **0.808** | **12/12** | 4.0 s |
| qwen3.7-plus (previous default) | 0.386 | 0.761 | 10/12 | 15.8 s |
| qwen3.8-max | 0.422 | 0.705 | 10/12 | 20.9 s |
| gemini-3.7-flash | 0.482 | 0.668 | 10/12 | 4.5 s |
| qwen3-vl-plus | 0.473 | 0.620 | 9/12 | 7.2 s |
| qwen3-vl-flash | 0.314 | 0.607 | 9/12 | 3.3 s |
| gemini-pro-latest | 0.347 | 0.522 | 8/12 | 5.2 s |
| gemini-3.5-flash | 0.323 | 0.499 | 8/12 | 13.2 s |
| kimi-k2.5 | 0.297 | 0.415 | 6/12 | 7.0 s |
| gemini-3.1-pro-preview | 0.288 | 0.380 | 7/12 | 5.2 s |
| Vidi1.5-9B (self-hosted, A6000) | 0.241 | — | 3/12 hits@0.3 | ~20 s + infra |

## Conclusions

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

Bench cost: ~$2 RunPod (pod terminated) + a few cents of API calls.
Not yet run: the planner track (story quality, blind-judged) and external
accounts (TwelveLabs, OpenAI, Anthropic, Reka).
