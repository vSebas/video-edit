# Web UX Rework Proposal — video-edit Workbench

**Project:** `video-edit` + OpenTake hybrid editor  
**Date:** 2026-09-01  
**Purpose:** Product/UX design handoff for simplifying the current web workbench and restructuring it around user goals instead of pipeline internals.

---

# 0. Executive summary

The current web interface has outgrown its original structure.

The backend evolved rapidly and now supports:

- story generation
- grounded evidence
- dialogue cleanup
- B-roll
- voiceover
- J/L cuts
- captions
- revision history
- OpenTake placement/sync
- atomic edit commands
- media management
- DaVinci exports
- diagnostics
- provider/model status
- artifact re-runs

The interface exposes too much of this capability directly.

The result is a page that feels like an **engineering control panel** rather than an **AI editing product**.

The core UX problem is:

> **The interface is organized around everything the system can do, instead of what the user is trying to do right now.**

The recommended redesign is to organize the product around four primary user tasks:

```text
STORY     EDIT     MEDIA     PUBLISH
```

with a separate hidden/de-emphasized diagnostics/developer layer.

The desired user experience should feel like:

> Here is your video. What do you want to do with it?

not:

> Here are all the internal steps, tools, and adapters available.

---

# 1. Current UX diagnosis

The app nominally has a three-step flow:

```text
Step 1 — Create
Step 2 — Pick a story
Step 3 — Watch, tweak, export
```

That is conceptually good.

The problem appears after a cut exists.

The result view becomes a long vertical dashboard containing, in sequence:

```text
Preview + edit chat
↓
DaVinci export
↓
Missing-shot recommendations
↓
Clip-value analytics
↓
Scene-by-scene explanation
↓
Optional claim review
↓
Advanced drawer
    ↓
    re-analyze footage
    re-transcribe speech
    regenerate ideas
    re-render preview
    re-render captions
    rebuild exports
    send to OpenTake
    pull changes from OpenTake
    dialogue cleanup
    OpenTake cleanup candidate list
    OpenTake diff
    another edit-command box
    revision history
    duplicate project
    restart project
    restart from zero
    delete project
    upload more media
    full media grid
```

Even when some of this is collapsed, the information architecture still communicates:

> **Here are all our capabilities.**

That is the wrong product framing.

---

# 2. Product UX principle

The interface should hide implementation complexity whenever possible.

The user should think in terms of:

```text
What story?
What should change?
What footage do I have?
How do I publish/export?
```

The user should **not** need to think in terms of:

```text
analysis adapter
ASR run
render pipeline
OpenTake placement
OpenTake sync
candidate plan
bridge readback
revision application
export rebuild
```

Those should remain backend concepts.

---

# 3. New top-level information architecture

Recommended project navigation:

```text
┌─────────────────────────────────────────────────────┐
│ Story     Edit     Media     Publish           ⋯   │
└─────────────────────────────────────────────────────┘
```

The four tabs correspond to actual user goals.

The `⋯` menu contains project-level or uncommon actions.

---

# 4. Story

The Story area answers:

> **What video are we making?**

It should contain:

- current story/concept
- alternative story proposals
- hook
- structure
- missing-shot suggestions
- "try different ideas"
- content archetype
- future trend/style recommendations

It should **not** contain render/export/infrastructure controls.

---

## 4.1 Story proposal UI

Example:

```text
STORY

Detected type:
Research progress
[Change]

────────────────────────────────────

1. Experiment failed → debugging → success

Hook:
"I thought this experiment was finally going to work."

Why it works:
Clear failure / retry / payoff.

Missing:
1 optional reaction shot.

[Use this story]

────────────────────────────────────

2. Quiet research-day vlog

[Use this story]

────────────────────────────────────

[Try different ideas]
```

The existing story cards are already relatively close to this UX.

---

# 5. Future trend/style integration belongs inside Story

Do **not** create another large top-level "Trends" dashboard.

Trend/style intelligence should appear as a creative decision inside the Story flow.

Example:

```text
VIDEO TYPE
Research progress

STORY
Experiment failed → debugging → success

STYLE

● Natural academic vlog
  Best overall fit

○ Problem → struggle → payoff   🔥 Rising
  91% footage match

○ Fast lab recap                ↗ Rising
  82% match

[Browse more styles]
```

