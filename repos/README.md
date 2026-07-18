# Candidate source checkouts

These are shallow working-tree clones made on 2026-07-17. They are reference/upstream repositories; our code does not silently modify them.

| Directory | Pinned commit | Role |
|---|---|---|
| `Crayotter` | `0bb257350e8933fa6301d4ddc391810cc0702794` | Artifact-rich planning/editing reference |
| `FireRed-OpenStoryline` | `04297707e7607dd398e906262235d0797068e7b4` | Conversational analysis and editing workflow |
| `mediamolder` | `33b9803aa431f2411e80fd0ce6aa3423fb32cbfb` | Declarative processing/rendering candidate |
| `OpenTake` | `acf07e55082112c0361b961d058b81059d832c87` | Native editable project, MCP, and interchange target |
| `NarratoAI` | `022b8bbea3d48f0f60201dd27635ab78ea6ad86a` | Commentary workflow and CapCut export reference |
| `palmier-pro` | `f831c83ace20e0ad80c757fa0ac4f46fd94a7b28` | Mature agent/editor and exporter reference |
| `vidi` | `fc30c870843a8e8e76864702b48b400b712fe0a8` | Temporal video-understanding research backend |
| `speclip-skills` | `cb51e23c69254930858e489c4bcaae5bb10f54b3` | Reusable editing methodology/skills |
| `video-autopilot-kit` | `fd45f0e876219d98fbcba11a38a8513b88309bdf` | CapCut/QA reference |
| `claude-code-video-toolkit` | `9826feb491cffe18367e85f6f759bffcfb93d3da` | Programmatic video-tool reference |
| `codex-storyboard` | `ac9057dee3a903eb211d8399a439ae9992e7656a` | Storyboard reference |
| `short-video-maker` | `9bb9a212ced86caa7e09099c382da1a44d638760` | Short-form rendering reference |
| `OpenReels` | `edc1c973634a3e179a527d00857b85de7b52a1dd` | Open editor/reference implementation |
| `CutScript` | `e5c47e31b39a4178ff3e86a9bd69eb908b7b8bb5` | Transcript-first editing, WhisperX, captions, and audio workflow reference |

To verify the current checkouts:

```bash
for repo in repos/*; do
  printf '%-28s %s\n' "${repo#repos/}" "$(git -C "$repo" rev-parse HEAD)"
done
```

Model weights and large external resource bundles are not part of these Git clones. In particular, cloning Vidi does not install its 9B model.
