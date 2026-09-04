# Platform Music Discovery + iPhone Remote Orchestration Design

**Date:** 2026-09-03  
**Project:** Vlog Studio (`video-edit`) + OpenTake  
**Audience:** Claude / Codex / engineering discussion and implementation  
**Goal:** Make Vlog Studio remotely controllable from an iPhone and replace model-invented music suggestions with grounded TikTok/Instagram-aware recommendations.

---

# 1. Executive summary

Two new product directions should be implemented together because they reinforce the same daily workflow:

```text
A. Platform-grounded music recommendations
B. iPhone-driven remote vlog orchestration
```

The desired experience is:

```text
shoot on iPhone
→ upload footage
→ open Vlog Studio from iPhone
→ choose story/style
→ let Vlog Studio orchestrate OpenTake
→ receive review video on iPhone
→ give feedback
→ repeat until satisfied
→ choose platform-native music
→ export/post
```

The central architectural rule should be:

> **The iPhone talks to Vlog Studio. Vlog Studio talks to OpenTake. The iPhone never becomes an OpenTake MCP client.**

That preserves:
- canonical plan authority;
- grounding;
- revision history;
- validation;
- authentication;
- orchestration ownership.

---

# 2. Current capabilities that already support this direction

The existing project already has much of the backend required.

## Vlog Studio currently has

```text
phone/browser upload
per-clip upload endpoint
Google Drive VlogInbox
Tailscale access
token-based remote authentication
responsive UI
grounded stories
style intelligence
music recommendation metadata
plan revision history
atomic edit commands
OpenTake placement
OpenTake sync preview/apply
owned review render
MP4 download
```

## OpenTake currently has

```text
authenticated external MCP
project lifecycle
project open/create/save
timeline settings
media import
track creation
clip placement/editing
J/L support
caption tools
beat tools
effects/keyframes
full timeline export internally
```

Therefore the new product direction should primarily be:

```text
orchestration + UX + provider grounding
```

rather than a new media/editing architecture.

---

# 3. Product target

The intended phone experience should feel closer to a remote creative assistant than to a phone timeline editor.

Example:

```text
LAST SPRING QUARTER CLASS

23 clips received
Analysis complete
3 stories available

Recommended:
"The experiment failed all morning and finally worked."

Style:
Fast research problem/payoff

[ Make this vlog ]
```

After processing:

```text
CUT 1 READY

[ video player ]

1:14

What do you want to change?

[ The first 10 seconds are too slow... ]

Quick:
[ Shorter ]
[ More B-roll ]
[ Fix dialogue ]
[ Try another style ]

Music:
○ Instagram candidate A
● Instagram candidate B
○ TikTok candidate C

[ Approve ]
```

The phone should not expose:
- track lanes;
- MCP;
- model provider settings;
- evidence IDs;
- low-level render diagnostics;
- OpenTake internals.

Those remain in desktop/diagnostic surfaces.

---

# 4. Architecture

Recommended high-level system:

```text
                          IPHONE
                            │
                     Safari / PWA
                            │
                         Tailscale
                            │
                            ▼
                      VLOG STUDIO
                    remote control plane
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
   Google Drive        story/style AI       review files
      VlogInbox
                            │
                            ▼
                       canonical plan
                            │
                            ▼
                        OpenTake
                    local editing engine
                            │
                            ▼
                      timeline readback
                            │
                            ▼
                     canonical revision
                            │
                            ▼
                      owned renderer
                            │
                            ▼
                      phone review
                            │
                            ▼
                       user feedback
                            │
                            └──────── repeat
```

---

# 5. Phone control rule: never expose OpenTake directly

Do not implement:

```text
iPhone
→ external OpenTake MCP
```

Implement:

```text
iPhone
→ Vlog Studio HTTPS/Tailscale session
→ Vlog Studio orchestration
→ OpenTake localhost MCP
```

## Why

Vlog Studio owns:

```text
project identity
media identity
evidence
story concepts
canonical plan
revision history
validation
renders
access control
```

OpenTake owns:

```text
interactive timeline execution
fine editing
local preview/editor UX
```

Letting the phone directly mutate OpenTake would bypass the most valuable safeguards in the project.

