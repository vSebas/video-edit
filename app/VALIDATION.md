# Application validation

**Verified:** 2026-07-17
**Image:** `video-editing-app:local`

## Automated checks

- Python source compilation: pass
- Browser JavaScript syntax check: pass
- Docker Compose configuration: pass
- API tests: 3 passed
  - reviewed fixture exposes seven assets, three concepts, a 31-second plan, and a render;
  - concept selection distinguishes compiled from uncompiled plans;
  - a generated media folder is hashed, probed, and thumbnailed without fabricated semantics.

## Live container checks

- `GET /api/health`: pass
- `GET /api/status`: pass
- `GET /api/projects/morning-routine`: pass
- Browser page and static assets: pass
- Headless Chromium layout/asset inspection: pass
- Background render job: pass
- Background editable-export job: pass

The app-rendered `runtime/projects/morning-routine/outputs/review.mp4` passed the
existing independent edit validator:

- H.264/AAC, 1080×1920, 30 fps, exactly 31.000 seconds
- 48 kHz stereo, decoded maximum −1.4 dBFS
- nine valid source trims, no repeated source frames
- contiguous video timeline, linked audio, six title beats

The app-generated editable copies independently parse as:

- OTIO: 31.000 seconds, two tracks, six markers
- DaVinci XMEML: 930 frames, nine video clips, nine audio clips

## Deliberately incomplete

New arbitrary projects stop at `awaiting_semantic_analysis`. They do not receive
concepts or an edit plan until a visual and/or speech adapter produces grounded
evidence. OpenStoryline credentials and the local timestamped-ASR runtime remain
unconfigured.
