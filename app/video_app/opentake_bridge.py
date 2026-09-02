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


def _event_entries(events: list[dict], fps: float) -> list[dict]:
    return [
        {
            "asset_id": event["asset_id"],
            "event_id": event["event_id"],
            "startFrame": round(event["timeline_start_seconds"] * fps),
            "durationFrames": round(event["duration_seconds"] * fps),
            "trimStartFrame": round(event["source_start_seconds"] * fps),
        }
        for event in events
    ]


def plan_entries(plan: dict) -> list[dict]:
    """Primary video-track events → add_clips entries, in project frames."""
    fps = plan["project"]["fps"]
    video = next(t for t in plan["tracks"] if t["kind"] == "video")
    return _event_entries(video["events"], fps)


def broll_entries(plan: dict) -> list[dict]:
    """B-roll track events (if any) → add_clips entries, in project frames."""
    videos = [t for t in plan["tracks"] if t["kind"] == "video"]
    if len(videos) == 2 and videos[1].get("role") == "broll":
        return _event_entries(videos[1]["events"], plan["project"]["fps"])
    return []


def _video_tracks(readback: dict) -> list[dict]:
    tracks = [t for t in readback.get("tracks", []) if t.get("type") == "video"]
    return sorted(
        tracks,
        key=lambda t: t.get("trackIndex")
        if isinstance(t.get("trackIndex"), int) else 999,
    )


def map_media(client: OpenTakeMcp, inventory: dict, needed: list[str]) -> dict[str, str]:
    """asset_id → OpenTake mediaRef by filename stem for the WHOLE inventory
    (best effort), so sync can attribute B-roll the user adds in the GUI
    from any known asset. Assets the plan needs must resolve or this raises
    with the user action (the GUI picker — never an agent-side import);
    ambiguous stems are skipped unless needed, keeping the bridge's
    ref→asset reverse map unambiguous."""
    assets = {a["asset_id"]: a for a in inventory["assets"]}
    entries = client.tool("get_media").get("entries", [])
    seen: dict[str, int] = {}
    for entry in entries:
        seen[entry["name"]] = seen.get(entry["name"], 0) + 1
    # A stem appearing twice in the LIBRARY is ambiguous — last-wins would
    # silently place the wrong source and self-consistently "verify" it.
    by_stem = {e["name"]: e for e in entries if seen[e["name"]] == 1}
    ambiguous_library = {name for name, count in seen.items() if count > 1}
    stem_owners: dict[str, list[str]] = {}
    for asset_id, asset in assets.items():
        stem_owners.setdefault(Path(asset["filename"]).stem, []).append(asset_id)
    ref_for, missing = {}, []
    for asset_id, asset in assets.items():
        stem = Path(asset["filename"]).stem
        hit = by_stem.get(stem)
        ambiguous = len(stem_owners[stem]) > 1 or stem in ambiguous_library
        if hit and not ambiguous:
            ref_for[asset_id] = hit["id"]
        elif asset_id in needed:
            missing.append(
                asset["filename"]
                + (" (ambiguous duplicate name — rename one copy)"
                   if ambiguous else "")
            )
    if missing:
        raise BridgeError(
            "Import these files into OpenTake's media library first "
            f"(picker or folder drop): {', '.join(sorted(missing))}"
        )
    return ref_for


def verify_placement(
    entries: list[dict], ref_for: dict[str, str], readback: dict
) -> list[str]:
    """The trial's full check: geometry, source trims, A/V pairing.
    Scoped to the primary (lowest-index) video track; B-roll overlays are
    verified separately by verify_broll_placement."""
    video_tracks = _video_tracks(readback)
    video = sorted(
        (video_tracks[0].get("clips", []) if video_tracks else []),
        key=lambda c: c["startFrame"],
    )
    broll_groups = {
        c.get("linkGroupId")
        for t in video_tracks[1:] for c in t.get("clips", [])
        if c.get("linkGroupId")
    }
    audio = [c for t in readback.get("tracks", []) if t.get("type") == "audio"
             for c in t.get("clips", [])
             if c.get("linkGroupId") not in broll_groups]
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