Once selected, this panel can collapse.

The interface should remain centered on:

> Make this video.

not:

> Explore the trend-analysis subsystem.

---

# 6. Edit should become the core workspace

Once a plan exists, Edit should be the default view.

The current player + revision chat are the strongest part of the existing result UX.

They should become the primary stable workspace instead of one section in a long scroll page.

Recommended layout:

```text
┌──────────────────────────────────────────────────────────────┐
│ Research day                         Cut 7 · 54 sec      ⋯  │
├──────────────────────────────────┬───────────────────────────┤
│                                  │                           │
│                                  │  AI EDITOR                │
│          VIDEO PREVIEW           │                           │
│                                  │  What should I change?    │
│                                  │                           │
│                                  │  [____________________]   │
│                                  │                           │
│                                  │  Quick actions            │
│                                  │  [Tighten dialogue]       │
│                                  │  [Improve B-roll]         │
│                                  │  [Add captions]           │
│                                  │  [Adjust pacing]          │
│                                  │                           │
├──────────────────────────────────┴───────────────────────────┤
│  1       2        3       4        5        6               │
│ [img]──[img]────[img]───[img]────[img]────[img]             │
│               STORYBOARD / TIMELINE                          │
└──────────────────────────────────────────────────────────────┘
```

The player should remain visible while the user:

- asks for changes
- cleans dialogue
- changes B-roll
- reviews scenes
- switches revisions
- edits captions
- tweaks pacing

This will make the app feel more like an editor and less like a dashboard.

---

# 7. Use only one edit-command interface

The current product effectively exposes two different natural-language edit surfaces:

1. the main revision form
2. the advanced atomic plan-command form

This distinction should disappear from the user experience.

There should be **one AI editor input**.

Examples:

```text
Remove the second "este".
```

```text
Make the opening faster.
```

```text
Put the market B-roll over this sentence.
```

```text
End on the reaction shot.
```

The backend can decide whether this becomes:

- an atomic deterministic operation
- a localized timeline operation
- a plan revision
- a larger creative replan

That should usually be an implementation detail.

---

# 8. Dialogue cleanup should be a first-class editing tool

Dialogue cleanup should not live under Advanced.

It is a normal editing operation.

Suggested quick action:

```text
[Tighten dialogue]
```

When clicked:

```text
Dialogue cleanup

7 suggestions · removes 4.8 s

☑ long pause             0.8 s
☑ "este"                 0.4 s
☐ short natural pause    0.3 s
☑ repeated phrase        1.1 s
☑ false start            1.0 s

[Apply 5 changes]
```

This is much more natural than exposing transcript-processing machinery.

---

# 9. B-roll should become a visible editing concept

As the editing model gets richer, B-roll should appear in the Edit workspace.

Potential UX:

```text
Quick actions

[Improve B-roll]
```

Then:

```text
B-roll suggestions

00:08–00:11
Current: speaker
Suggested: robot setup shot
Reason: supports "I was setting everything up"

[Use]
[See alternatives]
```

Eventually natural-language commands can manipulate this directly.

---

# 10. Revision history should be native to the editor

Revision history is important enough to deserve proper UX.

Do not bury it in Advanced.

Potential control:

```text
Cut 12 ▾
```

or:

```text
↶ Undo     ↷ Redo     History
```

History view:

```text
Cut 12     Now
Shortened intro by 0.7 s

Cut 11     2 min ago
Removed filler word

Cut 10
Initial edit
```

The existing revision infrastructure is strong enough to support a better interaction model.

---

# 11. Media should be its own workspace

All source-footage management belongs in Media.

Recommended structure:

```text
MEDIA

[ + Add clips / voiceover ]

All   Used   Unused   Needs review

┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ clip 1 │ │ clip 2 │ │ clip 3 │ │ clip 4 │
└────────┘ └────────┘ └────────┘ └────────┘
```

Selecting an asset can expose:

- filename
- duration
- media type
- transcript
- detected moments
- whether it is used in the current cut
- story citations
- clip-value score
- delete/remove action

---