---

# 6. Phone transport: Drive for footage, Vlog Studio for control

Google Drive should remain a **media transport**.

Recommended workflow:

```text
iPhone Camera / Photos
    ↓
Drive app / Shortcut
    ↓
VlogInbox/<project-name>/
    clips...
    nota
```

Vlog Studio monitors:

```text
still receiving
ready
imported
```

The phone then uses Vlog Studio to control:

```text
analysis
story selection
style selection
editing
OpenTake orchestration
rendering
revision
approval
```

Do not make Drive the state/control plane.

---

# 7. Vlog Studio should become a PWA

A native iOS app is not necessary for the first version.

Recommended:

```text
Safari
→ Vlog Studio
→ Add to Home Screen
```

Add:

```text
manifest.webmanifest
service worker
install metadata
standalone display mode
mobile icons
theme/background metadata
```

Optional later:

```text
push notifications
background sync for light metadata
offline shell
```

The PWA should be treated as the everyday phone UI.

---

# 8. Remote session model

The phone should interact with a logical editing session.

Suggested derived state:

```yaml
project_id: ...
current_revision: 8

phase:
  footage_received
  analyzing
  concepts_ready
  editing
  review_ready
  awaiting_feedback
  approved
  publishing

opentake:
  project_open: true
  timeline_fingerprint: ...
  sync_state: clean

review:
  revision: 8
  artifact: review-r008.mp4
  source: owned_renderer
  fresh: true
```

This can be computed from existing project/job/artifact state rather than becoming a second source of truth.

---

# 9. Stale-session behavior is essential

Phone control creates real concurrency.

Example:

```text
phone loaded revision 8
desktop user edits in OpenTake
phone sends a revision-8 instruction
```

Correct behavior:

```text
409 / stale session
```

UI:

```text
OpenTake changed since this review.

[ Sync OpenTake changes ]
[ View current review ]
```

Do not auto-merge arbitrary concurrent timeline changes.

Existing fingerprint/revision protections should be reused.

---

# 10. Phone review loop

The primary loop should be:

```text
review
→ instruction
→ proposal
→ deterministic apply / replan
→ OpenTake update if needed
→ sync
→ render
→ new review
```

Possible user instruction:

```text
"The first 10 seconds are too slow.
Remove the second explanation.
Use more robot footage while I talk."
```

Vlog Studio decides whether this becomes:

```text
atomic plan ops
```

or:

```text
controlled plan rewrite
```

The phone should not care.

---

# 11. OpenTake orchestration from the phone

The phone should expose a single conceptual action:

```text
Edit in OpenTake
```

but server-side it can perform:

```text
ensure OpenTake running
open/create project
import missing media
align settings
place/sync timeline
save
```

If OpenTake cannot be reached:

```text
OpenTake unavailable on host.
Owned-render editing remains available.
```

Do not make the whole phone flow depend on the OpenTake GUI being visible.

---

# 12. Review rendering

## Default path

Keep:

```text
canonical plan
→ owned renderer
→ review.mp4
```

Why:
- canonical state remains authoritative;
- deterministic behavior;
- remote review does not depend on desktop GUI state;
- existing render path is already integrated.

---

# 13. Exact OpenTake preview — later

OpenTake now has an internal full-timeline export implementation.

Eventually expose a narrow orchestration action:

```text
export_review
```

Possible response:

```yaml
source: opentake
timeline_fingerprint: ...
project_revision: ...
path: ...
codec: h264
resolution: 1080p
```

Use this when OpenTake contains creative state not reproduced by the owned renderer, such as:

```text
keyframed motion
advanced effects
motion graphics
advanced color
```

Do not silently replace the canonical render.

Label it:

```text
OpenTake preview
```

---

# 14. Review artifact identity

Every review file should be tied to:

```text
project_id
canonical revision
plan fingerprint
renderer identity
caption mode
music mode
OpenTake timeline fingerprint if applicable
```

Example:

```yaml
review_id: review-...
project_id: ...
revision: 8
plan_sha256: ...
source: owned_renderer
created_at: ...
fresh: true
```

