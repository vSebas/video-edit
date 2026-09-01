"""Server-side OpenTake placement: plan → live timeline, verified, bridged.

The trial proved this flow in `app/scripts/opentake_adapter.py`; this module
is its production home so the workbench can offer it as a button. Placement
is destructive to the open OpenTake project's timeline (the scratch-project
contract from the trial), so callers surface a confirmation first.
"""

from __future__ import annotations

import json
from pathlib import Path

from .opentake_mcp import OpenTakeMcp, OpenTakeMcpError


class BridgeError(RuntimeError):
    pass


def plan_entries(plan: dict) -> list[dict]:
    """Video-track events → add_clips entries, all in project frames."""
    fps = plan["project"]["fps"]
    video = next(t for t in plan["tracks"] if t["kind"] == "video")
    entries = []
    for event in video["events"]:
        entries.append({
            "asset_id": event["asset_id"],
            "event_id": event["event_id"],
            "startFrame": round(event["timeline_start_seconds"] * fps),
            "durationFrames": round(event["duration_seconds"] * fps),
            "trimStartFrame": round(event["source_start_seconds"] * fps),
        })
    return entries


def map_media(client: OpenTakeMcp, inventory: dict, needed: list[str]) -> dict[str, str]:
    """asset_id → OpenTake mediaRef by filename stem; missing media is a
    user action (the GUI picker), never an agent-side import."""
    assets = {a["asset_id"]: a for a in inventory["assets"]}
    by_stem = {e["name"]: e for e in client.tool("get_media").get("entries", [])}
    ref_for, missing = {}, []
    for asset_id in needed:
        hit = by_stem.get(Path(assets[asset_id]["filename"]).stem)
        if hit:
            ref_for[asset_id] = hit["id"]
        else:
            missing.append(assets[asset_id]["filename"])
    if missing:
        raise BridgeError(
            "Import these files into OpenTake's media library first "
            f"(picker or folder drop): {', '.join(sorted(missing))}"
        )
    return ref_for


def verify_placement(
    entries: list[dict], ref_for: dict[str, str], readback: dict
) -> list[str]:
    """The trial's full check: geometry, source trims, A/V pairing."""
    video = sorted(
        (c for t in readback.get("tracks", []) if t.get("type") == "video"
         for c in t.get("clips", [])),
        key=lambda c: c["startFrame"],
    )
    audio = [c for t in readback.get("tracks", []) if t.get("type") == "audio"
             for c in t.get("clips", [])]
    failures = []
    if len(video) != len(entries):
        failures.append(f"video clip count {len(video)} != {len(entries)}")
    if len(audio) != len(entries):
        failures.append(f"audio clip count {len(audio)} != {len(entries)}")
    audio_by_group: dict = {}
    for clip in audio:
        audio_by_group.setdefault(clip.get("linkGroupId"), []).append(clip)
    for want, got in zip(entries, video):
        for field, expected in (
            ("startFrame", want["startFrame"]),
            ("durationFrames", want["durationFrames"]),
            ("trimStartFrame", want["trimStartFrame"]),
        ):
            if got.get(field, 0) != expected:
                failures.append(
                    f"{want['event_id']}: {field} {got.get(field, 0)} != {expected}"
                )
        if got.get("mediaRef") != ref_for[want["asset_id"]]:
            failures.append(f"{want['event_id']}: wrong media {got.get('mediaRef')}")
        partners = audio_by_group.get(got.get("linkGroupId"), [])
        if len(partners) != 1:
            failures.append(
                f"{want['event_id']}: expected 1 linked audio, got {len(partners)}"
            )
    return failures


def build_bridge(plan: dict, entries: list[dict], ref_for: dict[str, str],
                 readback: dict, project_id: str) -> dict:
    clips = sorted(
        (c for t in readback.get("tracks", []) if t.get("type") == "video"
         for c in t.get("clips", [])),
        key=lambda c: c["startFrame"],
    )
    events = [
        {
            "event_id": e["event_id"],
            "clip_id": c["clipId"],
            "link_group_id": c.get("linkGroupId"),
            "source_start_frame": e["trimStartFrame"],
            "source_end_frame": e["trimStartFrame"] + e["durationFrames"],
            "timeline_start_frame": e["startFrame"],
        }
        for e, c in zip(entries, clips)
    ]
    return {
        "schema_version": "opentake-bridge.v1",
        "project_id": project_id,
        "plan_revision": plan.get("revision", 1),
        "fps": plan["project"]["fps"],
        "media": ref_for,
        "events": events,
    }


def place_plan(
    plan: dict, inventory: dict, project_id: str,
    client: OpenTakeMcp | None = None,
) -> tuple[dict, dict]:
    """Clear the open OpenTake timeline and place the plan; returns
    (summary, bridge). Raises BridgeError with an actionable message."""
    try:
        client = client or OpenTakeMcp()
    except OpenTakeMcpError as exc:
        raise BridgeError(str(exc)) from exc
    entries = plan_entries(plan)
    ref_for = map_media(client, inventory, sorted({e["asset_id"] for e in entries}))

    try:
        before = client.get_timeline()
        existing = [c["clipId"] for t in before.get("tracks", [])
                    for c in t.get("clips", [])]
        if existing:
            client.tool("remove_clips", {"clipIds": existing})
        client.tool("add_clips", {"entries": [
            {
                "mediaRef": ref_for[e["asset_id"]],
                "startFrame": e["startFrame"],
                "durationFrames": e["durationFrames"],
                "trimStartFrame": e["trimStartFrame"],
            }
            for e in entries
        ]})
        readback = client.get_timeline()
    except OpenTakeMcpError as exc:
        raise BridgeError(str(exc)) from exc

    failures = verify_placement(entries, ref_for, readback)
    if failures:
        raise BridgeError(
            "Placement verification failed: " + "; ".join(failures[:5])
        )
    bridge = build_bridge(plan, entries, ref_for, readback, project_id)
    summary = {
        "placed_clips": len(entries),
        "total_frames": sum(e["durationFrames"] for e in entries),
        "removed_previous_clips": len(existing),
    }
    return summary, bridge