# 12. Move "Valor de cada clip" to Media

The current clip-value analytics are potentially useful.

They are just in the wrong place.

They should not sit directly under the finished-video experience every time.

Instead:

```text
Media → Usage / Value
```

Example:

```text
clip_014.mov

Used in current cut:
7.4 s

Used in story:
Yes

Strong moments:
2

Clip value:
High
```

This keeps the result/edit view focused.

---

# 13. Move the entire source media grid out of Advanced

The full clip grid should be a normal Media feature.

Do not hide basic source management alongside developer-style pipeline controls.

Media management is a first-class task.

---

# 14. Publish should unify all output actions

Publish should answer:

> **What do I want to do with the finished video?**

Example:

```text
PUBLISH

Final video
✓ 54 sec
✓ captions ready
✓ audio normalized

Social

Instagram Reels      [Publish / Export]
TikTok               [Send draft]

Files

MP4                   [Download]

Continue editing

OpenTake              [Open]
DaVinci Resolve       [Export timeline]
```

This is where future TikTok/Instagram integration should live.

---

# 15. OpenTake UX should hide bridge terminology

The current UI exposes concepts such as:

```text
Enviar a OpenTake
Traer cambios de OpenTake
```

These describe the implementation.

The user ideally sees:

```text
[Open in OpenTake]
```

After manual changes:

```text
OpenTake
● Changes detected

[Bring changes into this cut]
```

Eventually synchronization can become more automatic.

The user should not need to mentally model:

```text
place
→ edit
→ readback
→ diff
→ candidate revision
→ apply
```

The backend should manage that complexity.

---

# 16. DaVinci export belongs in Publish

The current prominent "Take it into your editor" card is useful but too implementation-oriented for the default result page.

Move it to:

```text
Publish → Continue editing → DaVinci Resolve
```

Then expose:

- export status
- timeline file
- proxy status
- rebuild action

only when needed.

---

# 17. Advanced should become Diagnostics

The existing Advanced section currently mixes:

- developer operations
- normal editing features
- media management
- project lifecycle actions

These should be separated.

Rename the true technical area:

```text
Diagnostics
```

or:

```text
Developer
```

Access via:

```text
⋯ → Diagnostics
```

or:

```text
Settings → Developer
```

---

# 18. Diagnostics contents

This is where to place:

- force visual re-analysis
- force ASR
- regenerate concepts manually
- manual render
- manual caption render
- rebuild exports
- raw evidence
- provider/model status
- job history
- bridge/debug state
- raw OpenTake diff
- artifact identities
- cache state
- pipeline internals

These are valuable while developing the system.

They should not shape the everyday product UX.

---

# 19. Project-level actions belong in the overflow menu

Recommended `⋯` menu:

```text
Duplicate project
Project settings
Diagnostics
Restart project
Restart and re-analyze
Delete project
```

Do not render all of these as large buttons inside the main workspace.

---

# 20. Sidebar simplification

Current sidebar permanently exposes project list plus system capabilities.

Simplify it.

Recommended:

```text
Vlog Studio

[ + New vlog ]

TODAY
Research day

AUGUST
Conference
Lake trip
Project demo

──────────────

Settings
```

System health can be a small indicator:

```text
● Ready
```

Clicking it can open diagnostics.

There is no need to permanently show:

```text
Footage understanding
Speech transcription
Video rendering
DaVinci Resolve export
```

to someone making a vlog.

---

# 21. Reduce visual "card stacking"

The current UI uses many:

```text
card
card
section
card
section
details
button grid
```

This is natural when a product grows feature-by-feature.

The redesign should use more persistent workspaces and less long-page scrolling.

Especially in Edit:

```text
player stays visible
assistant stays visible
timeline/storyboard stays visible
tools change contextually
```

This will make the app feel substantially more coherent.

---

# 22. Storyboard / timeline should become more important

The current scene strip is useful but presented lower on the result page.

Promote it.

At minimum, the Edit workspace should show:

```text
scene thumbnail
duration
intent / short label
```

Potential future evolution:

```text
primary video lane
B-roll lane
audio indicators
voiceover lane
caption markers
```

without needing to become a full professional NLE.

---

