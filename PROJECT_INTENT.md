# AI-Assisted Video Editing Project — Intent

**Status:** Enduring project charter. Phase 1 is complete; current execution
status and next milestones are tracked in `STATUS_AND_ROADMAP.md`.

## Goal

Find, adapt, combine, or build a tool that can turn a collection of raw recorded videos, photos, audio, and text instructions into a strong short-form video quickly. The initial focus is Instagram Reels and TikTok, but the system should support other formats, aspect ratios, durations, and styles.

This should be an **AI-assisted editor and creative director**, not primarily an AI video generator. It should favor the user's real media and use AI mainly to understand, organize, recommend, and edit that material. Generated footage may be optional when explicitly useful, but it is not the foundation of the workflow.

## Intended Experience

The user provides raw media and, optionally, a prompt describing the desired result. The system should:

1. Understand the visual, audio, and spoken content of the media.
2. Summarize what the available material is about and identify its strongest moments.
3. Suggest one or more viable video topics, hooks, structures, styles, and target formats.
4. Explain which source clips and exact moments support the proposed story.
5. Identify missing or weak material and recommend specific additional shots, photos, narration, or audio to record.
6. Create an editing plan and then assemble the video from the selected material.
7. Allow revisions through text instructions as well as conventional manual editing.

Example feedback might be: “These clips show X and Y. A strong Reel could focus on Z, open with this moment, follow this structure, and end here. The middle is weak; record a short close-up of A or a voiceover explaining B.”

## Desired Outputs

The system should ideally produce both:

- A rendered video ready for review or publishing.
- A non-destructive, editable timeline/project linked to the original media, so the result can be refined manually in software such as DaVinci Resolve, CapCut, or another editor.

Open interchange formats such as OpenTimelineIO, FCPXML, or EDL may be useful, particularly for DaVinci Resolve. CapCut compatibility should be investigated, but should not be assumed until its project format and reliability are verified.

## Core Principles

- **Real-footage first:** prioritize recorded videos, photos, original audio, and user-provided assets.
- **Grounded recommendations:** every important content claim, clip choice, and edit decision should be traceable to the source media and timecodes.
- **Plan before execution:** keep creative/narrative planning distinct from timeline construction and rendering.
- **Human control:** expose the plan, selected clips, timeline, and intermediate results so the user can approve, revise, or edit them.
- **Non-destructive workflow:** preserve source files and make edits reversible where possible.
- **Flexible output:** support vertical and horizontal formats, different platforms, durations, pacing, caption styles, and content types.
- **Iterative editing:** support requests such as “shorten the intro,” “make it less dramatic,” “use only my footage,” or “make a 30-second vertical version.”
- **Practical quality checks:** validate durations, aspect ratios, crop/reframing, audio levels, captions, missing media, and render results instead of trusting a plausible-looking plan.
- **Modular and replaceable:** avoid depending unnecessarily on a single model, vendor, or editing application.

## Capabilities to Explore

- Video, image, audio, speech, and on-screen-text understanding
- Shot/scene detection and exact temporal retrieval
- Subject tracking and composition-aware reframing for vertical video
- Transcription, speaker detection, captions, and searchable media indexing
- Clip quality, relevance, redundancy, and continuity assessment
- Topic discovery, hook generation, narrative planning, and pacing recommendations
- Missing-shot and pickup-shot recommendations
- Timeline generation, trimming, transitions, titles, captions, music, and audio cleanup
- Prompt-driven revisions and localized re-rendering
- Editable project/timeline export alongside final rendering
- Reusable style templates without forcing every video into the same structure

## Investigation Approach

Before committing to a custom implementation, evaluate existing open-source projects, maintained libraries, plugins, and free tools that may already provide part or all of the workflow. The repositories already identified are starting points, not boundaries. Additional web research should look for newer or better alternatives and compatible components.

Potential outcomes include:

1. Adopting an existing tool with minimal configuration.
2. Extending or forking the strongest existing project.
3. Combining several tools behind a small orchestration layer.
4. Building a focused custom system only where existing options are insufficient.

Initial references include Crayotter, FireRed-OpenStoryline, MediaMolder, NarratoAI, speclip-skills, Palmier Pro, Vidi/Vidi2.5, video-autopilot-kit, claude-code-video-toolkit, codex-storyboard, short-video-maker, and OpenReels. These should be assessed for actual usability, license, maintenance, architecture, media understanding, render quality, and editable-timeline support rather than selected from README claims alone.

## Existing Local Material

The local `Crayotter/` and `FireRed-OpenStoryline/` directories are incomplete experiment artifacts rather than full repositories, but they remain useful. They contain examples of clip analysis, plans, timelines, execution traces, intermediate renders, and final outputs that can help evaluate strengths, failure modes, and desired behavior.

The papers `crayotter-paper.pdf` and `2511.19529v2.pdf` are also relevant. In particular:

- Crayotter supports the idea of an artifact-grounded workflow whose plans, timeline states, renders, and revisions are visible and repairable.
- Vidi2.5/Vidi-Edit supports the separation of multimodal understanding and high-level editing plans from downstream timeline execution and rendering, including fine-grained temporal and spatial grounding.

These ideas are useful foundations, but the project should remain open to other models, tools, and architectures.

## Original Near-Term Direction — Completed

The next phase should compare the available tools and local experiment results, identify reusable components, and design a small proof of concept around real footage. A useful first milestone would analyze a folder of clips, propose a grounded short-form story with missing-shot advice, generate a reviewable timeline, render it, and export an editable project for at least one conventional editor.

That milestone was completed by the morning-routine proof of concept, which
was retired on 2026-08-19 once the daily-vlog application superseded it. See
`STATUS_AND_ROADMAP.md`, `app/VALIDATION.md`, and git history.