def verify_broll_placement(entries: list[dict], ref_for: dict[str, str],
                           readback: dict) -> list[str]:
    """Geometry check for overlay clips on the second video track. No audio
    pairing requirement — B-roll is picture-only by definition."""
    video_tracks = _video_tracks(readback)
    clips = sorted(
        (video_tracks[1].get("clips", []) if len(video_tracks) > 1 else []),
        key=lambda c: (c["startFrame"], c.get("trimStartFrame", 0)),
    )
    failures = []
    if len(clips) != len(entries):
        failures.append(f"B-roll clip count {len(clips)} != {len(entries)}")
        return failures
    ordered = sorted(entries, key=lambda e: (e["startFrame"], e["trimStartFrame"]))
    for want, got in zip(ordered, clips):
        for field in ("startFrame", "durationFrames", "trimStartFrame"):
            if got.get(field, 0) != want[field]:
                failures.append(
                    f"{want['event_id']}: {field} {got.get(field, 0)} != {want[field]}"
                )
        if got.get("mediaRef") != ref_for[want["asset_id"]]:
            failures.append(f"{want['event_id']}: wrong media {got.get('mediaRef')}")
    return failures


def _broll_bridge_events(entries: list[dict], readback: dict) -> list[dict]:
    video_tracks = _video_tracks(readback)
    clips = sorted(
        (video_tracks[1].get("clips", []) if len(video_tracks) > 1 else []),
        key=lambda c: (c["startFrame"], c.get("trimStartFrame", 0)),
    )
    ordered = sorted(entries, key=lambda e: (e["startFrame"], e["trimStartFrame"]))
    return [
        {
            "event_id": e["event_id"],
            "clip_id": c["clipId"],
            "link_group_id": c.get("linkGroupId"),
            "source_start_frame": e["trimStartFrame"],
            "source_end_frame": e["trimStartFrame"] + e["durationFrames"],
            "timeline_start_frame": e["startFrame"],
        }
        for e, c in zip(ordered, clips)
    ]


