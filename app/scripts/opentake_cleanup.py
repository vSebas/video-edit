#!/usr/bin/env python3
"""Trial step 4: Spanish dialogue cleanup on a live OpenTake timeline.

The brain side: filler and dead-air candidate ranges are computed from OUR
word-level large-v3 transcript (never OpenTake's), mapped through the placed
clips into project frames. The hands side: one atomic ripple_delete_ranges
call per approved batch — linked A/V cut together, gaps closed.

Conservative on purpose: "este"/"o sea" are legitimate Spanish words, so they
only count as filler with a trailing hesitation gap; pure hesitations
("eh", "em", "mmm") always qualify. Dead air is an intra-clip word gap
>= 1.2 s, tightened to leave 0.4 s of natural pause.

Trial-only constraints: the mapping assumes a flat, forward, 1x, 30 fps
timeline and filename-stem media identity. Candidate numbers are recomputed on
each invocation and are not bound to a timeline/transcript revision. The MCP
client has no retry/reconciliation, and the post-readback is recorded but not
used as a rollback condition. Use a saved project you can recover.

Usage:
  opentake_cleanup.py <project_id> --list           # show candidates
  opentake_cleanup.py <project_id> --apply all      # apply every candidate
  opentake_cleanup.py <project_id> --apply 1,3,7    # apply a subset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opentake_adapter import McpClient, REPO, load_plan

PURE_FILLERS = {"eh", "em", "mmm", "eee", "ehh", "emm"}
GAP_FILLERS = {"este", "o sea", "digamos", "como que"}
HESITATION_GAP = 0.35
DEAD_AIR_MIN = 1.2
DEAD_AIR_KEEP = 0.4
WORD_PAD = 0.04
FPS = 30


def latest_transcripts(project_id: str) -> dict[str, list[dict]]:
    """Newest ASR run's words per asset, selected by manifest imported_at."""
    runs = REPO / "runtime" / "projects" / project_id / "analysis" / "runs"
    newest, newest_at = None, ""
    for manifest_path in runs.glob("asr-live-*/manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("imported_at", "") > newest_at:
            newest, newest_at = manifest_path.parent, manifest["imported_at"]
    if newest is None:
        raise SystemExit("no ASR run found")
    words: dict[str, list[dict]] = {}
    data = json.loads((newest / "raw" / "transcripts.json").read_text())
    for record in data.get("transcripts", []):
        asset_words = words.setdefault(record["asset_id"], [])
        for segment in record.get("segments", []):
            for w in segment.get("words", []):
                asset_words.append({
                    "word": w["word"].strip().lower().strip(".,¿?¡!…"),
                    "start": w["start_seconds"],
                    "end": w["end_seconds"],
                })
    for asset_words in words.values():
        asset_words.sort(key=lambda w: w["start"])
    return words


def clip_layout(project_id: str, client: McpClient) -> list[dict]:
    """Placed video clips (from live readback) with their asset ids."""
    media = client.tool("get_media")
    stem_for_ref = {e["id"]: e["name"] for e in media.get("entries", [])}
    _, inventory = load_plan(project_id)
    asset_for_stem = {
        Path(a["filename"]).stem: a["asset_id"] for a in inventory["assets"]
    }
    timeline = client.tool("get_timeline")
    clips = []
    for track in timeline.get("tracks", []):
        if track.get("type") != "video":
            continue
        for c in track.get("clips", []):
            stem = stem_for_ref.get(c["mediaRef"], "")
            clips.append({
                "clipId": c["clipId"],
                "asset_id": asset_for_stem.get(stem),
                "startFrame": c["startFrame"],
                "durationFrames": c["durationFrames"],
                "trimStartFrame": c.get("trimStartFrame", 0),
            })
    return sorted(clips, key=lambda c: c["startFrame"])


def candidates_for(words: dict, clips: list[dict]) -> list[dict]:
    """Filler + dead-air ranges inside placed source windows, project frames."""
    found = []
    for clip in clips:
        asset_words = words.get(clip["asset_id"]) or []
        src_start = clip["trimStartFrame"] / FPS
        src_end = (clip["trimStartFrame"] + clip["durationFrames"]) / FPS

        def to_timeline(src_s: float, src_e: float) -> tuple[int, int]:
            a = clip["startFrame"] + round((src_s - src_start) * FPS)
            b = clip["startFrame"] + round((src_e - src_start) * FPS)
            lo = clip["startFrame"]
            hi = clip["startFrame"] + clip["durationFrames"]
            return max(a, lo), min(b, hi)

        inside = [w for w in asset_words if w["start"] >= src_start - 0.01
                  and w["end"] <= src_end + 0.01]
        for i, w in enumerate(inside):
            gap_after = (inside[i + 1]["start"] - w["end"]) if i + 1 < len(inside) \
                else src_end - w["end"]
            bigram = f"{w['word']} {inside[i+1]['word']}" if i + 1 < len(inside) else ""
            reason = None
            s, e = w["start"], w["end"]
            if w["word"] in PURE_FILLERS:
                reason = f"filler «{w['word']}»"
            elif w["word"] in GAP_FILLERS and gap_after >= HESITATION_GAP:
                reason = f"filler «{w['word']}» + {gap_after:.2f}s hesitation"
            elif bigram in GAP_FILLERS and i + 2 <= len(inside):
                next_gap = (inside[i + 2]["start"] - inside[i + 1]["end"]) \
                    if i + 2 < len(inside) else src_end - inside[i + 1]["end"]
                if next_gap >= HESITATION_GAP:
                    reason = f"filler «{bigram}» + {next_gap:.2f}s hesitation"
                    e = inside[i + 1]["end"]
            if reason:
                a, b = to_timeline(s - WORD_PAD, e + WORD_PAD)
                if b > a:
                    found.append({"frames": (a, b), "reason": reason,
                                  "clip": clip["clipId"],
                                  "context": " ".join(x["word"] for x in inside[max(0, i-2):i+3])})
            # dead air after this word
            if i + 1 < len(inside) and gap_after >= DEAD_AIR_MIN:
                cut_s = w["end"] + DEAD_AIR_KEEP / 2
                cut_e = inside[i + 1]["start"] - DEAD_AIR_KEEP / 2
                a, b = to_timeline(cut_s, cut_e)
                if b - a >= int(0.4 * FPS):
                    found.append({"frames": (a, b),
                                  "reason": f"dead air {gap_after:.1f}s -> {DEAD_AIR_KEEP}s",
                                  "clip": clip["clipId"],
                                  "context": f"…{w['word']} | {inside[i+1]['word']}…"})
    found.sort(key=lambda c: c["frames"][0])
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--apply", help="'all' or comma-separated candidate numbers")
    args = parser.parse_args()

    client = McpClient()
    clips = clip_layout(args.project_id, client)
    matched = sum(1 for c in clips if c["asset_id"])
    print(f"timeline: {len(clips)} clips ({matched} matched to assets)")
    words = latest_transcripts(args.project_id)
    cands = candidates_for(words, clips)
    total = sum(b - a for a, b in (c["frames"] for c in cands))
    print(f"candidates: {len(cands)}, total {total} frames = {total/FPS:.1f}s\n")
    for n, c in enumerate(cands, 1):
        a, b = c["frames"]
        print(f"  {n:2d}. [{a:5d}-{b:5d}] ({(b-a)/FPS:4.1f}s) {c['reason']}")
        print(f"       context: {c['context']}")

    if not args.apply:
        return
    chosen = cands if args.apply == "all" else \
        [cands[int(i) - 1] for i in args.apply.split(",")]
    before = client.tool("get_timeline")
    before_frames = before.get("totalFrames")
    ranges = [[a, b] for a, b in (c["frames"] for c in chosen)]
    result = client.tool("ripple_delete_ranges", {
        "trackIndex": 0, "units": "frames", "ranges": ranges,
    })
    print(f"\napplied {len(ranges)} ranges:",
          json.dumps(result)[:200])
    after = client.tool("get_timeline")
    removed = sum(b - a for a, b in ranges)
    print(f"totalFrames: {before_frames} -> {after.get('totalFrames')} "
          f"(expected ~{before_frames - removed})")
    out = REPO / "runtime" / "projects" / args.project_id / "opentake-cleanup-readback.json"
    out.write_text(json.dumps(after, indent=1))
    print(f"readback persisted: {out}")


if __name__ == "__main__":
    main()