This prevents the phone from showing a visually valid but stale cut.

---

# 15. Notifications

Useful later:

```text
Footage received
Analysis complete
Stories ready
Cut ready to review
OpenTake changed
Render failed
Final export ready
```

For an iPhone PWA, web push may be sufficient if supported by the deployment mode.

Do not block MVP on push notifications.

Polling/current job APIs are enough initially.

---

# 16. Music architecture: separate intent from catalog

Current music recommendation logic is approximately:

```text
story tone
+
measured style BPM/energy
→ LLM
→ concrete searchable track
```

This should evolve to:

```text
creative music intent
+
real platform catalog/trend candidates
→ rank
→ recommendation
```

The model should not invent the final catalog.

---

# 17. Introduce `MusicDiscoveryProvider`

Suggested interface:

```python
class MusicDiscoveryProvider(Protocol):
    def search_audio(
        self,
        query: MusicQuery,
    ) -> list[MusicCandidate]:
        ...

    def trending_audio(
        self,
        query: TrendQuery,
    ) -> list[MusicCandidate]:
        ...

    def get_audio(
        self,
        platform_audio_id: str,
    ) -> MusicCandidate:
        ...
```

Potential adapters:

```text
InstagramAudioProvider
TikTokTrendProvider
ThirdPartySocialProvider
ManualAudioProvider
```

Do not let `edit-plan.v1` depend directly on any provider response format.

---

# 18. Music intent object

Before searching a platform, derive a platform-independent request.

Example:

```yaml
music_intent:
  concept_id: concept-03

  mood:
    - reflective
    - upbeat_payoff

  energy: medium

  bpm:
    preferred: 118
    range: [105, 128]

  structure:
    needs_drop: false
    beat_sync_importance: medium

  niche:
    - academia
    - research
    - engineering

  platform_targets:
    - instagram
    - tiktok
```

This can come from:
- selected story;
- selected style;
- measured reference BPM/energy;
- user preference.

---

# 19. Platform candidate model

Normalize every provider into something like:

```yaml
music_candidate:
  provider: instagram
  platform: instagram

  platform_audio_id: "..."

  title: "..."
  artist: "..."

  availability:
    region: US
    account_usable: true
    checked_at: ...

  musical:
    bpm: 118
    energy: medium
    duration_seconds: ...
    beat_grid: null

  trend:
    state: rising
    rank: 18
    velocity: 0.82
    observed_at: ...

  provenance:
    source: instagram_audio_api
```

Do not invent fields a provider cannot support.

Unknown values should remain unknown.

---

# 20. Music ranking

Conceptual score:

$$
S =
w_s S_{\text{story}}
+
w_y S_{\text{style}}
+
w_b S_{\text{bpm}}
+
w_e S_{\text{energy}}
+
w_t S_{\text{trend}}
+
w_p S_{\text{personal}}
+
w_a S_{\text{availability}}
$$

Where:

```text
story fit
style fit
BPM fit
energy fit
trend momentum
personal preference
platform/account availability
```

Availability should be a hard or near-hard constraint.

A perfect song that cannot be attached to the target Reel should not rank first.

---

# 21. Instagram strategy

Instagram should be the first official-platform integration to investigate deeply.

The desired provider capabilities are:

```text
audio search
audio metadata
trending audio
audio ID
account/region availability
Reel audio attachment where permitted
```

Before implementation, verify current Meta API requirements and permissions.

Do not hardcode assumptions from old API behavior.

---

# 22. TikTok strategy

Treat TikTok separately.

Do not assume the standard TikTok creator/display API provides general trending sound discovery.

Potential sources:

```text
TikTok Creative Center
approved third-party provider
Research API if the project is eligible
manual references
other legal/allowed trend feeds
```

The application should remain provider-agnostic.

---

# 23. Do not download a huge music/video corpus

For trend discovery:

```text
metadata first
→ rank
→ inspect top candidates
```

For music, a full audio download is usually unnecessary.

Useful metadata may be enough:

```text
audio ID
title
artist
trend stats
duration
BPM/energy if provider supplies it
```

If BPM/beat structure is needed and audio access is permitted, analyze only selected candidates.

---