# 23. Progressive disclosure

The UI should follow:

```text
simple by default
powerful when requested
```

Examples:

Default:

```text
Video
Edit request
Quick actions
Storyboard
```

Click a scene:

```text
trim
replace
volume
B-roll
source clip
```

Click "More":

```text
advanced scene properties
```

Avoid displaying every possible operation simultaneously.

---

# 24. Mobile / phone UX mismatch

A concrete issue is the current desktop-width assumption.

The app currently behaves like a desktop workbench even though capture/upload is increasingly phone-first.

Do not necessarily make the **full editor** mobile-first immediately.

Instead define two target experiences.

---

# 25. Phone experience

Optimize phone for:

```text
create vlog
upload footage
write prompt
check progress
review stories
watch result
give simple edit instruction
publish/share
```

Potential phone navigation:

```text
Story
Preview
Publish
```

Keep advanced timeline/media management limited.

---

# 26. Desktop experience

Desktop supports the full workspace:

```text
Story
Edit
Media
Publish
OpenTake
advanced editing
exports
diagnostics
```

The same web app can progressively adapt.

But avoid forcing a desktop minimum width as the long-term design.

---

# 27. Language consistency

The current interface mixes English and Spanish.

Examples include English phrases such as:

```text
Tell the editor what to change
Take it into your editor
Duplicate vlog
```

alongside Spanish:

```text
Valor de cada clip
Qué hay en este corte y por qué
Enviar a OpenTake
Limpieza de diálogo
Historial de revisiones
```

This contributes to the feeling that features were added incrementally.

Recommendation:

```text
one UI language per session
```

Add proper localization later.

For now, choose one primary language and make all user-facing strings consistent.

---

# 28. Suggested navigation behavior by project state

The UI can adapt according to project maturity.

## New project

Default:

```text
Story
```

Main action:

```text
Create my vlog
```

## Concepts ready

Default:

```text
Story
```

Main action:

```text
Choose story
```

## Plan/render ready

Default:

```text
Edit
```

## Finalized

Default can remain Edit, but Publish should show a completion badge.

---

# 29. Status communication

Avoid exposing internal phase/state strings where possible.

Prefer human status:

```text
Analyzing footage…
Writing story ideas…
Building first cut…
Rendering preview…
Ready to edit
```

A lightweight progress overlay/card is sufficient.

---

# 30. Busy state

The current explicit step list is useful.

Keep it, but visually simplify:

```text
Creating your vlog

✓ Watching footage
✓ Transcribing speech
● Writing story ideas

You can leave this page open.
```

No need to expose adapter names or infrastructure.

---

# 31. Missing-shot recommendations belong in Story

Missing coverage is a story/planning concept.

Place it near the selected story:

```text
Missing coverage

Optional:
Record a 3-second reaction after the experiment works.

[Dismiss]
[Mark as recorded]
```

Once the user is actively editing, do not repeatedly show a large missing-shot card unless relevant.

---

# 32. Evidence / claim review should be contextual

The current approach already avoids forcing review of every uncertain claim.

Continue that direction.

If a selected story depends on an uncertain moment:

```text
One quick check

The editor wants to use this moment:
[thumbnail]

"It looks like the experiment failed."

[True]
[Fix wording]
[Don't use]
```

Do not surface a full evidence-management interface during normal editing unless explicitly requested.

Raw evidence belongs in Diagnostics/Media detail.

---

# 33. Clip explanations should be contextual

The scene-by-scene "what is in this cut and why" view is useful.

But instead of a large standalone section, attach it to the storyboard.

Example:

```text
[Scene 4 thumbnail]
Robot setup · 2.1 s

Why:
Supports "I spent the morning setting everything up."
```

This removes another long page section.

---

# 34. Suggested Edit layout in more detail

Desktop:

```text
┌─────────────────────────────────────────────────────────────┐
│ Project title                Story | Edit | Media | Publish │
├────────────────────────────────┬────────────────────────────┤
│                                │                            │
│                                │ AI EDITOR                  │
│                                │                            │
│       VIDEO PREVIEW            │ "What should I change?"    │
│                                │ [______________________]   │
│                                │                            │
│                                │ QUICK                      │
│                                │ Tighten dialogue           │
│                                │ Improve B-roll             │
│                                │ Captions                   │
│                                │ Pacing                     │
│                                │                            │
├────────────────────────────────┴────────────────────────────┤
│ STORYBOARD                                                   │
│ [1]──[2]──[3]──[4]──[5]──[6]                               │
├─────────────────────────────────────────────────────────────┤
│ Cut 12     Undo     Redo     History                        │
└─────────────────────────────────────────────────────────────┘
```

---

# 35. Context-sensitive scene editing

Click scene 4:

```text
Scene 4
2.1 s

Source:
IMG_2041.MOV · 14.2–16.3 s

[Shorten]
[Extend]
[Replace]
[Use B-roll here]
[Mute]
[Open source]
```

Avoid showing these controls until the user selects a scene.

---

# 36. Context-sensitive B-roll

If scene or speech range is selected:

```text
B-roll

Current:
None

Suggested:
[robot setup] 92%
[lab hallway] 71%

[Use robot setup]
```

This directly leverages the grounded evidence system.

---

# 37. Context-sensitive captions

Quick action:

```text
Captions
```

Panel:

```text
Style
● Clean
○ Bold
○ Active-word

Position
Bottom center

Preview:
[caption sample]

[Apply]
```

Future trend/style intelligence can populate current/trending presets.

---

# 38. Context-sensitive style

In Edit, the chosen style should be summarized, not dominate.

Example:

```text
Style
Problem → struggle → payoff
[Change]
```

Click Change → Story/style selector.

---

# 39. Publish layout in more detail

```text
PUBLISH

Preview
[video]

Ready
✓ duration
✓ captions
✓ audio
✓ no missing required media

SOCIAL

Instagram Reels
Trending audio: [selected sound]
[Prepare Reel]

TikTok
Sound: [selected sound]
[Send draft]

FILES

[Download MP4]

CONTINUE EDITING

[Open in OpenTake]
[Export to DaVinci Resolve]
```

This creates a natural home for the trend/music system discussed separately.

---

# 40. Media layout in more detail

```text
MEDIA

[+ Add files]

Filters:
All | Used | Unused | Audio | Needs review

Search [____________]

[thumbnail] morning_walk.mov
Used 4.2 s · 2 moments

[thumbnail] robot_test.mov
Used 8.1 s · strong

[thumbnail] lunch.mov
Unused
```

Click asset → side panel with detail.

---

# 41. Developer mode

Because the project is still actively being engineered, developer controls are useful.

Recommendation:

```text
Settings
  └─ Developer mode [on/off]
```

When on:

- diagnostics available
- raw plan/event IDs optionally visible
- provider status visible
- raw evidence visible
- manual pipeline actions available

When off:

- normal creator-facing UI only

This solves the tension between:

```text
product UX
```

and:

```text
research/engineering workbench
```

without forcing one to serve both badly.

---

# 42. Do not remove observability

Simplifying the UI should **not** mean removing technical visibility from the system.

Keep:

- evidence
- job states
- revisions
- diffs
- provider diagnostics
- artifact identities
- cache controls

Just move them out of the main creator flow.

---

# 43. Accessibility and ergonomics

While reworking the UX:

- keyboard-accessible navigation
- clear focus states
- proper button labels
- responsive layouts
- readable caption/text sizes
- avoid tiny 9–10 px functional text
- avoid relying solely on color for status
- keep dangerous actions clearly separated

The current visual system uses many very small text sizes. That is acceptable for dense developer UIs but less suitable for a polished creator product.

---

# 44. Visual hierarchy

Recommended hierarchy:

## Primary
- video
- story
- edit input
- main CTA

## Secondary
- quick edit tools
- storyboard
- style
- revision history

## Tertiary
- media analytics
- evidence
- exports details
- diagnostics

This hierarchy should be reflected in size, position, and visibility.

---

# 45. Avoid feature-button grids as default navigation

The current Advanced area uses many button grids.

This works for utility panels but should not define the main app.

Prefer:

```text
task-based navigation
+
contextual actions
```

instead of:

```text
grid of capabilities
```

---

# 46. Rework scope

This should be treated as:

> **UX Architecture v2**

not:

> CSS cleanup.

The rework should include:

1. information architecture
2. routing/state model
3. component structure
4. interaction flows
5. responsive behavior
6. language consistency
7. progressive disclosure
8. developer/diagnostics separation

---

# 47. Suggested implementation phases

## Phase 1 — information architecture only

Create top navigation:

```text
Story
Edit
Media
Publish
```

Move existing content into the correct areas without changing backend behavior.

Goal:

> same capabilities, radically clearer organization.

## Phase 2 — unified Edit workspace

Create persistent:

- player
- AI edit box
- quick tools
- storyboard
- revision/history controls

Remove the second edit-command interface from normal UX.

## Phase 3 — Media workspace

Move:

- full media grid
- add/remove media
- clip-value analytics
- transcript/evidence detail

into Media.

## Phase 4 — Publish workspace

Move:

- DaVinci
- OpenTake handoff
- final render/download
- future TikTok/IG actions

into Publish.

## Phase 5 — Diagnostics separation

Move:

- manual reruns
- provider status
- raw sync/debug output
- job internals
- reset tools

into Diagnostics/Developer.

## Phase 6 — responsive/mobile pass

Create a reduced mobile flow optimized for:

- upload
- story
- preview
- simple edits
- publish

## Phase 7 — trend/style UX

Only after the navigation architecture is clean, add:

- content archetype
- recommended styles
- trend indicators
- music selection

inside Story/Publish.

---

# 48. Suggested technical frontend direction

A full frontend framework migration is **not required** just to fix the UX.

The existing app can first be reorganized using the current stack.

However, as interaction complexity grows, component/state management will become harder in one large `app.js`.

Potential later options:

- lightweight modular vanilla JS
- Preact
- React
- Svelte

Do not migrate frameworks solely for fashion.

First solve information architecture.

If the current single-file rendering approach becomes an implementation bottleneck during the redesign, then choose a component framework.

---

# 49. Immediate code-level cleanup opportunities

Even before a larger redesign:

1. stop appending every section to one `resultSection()`
2. split project UI into named workspace renderers:
   - `renderStoryWorkspace`
   - `renderEditWorkspace`
   - `renderMediaWorkspace`
   - `renderPublishWorkspace`
3. add `activeWorkspace` to client state
4. move advanced pipeline actions into `renderDiagnostics`
5. remove duplicate normal/atomic edit inputs
6. make revision history a persistent Edit control
7. move media grid and clip-score loading to Media only
8. move OpenTake/DaVinci output actions to Publish
9. normalize all user-facing strings to one language
10. remove desktop-only assumptions where possible

---

# 50. UX acceptance criteria

The redesigned interface should pass these tests.

## First-time user

After opening a project, can they answer within ~5 seconds:

> What should I do next?

## Existing cut

Can they immediately see:

- the video
- how to change it
- the current story
- how to publish/export

without scrolling through unrelated analytics?

## Common edit

Can they remove filler or change pacing without knowing what an atomic plan operation is?

## Media

Can they find unused footage without searching through Advanced?

## OpenTake

Can they continue editing without understanding bridge/sync implementation?

## Diagnostics

Can the developer still inspect all underlying state when necessary?

## Mobile

Can someone upload footage, choose a story, watch the result, request a simple change, and publish from a phone?

---

# 51. Product-level goal

The interface should transition from:

> **Video Editing Workbench**

toward:

> **AI Vlog Studio / AI Editor**

The backend can remain sophisticated.

The frontend should make that sophistication feel simple.

---

# 52. Final recommendation

The current UX should be reworked before adding major new visible subsystems such as trend/style intelligence.

Otherwise the trend system will become another card, another section, and another group of controls in an already overloaded page.

The backend is now capable enough that the interface should start doing the opposite of what it did during early development:

> **hide complexity instead of exposing it.**

The recommended target is:

```text
STORY
What video am I making?

EDIT
Make the video better.

MEDIA
What footage do I have?

PUBLISH
Where does the finished video go?
```

Everything else belongs behind context, overflow menus, or diagnostics.

That would make the product feel substantially more coherent without requiring any fundamental backend redesign.