def build_bridge(plan: dict, entries: list[dict], ref_for: dict[str, str],
                 readback: dict, project_id: str,
                 broll: list[dict] | None = None) -> dict:
    video_tracks = _video_tracks(readback)
    clips = sorted(
        (video_tracks[0].get("clips", []) if video_tracks else []),
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
    bridge = {
        "schema_version": "opentake-bridge.v1",
        "project_id": project_id,
        "plan_revision": plan.get("revision", 1),
        "fps": plan["project"]["fps"],
        "media": ref_for,
        "events": events,
    }
    if broll:
        bridge["broll_events"] = _broll_bridge_events(broll, readback)
    return bridge


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
    video_events = next(t for t in plan["tracks"] if t["kind"] == "video")["events"]
    audio_events = next(t for t in plan["tracks"] if t["kind"] == "audio")["events"]
    mirrored = len(video_events) == len(audio_events) and all(
        v["asset_id"] == a["asset_id"]
        and v["source_start_seconds"] == a["source_start_seconds"]
        and v["timeline_start_seconds"] == a["timeline_start_seconds"]
        and v["duration_seconds"] == a["duration_seconds"]
        for v, a in zip(video_events, audio_events)
    )
    if not mirrored:
        raise BridgeError(
            "This plan has J/L cuts (audio boundaries differ from video). "
            "OpenTake's linked clips cannot represent that — render directly, "
            "or send the earlier mirrored revision and keep J/L as the final "
            "render-side polish."
        )
    audio_tracks = [t for t in plan["tracks"] if t["kind"] == "audio"]
    if len(audio_tracks) > 1 and audio_tracks[1].get("events"):
        raise BridgeError(
            "This plan has a voiceover track — render-side polish that "
            "OpenTake cannot represent. Remove the voiceover (instrucción: "
            "'quita la voz en off') before sending, or render directly."
        )
    for event in audio_events:
        db = event.get("volume_db")
        if db is not None and db > 0:
            # OpenTake volume caps at unity; silently clamping a +gain
            # would misrepresent the plan (cross-review 26).
            raise BridgeError(
                f"{event['event_id']} has +{db:g}dB gain, which OpenTake "
                "cannot represent (volume caps at 0dB). Keep positive gain "
                "as render-side polish, or lower it."
            )
    entries = plan_entries(plan)
    broll = broll_entries(plan)
    needed = sorted({e["asset_id"] for e in entries + broll})
    ref_for = map_media(client, inventory, needed)

    try:
        before = client.get_timeline()
        # ALL preflight happens before anything is removed (cross-review
        # BLOCKER 2: a failed check after removal left an empty timeline).
        fps = plan["project"]["fps"]
        if before.get("fps") not in (None, fps):
            raise BridgeError(
                f"OpenTake project runs at {before.get('fps')} fps but the "
                f"plan is {fps} fps — align the project settings first"
            )
        dims = (before.get("width"), before.get("height"))
        want = (plan["project"]["width"], plan["project"]["height"])
        if all(dims) and dims != want:
            raise BridgeError(
                f"OpenTake canvas is {dims[0]}x{dims[1]} but the plan is "
                f"{want[0]}x{want[1]} — align the project settings first"
            )
        broll_index = None
        if broll:
            # The MCP surface cannot create tracks; overlays need an
            # existing second video track (added once in the OpenTake GUI).
            before_videos = _video_tracks(before)
            if len(before_videos) < 2:
                raise BridgeError(
                    "This plan has B-roll, but the open OpenTake project has "
                    "only one video track. Add an empty video track in "
                    "OpenTake (right-click the track area), then send again."
                )
            broll_index = before_videos[1].get("trackIndex")
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
        if broll:
            client.tool("add_clips", {"entries": [
                {
                    "mediaRef": ref_for[e["asset_id"]],
                    "startFrame": e["startFrame"],
                    "durationFrames": e["durationFrames"],
                    "trimStartFrame": e["trimStartFrame"],
                    "trackIndex": broll_index,
                }
                for e in broll
            ]})
        readback = client.get_timeline()

        # Plan volumes ride on the linked audio partner (OpenTake's model:
        # volume lives on the audio clip, 0..1 with 1.0 omitted).
        volume_for = {}
        video_tracks = _video_tracks(readback)
        primary_clips = sorted(
            (video_tracks[0].get("clips", []) if video_tracks else []),
            key=lambda c: c["startFrame"],
        )
        partner_for_group = {
            c.get("linkGroupId"): c["clipId"]
            for t in readback.get("tracks", []) if t.get("type") == "audio"
            for c in t.get("clips", [])
        }
        for audio_event, clip in zip(audio_events, primary_clips):
            db = audio_event.get("volume_db")
            if db is None or db == 0:
                continue
            partner = partner_for_group.get(clip.get("linkGroupId"))
            if partner:
                volume_for.setdefault(
                    round(max(0.0, 10 ** (db / 20)), 4), []
                ).append(partner)
        for volume, clip_ids in volume_for.items():
            client.tool(
                "set_clip_properties", {"clipIds": clip_ids, "volume": volume}
            )
        if volume_for:
            readback = client.get_timeline()
    except OpenTakeMcpError as exc:
        # Preflight passed but a mutation failed mid-flight: best-effort
        # restore of the pre-placement primary clips so the user is not
        # left staring at an empty timeline. (Linked audio re-creates
        # automatically; a restore failure is reported, never hidden.)
        restore_note = ""
        if existing:
            try:
                current = client.get_timeline()
                current_ids = [c["clipId"] for t in current.get("tracks", [])
                               for c in t.get("clips", [])]
                if current_ids:
                    client.tool("remove_clips", {"clipIds": current_ids})
                previous_videos = _video_tracks(before)
                client.tool("add_clips", {"entries": [
                    {
                        "mediaRef": c["mediaRef"],
                        "startFrame": c["startFrame"],
                        "durationFrames": c["durationFrames"],
                        "trimStartFrame": c.get("trimStartFrame", 0),
                    }
                    for c in (previous_videos[0].get("clips", [])
                              if previous_videos else [])
                ]})
                restore_note = " The previous timeline was restored."
            except OpenTakeMcpError:
                restore_note = (
                    " Restoring the previous timeline ALSO failed — "
                    "re-send the plan or undo in OpenTake."
                )
        raise BridgeError(f"{exc}{restore_note}") from exc

    failures = verify_placement(entries, ref_for, readback)
    if broll:
        failures += verify_broll_placement(broll, ref_for, readback)
    if failures:
        raise BridgeError(
            "Placement verification failed: " + "; ".join(failures[:5])
        )
    bridge = build_bridge(plan, entries, ref_for, readback, project_id, broll)
    summary = {
        "placed_clips": len(entries),
        "placed_broll_clips": len(broll),
        "total_frames": sum(e["durationFrames"] for e in entries),
        "removed_previous_clips": len(existing),
    }
    return summary, bridge


def saved_bundle_state(projects_dir: Path | None = None) -> dict | None:
    """Clip counts of the currently OPEN OpenTake project's saved bundle.

    The open project is identified by its `.<name>.opentake-lock` file. Used
    as a staleness cross-check: the live MCP view and the saved file forked
    within one app session once (2026-09-01), silently reporting "no
    changes" for real edits. Best-effort — returns None when the bundle
    directory is not visible (unmounted) or no lock is present.
    """
    import os

    root = projects_dir or Path(os.environ.get("OPENTAKE_PROJECTS_DIR", ""))
    if not root or not root.is_dir():
        return None
    locks = sorted(
        root.glob(".*.opentake-lock"),
        key=lambda lock: lock.stat().st_mtime,
        reverse=True,
    )
    for lock in locks:
        # ".test.opentake.opentake-lock" -> bundle "test.opentake"
        bundle = root / lock.name[1:-len(".opentake-lock")]
        project_file = bundle / "project.json"
        if not project_file.is_file():
            continue
        try:
            saved = json.loads(project_file.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        tracks = saved.get("timeline", {}).get("tracks") or saved.get("tracks") or []
        counts = {"video": 0, "audio": 0}
        for track in tracks:
            kind = track.get("type")
            if kind in counts:
                counts[kind] += len(track.get("clips", []))
        return {
            "bundle": bundle.name,
            "saved_video_clips": counts["video"],
            "saved_audio_clips": counts["audio"],
            "saved_at": project_file.stat().st_mtime,
        }
    return None


def staleness_warning(readback: dict, saved: dict | None) -> dict | None:
    """Compare the live MCP timeline against the saved bundle; a clip-count
    mismatch means one of them is behind (either direction is possible, so
    the advice covers both)."""
    if saved is None:
        return None
    live = {"video": 0, "audio": 0}
    for track in readback.get("tracks", []):
        kind = track.get("type")
        if kind in live:
            live[kind] += len(track.get("clips", []))
    if (live["video"] == saved["saved_video_clips"]
            and live["audio"] == saved["saved_audio_clips"]):
        return None
    return {
        "bundle": saved["bundle"],
        "live_clips": live,
        "saved_clips": {
            "video": saved["saved_video_clips"],
            "audio": saved["saved_audio_clips"],
        },
        "advice": (
            "OpenTake's saved project and its live interface disagree — "
            "save in OpenTake first; if this warning persists, restart "
            "OpenTake and sync again."
        ),
    }
