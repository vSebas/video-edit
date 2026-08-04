# MediaMolder Vidi Adapter Evaluation

**Checkout:** `repos/mediamolder`  
**Commit:** `33b9803aa431f2411e80fd0ce6aa3423fb32cbfb`  
**License:** LGPL-2.1-or-later  
**Verdict:** **PARTIAL** — useful processor and observability reference; its
documented Vidi service contract is not yet a trustworthy end-to-end temporal
grounding path.

## What was executed

The focused Go test suite was run locally:

```bash
go test ./processors -run VidiAnalyzer -count=1 -v
```

All nine tests passed. They cover parameter validation, audio passthrough,
frame buffering, JPEG/HTTP inference round-trip against a mock service,
non-fatal service errors, frame decimation, and processor registration.

## What the adapter contributes

- A small HTTP boundary between a media graph and a separately hosted model.
- Configurable frame batching, decimation, JPEG quality, request timeout, and
  task/query fields.
- Stream passthrough: inference metadata is attached without replacing media.
- Non-fatal error metadata that lets a graph keep running when inference fails.
- Mappings for captions, temporal ranges, detections, answers, and edit actions.

These are good implementation ideas for the owned provider adapter.

## Limitations found

The tests use a mock HTTP server; they do not prove that the documented Python
wrapper runs the actual Vidi checkpoint or returns the advertised shapes.

- Official Vidi1.5 is a temporal retrieval model. The guide additionally
  advertises spatial boxes and structured edit plans that its example wrapper
  does not produce.
- Each request receives a batch-window duration, not the complete asset
  duration. Returned timestamps are not offset by the batch's source start, so
  repeated batches cannot be safely treated as asset-relative evidence.
- A one-frame batch has zero duration and therefore bypasses the guide's
  grounding prompt.
- `Close` discards a final partial frame buffer instead of flushing it.
- The default 30-second timeout is unsuitable for local 9B inference on the
  current 6 GiB RTX 2060.
- The model is described as Vidi2.5 while the runnable checkpoint and wrapper
  are Vidi1.5-9B; those capabilities should not be conflated.

## Decision

Keep the useful transport, buffering, passthrough, and error-handling ideas,
but let the owned workbench control asset IDs, complete durations, exact source
ranges, provenance, normalization, and validation. Local Vidi inference now
has a PARTIAL feasibility result on the RTX 2060, so actual MediaMolder-to-Vidi
service interoperability is the next bounded spike. Evaluate MediaMolder
rendering separately against the canonical 31-second `edit-plan.json`; neither
test should make MediaMolder the project format.