# 24. Keep platform music late-bound

Default publishing model:

```text
Vlog Studio
→ chooses native platform sound
→ stores audio ID + timing metadata
→ edits video around timing/beat structure
→ exports video without copyrighted track
→ user/platform publishing flow attaches native audio
```

This is preferable to burning trending copyrighted music into the MP4.

---

# 25. Music sync metadata

A useful future canonical recommendation:

```yaml
music:
  mode: recommended

  recommended:
    platform: instagram
    provider: instagram_audio_api
    platform_audio_id: "..."

    name: "..."
    artist: "..."

    source_offset_seconds: 12.4

    bpm: 118
    energy: medium

    beat_zero_seconds: 0.0

    beat_grid_seconds:
      - 0.0
      - 0.508
      - 1.016

    late_bound: true
```

The source offset matters.

The same song can contain several very different usable segments.

---

# 26. Temporary guide audio

If the project edits against a platform-native track but does not embed it:

Possible preview options:

```text
silence
metronome/click
beat markers only
temporary legally available local preview
```

The preview should clearly indicate:

```text
Native Instagram audio will be added at posting.
```

---

# 27. Publishing workflow

Long-term:

```text
Approve cut
→ choose platform
→ choose recommended audio
→ final render
→ hand off/post
```

If platform API allows direct native audio attachment:

```text
publish with platform_audio_id
```

If not:

```text
show exact native audio title/id
show source offset
show timing instructions
open platform handoff
```

Do not fake direct-publishing support where APIs do not allow it.

---

# 28. Phone UX — recommended information hierarchy

## Home

```text
projects
new vlog
VlogInbox status
recent review-ready projects
```

## Historia

```text
story cards
recommended style
why it fits
missing-shot advice
make this vlog
```

## Edición

Phone-optimized:

```text
review player
current revision
single edit instruction box
quick actions
change story
change style
OpenTake status
```

Do not show a timeline.

## Metraje

Phone-optimized:

```text
used
unused
missing
add footage
record/add voice note
```

## Publicar

```text
final review
music recommendations
caption mode
platform target
download/export
approve
```

---

# 29. iPhone voice feedback

A natural extension:

```text
record voice note on iPhone
→ upload as instruction
→ ASR
→ editing command
```

Example:

> “I like the middle, but the intro drags. Remove the second explanation and put the robot run over that sentence.”

This is particularly useful while walking or commuting.

Do not confuse:
- voice instruction;
- voiceover media.

The UI should explicitly distinguish them.

---

# 30. iPhone pickup-shot loop

Style/story matching can request:

```text
one reaction shot
one lab establishing shot
one close-up of robot
```

Phone flow:

```text
Vlog Studio asks for pickup
→ user records on iPhone
→ uploads directly to current project
→ analysis only on new media
→ plan updates
→ new review
```

This could become one of the strongest product loops.

---

# 31. Security

Keep the current principle:

```text
localhost by default
remote bind only with authentication
Tailscale preferred
```

Do not expose OpenTake MCP publicly.

Vlog Studio should be the only remotely reachable service.

Recommended:

```text
Tailscale
+
VIDEO_EDITING_TOKEN/session cookie
+
CSRF-safe mutations
+
rate limits for expensive endpoints
```

Later consider:
- per-device sessions;
- session revocation;
- audit log.

---

# 32. Remote API idempotency

Phone networks are unreliable.

Every expensive/mutating action should be safe against retry.

Examples:

```text
make vlog
render revision
place in OpenTake
sync/apply
music search
publish
```

Use:

```text
request ID
revision
fingerprint
dedup
```

where appropriate.

The current project already has useful foundations in job dedup and revision guards.

---

# 33. OpenTake lifecycle

If OpenTake is “always running,” Vlog Studio should track:

```text
reachable
authenticated
current project
current timeline fingerprint
last save
```

Do not require the desktop window to be visible.

OpenTake should behave like a local editing service with a GUI attached.

---

# 34. OpenTake failure behavior

If OpenTake crashes or is unavailable:

```text
Vlog Studio remains usable.
```

Possible degraded mode:

```text
canonical plan edits
owned rendering
history
phone review
```

When OpenTake returns:

```text
place/sync
```

This is another reason not to make OpenTake the canonical source.

---

# 35. Review loop state machine

Suggested state machine:

```text
INGESTING
    ↓
READY_FOR_ANALYSIS
    ↓
ANALYZING
    ↓
CONCEPTS_READY
    ↓
PLAN_READY
    ↓
EDITING
    ↓
RENDERING
    ↓
REVIEW_READY
    ↓
AWAITING_FEEDBACK
    ├──→ EDITING
    └──→ APPROVED
             ↓
          PUBLISH_READY
```

Do not implement a second giant persistent state object if existing artifacts can derive this.

---

# 36. Implementation phases

## Phase 1 — Phone-first review loop

No new native app.

Implement:

```text
mobile/PWA shell
review-ready state
revision-aware review player
single feedback box
quick edit actions
stale-session UI
```

Use existing endpoints.

---

## Phase 2 — Remote OpenTake orchestration UX

Make OpenTake appear as:

```text
Editing engine: connected
Project synced
```

Expose simple actions:

```text
Send/update in OpenTake
Sync OpenTake edits
```

Phone never sees MCP details.

---

## Phase 3 — Review artifact discipline

Add explicit:

```text
review identity
freshness
source
revision
fingerprint
```

Prevent stale phone playback.

---

## Phase 4 — Music provider abstraction

Implement:

```text
MusicDiscoveryProvider
MusicCandidate
MusicIntent
candidate normalization
ranking
```

Keep current LLM recommendation as fallback only.

---

## Phase 5 — Instagram audio integration

Verify current API.

Implement the smallest useful surface:

```text
search
trending
audio metadata
platform ID persistence
```

Do not start with automatic publishing.

---

## Phase 6 — TikTok provider bake-off

Evaluate:

```text
official sources
Creative Center access
third-party provider
manual/imported candidates
```

Select based on:
- currentness;
- niche relevance;
- legal/API fit;
- cost;
- availability metadata.

---

## Phase 7 — Late-bound timing

Add:

```text
source offset
BPM
beat-zero
beat grid
```

Allow planner to align montage sections.

---

## Phase 8 — OpenTake exact review export

Expose a narrow external export command if needed.

Use only when OpenTake-only visual state exists.

---

## Phase 9 — Notifications

Add:

```text
review ready
analysis complete
failure
approval/publish ready
```

---

# 37. Tests

## Phone/session tests

```text
stale revision rejects edit
OpenTake fingerprint changed
retry does not duplicate job
review artifact freshness
phone project switch during active job
auth required remotely
```

## Music tests

```text
provider normalization
unavailable candidate rejected
wrong region down-ranked/rejected
LLM cannot invent provider ID
late-bound audio does not burn track
source offset preserved
trend snapshot provenance
```

## OpenTake orchestration tests

```text
OpenTake offline
OpenTake reconnect
project mismatch
place idempotency
sync before phone apply
export-review fingerprint
```

---

# 38. Explicit non-goals

Do not build yet:

```text
native Swift timeline editor
direct iPhone → OpenTake MCP
full TikTok scraper
large social media archive
automatic copyrighted-audio downloading
always-on cloud backend
multi-user collaboration
CapCut-style mobile timeline
full automatic posting to every platform
```

---

# 39. Long-term product loop

The most compelling long-term loop is:

```text
shoot on phone
→ Vlog Studio understands footage
→ recommends grounded story
→ recommends current relevant style
→ recommends real platform-native music
→ OpenTake executes/refines
→ phone review
→ user feedback
→ revisions
→ approval
→ platform-native publish
→ final manual edits become preference data
```

This gives the project a distinct identity:

> **A remote creative control plane for personal short-form video, with a real editing engine underneath and current platform-aware creative intelligence on top.**

---

# 40. Final recommendation

Build the phone control path before a native iOS app.

Build platform-grounded music before a large general Trend Scout.

Keep:

```text
Vlog Studio = control plane
OpenTake = local editing engine
edit-plan = canonical intent
owned renderer = canonical review/final path
platform music = late-bound native media
```

That is the cleanest extension of the architecture that already exists.
