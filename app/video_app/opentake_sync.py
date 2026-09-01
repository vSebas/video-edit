from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy


class SyncError(ValueError):
    """OpenTake readback cannot be represented safely as an edit plan."""


_SPEED_FIELDS = {
    "isreverse",
    "isreversed",
    "playbackspeed",
    "playbackrate",
    "rate",
    "reverse",
    "reversed",
    "speed",
    "speedrate",
    "timeremap",
    "timeremapping",
    "timescale",
    "timewarp",
}


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SyncError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise SyncError(f"{label} must be finite")
    return result


def _frame(value, label: str) -> int:
    number = _number(value, label)
    if number != int(number):
        raise SyncError(f"{label} must be an integer frame")
    return int(number)


def _seconds(frames: int, fps: float) -> float:
    return round(frames / fps, 6)


def _event_frames(event: dict, fps: float, label: str) -> tuple[int, int, int]:
    try:
        source_start = round(_number(event["source_start_seconds"], label) * fps)
        duration = round(_number(event["duration_seconds"], label) * fps)
        timeline_start = round(_number(event["timeline_start_seconds"], label) * fps)
    except KeyError as exc:
        raise SyncError(f"{label} is missing {exc.args[0]}") from exc
    return source_start, source_start + duration, timeline_start


def _single_track(document: dict, kind: str, label: str) -> dict:
    tracks = document.get("tracks")
    if not isinstance(tracks, list):
        raise SyncError(f"{label} tracks must be a list")
    matches = [track for track in tracks if track.get("kind") == kind]
    if len(matches) != 1:
        raise SyncError(f"{label} must contain exactly one {kind} track")
    if not isinstance(matches[0].get("events"), list):
        raise SyncError(f"{label} {kind} events must be a list")
    return matches[0]


def _readback_track(readback: dict, kind: str) -> dict:
    tracks = readback.get("tracks")
    if not isinstance(tracks, list):
        raise SyncError("readback tracks must be a list")
    matches = [track for track in tracks if track.get("type") == kind]
    if len(matches) != 1:
        raise SyncError(f"readback must contain exactly one {kind} track")
    if not isinstance(matches[0].get("clips"), list):
        raise SyncError(f"readback {kind} clips must be a list")
    return matches[0]


def _check_speed_fields(clip: dict, label: str) -> None:
    for key in clip:
        normalized = "".join(char for char in key.lower() if char.isalnum())
        if (
            normalized in _SPEED_FIELDS
            or "speed" in normalized
            or "reverse" in normalized
        ):
            raise SyncError(f"unsupported speed/reverse field {key!r} on {label}")


def _normalize_clip(clip: dict, kind: str, index: int, total_frames: int) -> dict:
    if not isinstance(clip, dict):
        raise SyncError(f"readback {kind} clip {index} must be an object")
    clip_id = clip.get("clipId")
    if not isinstance(clip_id, str) or not clip_id:
        raise SyncError(f"readback {kind} clip {index} has no clipId")
    label = f"{kind} clip {clip_id}"
    _check_speed_fields(clip, label)
    media_ref = clip.get("mediaRef")
    if not isinstance(media_ref, str) or not media_ref:
        raise SyncError(f"{label} has no mediaRef")
    start = _frame(clip.get("startFrame"), f"{label} startFrame")
    duration = _frame(clip.get("durationFrames"), f"{label} durationFrames")
    trim_start = _frame(clip.get("trimStartFrame", 0), f"{label} trimStartFrame")
    trim_end = _frame(clip.get("trimEndFrame", 0), f"{label} trimEndFrame")
    if start < 0 or trim_start < 0 or trim_end < 0:
        raise SyncError(f"{label} frame values must be non-negative")
    if duration <= 0:
        raise SyncError(f"{label} durationFrames must be positive")
    if start + duration > total_frames:
        raise SyncError(
            f"{label} ends at frame {start + duration}, beyond "
            f"totalFrames {total_frames}"
        )
    return {
        "clip_id": clip_id,
        "link_group_id": clip.get("linkGroupId"),
        "media_ref": media_ref,
        "start": start,
        "duration": duration,
        "trim_start": trim_start,
        "trim_end": trim_end,
        "source_end": trim_start + duration,
    }


def _geometry(clip: dict) -> tuple:
    return (
        clip["media_ref"],
        clip["start"],
        clip["duration"],
        clip["trim_start"],
        clip["trim_end"],
    )


def _suffix(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("a") + remainder) + result
    return result


def _rebuilt_event(original: dict, event_id: str, clip: dict, fps: float) -> dict:
    event = deepcopy(original)
    source_start = _seconds(clip["trim_start"], fps)
    duration = _seconds(clip["duration"], fps)
    event.update(
        {
            "event_id": event_id,
            "source_start_seconds": source_start,
            "source_end_seconds": round(source_start + duration, 6),
            "timeline_start_seconds": _seconds(clip["start"], fps),
            "duration_seconds": duration,
            "playback_rate": 1.0,
        }
    )
    return event


def _split_detail(info: dict, descendants: list[dict]) -> str:
    pieces = ", ".join(
        f"{item['video_event_id']} source [{item['video']['trim_start']},"
        f"{item['video']['source_end']}) at timeline {item['video']['start']}"
        for item in descendants
    )
    by_source = sorted(descendants, key=lambda item: item["video"]["trim_start"])
    gaps = []
    cursor = info["source_start"]
    for item in by_source:
        clip = item["video"]
        if clip["trim_start"] > cursor:
            gaps.append(f"[{cursor},{clip['trim_start']})")
        cursor = max(cursor, clip["source_end"])
    if cursor < info["source_end"]:
        gaps.append(f"[{cursor},{info['source_end']})")
    gap_detail = f"; uncovered source frames {', '.join(gaps)}" if gaps else ""
    return f"split into {pieces}{gap_detail}"


def _build_diff(infos: list[dict], descendants: dict[str, list[dict]]) -> list[dict]:
    changes = []
    for info in infos:
        event_id = info["event_id"]
        items = descendants.get(event_id, [])
        if not items:
            changes.append(
                {
                    "kind": "deleted",
                    "event_id": event_id,
                    "detail": (
                        f"removed source frames [{info['source_start']},"
                        f"{info['source_end']}) from timeline frame "
                        f"{info['timeline_start']}"
                    ),
                }
            )
            continue
        if len(items) > 1:
            changes.append(
                {
                    "kind": "split",
                    "event_id": event_id,
                    "detail": _split_detail(info, items),
                }
            )
            continue

        clip = items[0]["video"]
        base = {"event_id": event_id, "clip_id": clip["clip_id"]}
        source_changed = (
            clip["trim_start"] != info["source_start"]
            or clip["source_end"] != info["source_end"]
        )
        moved = clip["start"] != info["timeline_start"]
        if source_changed:
            changes.append(
                {
                    "kind": "trimmed",
                    **base,
                    "detail": (
                        f"source frames [{info['source_start']},{info['source_end']}) "
                        f"-> [{clip['trim_start']},{clip['source_end']})"
                    ),
                }
            )
        if moved:
            original_end = info["timeline_start"] + (
                info["source_end"] - info["source_start"]
            )
            changes.append(
                {
                    "kind": "moved",
                    **base,
                    "detail": (
                        f"timeline frames [{info['timeline_start']},{original_end}) -> "
                        f"[{clip['start']},{clip['start'] + clip['duration']})"
                    ),
                }
            )
        if not source_changed and not moved:
            changes.append(
                {
                    "kind": "unchanged",
                    **base,
                    "detail": (
                        f"source frames [{info['source_start']},{info['source_end']}) "
                        f"at timeline frame {info['timeline_start']}"
                    ),
                }
            )
    return changes


def timeline_to_candidate_plan(
    plan: dict, bridge: dict, readback: dict
) -> tuple[dict, list[dict]]:
    """Convert one supported OpenTake timeline readback into a plan revision."""
    if plan.get("schema_version") != "edit-plan.v1":
        raise SyncError("plan schema_version must be edit-plan.v1")
    if bridge.get("schema_version") != "opentake-bridge.v1":
        raise SyncError("bridge schema_version must be opentake-bridge.v1")

    plan_revision = _frame(plan.get("revision", 1), "plan revision")
    bridge_revision = _frame(bridge.get("plan_revision"), "bridge plan_revision")
    if plan_revision != bridge_revision:
        raise SyncError(
            f"plan revision mismatch: plan is {plan_revision}, "
            f"bridge is {bridge_revision}"
        )

    project = plan.get("project")
    if not isinstance(project, dict):
        raise SyncError("plan project must be an object")
    fps = _number(project.get("fps"), "plan fps")
    bridge_fps = _number(bridge.get("fps"), "bridge fps")
    readback_fps = _number(readback.get("fps"), "readback fps")
    if fps <= 0:
        raise SyncError("plan fps must be positive")
    if fps != bridge_fps or fps != readback_fps:
        raise SyncError(
            f"fps mismatch: plan={fps:g}, bridge={bridge_fps:g}, "
            f"readback={readback_fps:g}"
        )

    width = _frame(project.get("width"), "plan width")
    height = _frame(project.get("height"), "plan height")
    readback_width = _frame(readback.get("width"), "readback width")
    readback_height = _frame(readback.get("height"), "readback height")
    if (width, height) != (readback_width, readback_height):
        raise SyncError(
            "readback dimensions mismatch: "
            f"plan={width}x{height}, readback={readback_width}x{readback_height}"
        )

    total_frames = _frame(readback.get("totalFrames"), "readback totalFrames")
    if total_frames <= 0:
        raise SyncError("readback totalFrames must be positive")

    video_track = _single_track(plan, "video", "plan")
    audio_track = _single_track(plan, "audio", "plan")
    original_video = video_track["events"]
    original_audio = audio_track["events"]
    if len(original_video) != len(original_audio):
        raise SyncError("plan video/audio event counts do not match")

    video_by_id = {}
    audio_for_video = {}
    audio_event_ids = set()
    for video_event, audio_event in zip(original_video, original_audio):
        event_id = video_event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise SyncError("plan video event has no event_id")
        if event_id in video_by_id:
            raise SyncError(f"duplicate plan video event_id {event_id}")
        audio_event_id = audio_event.get("event_id")
        if not isinstance(audio_event_id, str) or not audio_event_id:
            raise SyncError(f"linked audio for {event_id} has no event_id")
        if audio_event_id in audio_event_ids:
            raise SyncError(f"duplicate plan audio event_id {audio_event_id}")
        audio_event_ids.add(audio_event_id)
        if _number(video_event.get("playback_rate", 1.0), event_id) != 1.0:
            raise SyncError(f"unsupported playback_rate on plan event {event_id}")
        if _number(audio_event.get("playback_rate", 1.0), event_id) != 1.0:
            raise SyncError(f"unsupported playback_rate on linked audio for {event_id}")
        if _event_frames(video_event, fps, event_id) != _event_frames(
            audio_event, fps, f"linked audio for {event_id}"
        ) or video_event.get("asset_id") != audio_event.get("asset_id"):
            raise SyncError(f"plan video/audio geometry does not match for {event_id}")
        video_by_id[event_id] = video_event
        audio_for_video[event_id] = audio_event

    media = bridge.get("media")
    if not isinstance(media, dict):
        raise SyncError("bridge media must be an asset_id-to-mediaRef object")
    asset_for_ref = {}
    for asset_id, media_ref in media.items():
        if not isinstance(asset_id, str) or not isinstance(media_ref, str):
            raise SyncError("bridge media identities must be strings")
        if media_ref in asset_for_ref:
            raise SyncError(f"ambiguous bridge mediaRef {media_ref}")
        asset_for_ref[media_ref] = asset_id

    bridge_events = bridge.get("events")
    if not isinstance(bridge_events, list):
        raise SyncError("bridge events must be a list")
    infos_by_id = {}
    exact_by_clip = {}
    infos_by_ref = defaultdict(list)
    for item in bridge_events:
        if not isinstance(item, dict):
            raise SyncError("bridge event must be an object")
        event_id = item.get("event_id")
        clip_id = item.get("clip_id")
        if event_id not in video_by_id:
            raise SyncError(f"bridge references unknown plan event {event_id}")
        if event_id in infos_by_id:
            raise SyncError(f"duplicate bridge event {event_id}")
        if not isinstance(clip_id, str) or not clip_id:
            raise SyncError(f"bridge event {event_id} has no clip_id")
        if clip_id in exact_by_clip:
            raise SyncError(f"duplicate bridge clip_id {clip_id}")
        original = video_by_id[event_id]
        asset_id = original.get("asset_id")
        media_ref = media.get(asset_id)
        if not isinstance(media_ref, str):
            raise SyncError(f"bridge has no mediaRef for plan asset {asset_id}")
        source_start = _frame(
            item.get("source_start_frame"),
            f"bridge event {event_id} source_start_frame",
        )
        source_end = _frame(
            item.get("source_end_frame"), f"bridge event {event_id} source_end_frame"
        )
        timeline_start = _frame(
            item.get("timeline_start_frame"),
            f"bridge event {event_id} timeline_start_frame",
        )
        if source_start < 0 or source_end <= source_start or timeline_start < 0:
            raise SyncError(f"bridge event {event_id} has an invalid frame envelope")
        expected_start, expected_end, expected_timeline = _event_frames(
            original, fps, f"plan event {event_id}"
        )
        if (source_start, source_end, timeline_start) != (
            expected_start,
            expected_end,
            expected_timeline,
        ):
            raise SyncError(f"bridge envelope does not match plan event {event_id}")
        info = {
            "event_id": event_id,
            "clip_id": clip_id,
            "media_ref": media_ref,
            "asset_id": asset_id,
            "source_start": source_start,
            "source_end": source_end,
            "timeline_start": timeline_start,
            "video_event": original,
            "audio_event": audio_for_video[event_id],
        }
        infos_by_id[event_id] = info
        exact_by_clip[clip_id] = info
        infos_by_ref[media_ref].append(info)

    missing_bridge_events = [
        event_id for event_id in video_by_id if event_id not in infos_by_id
    ]
    if missing_bridge_events:
        raise SyncError(
            "bridge does not cover plan events: " + ", ".join(missing_bridge_events)
        )
    infos = [infos_by_id[event["event_id"]] for event in original_video]

    readback_video = _readback_track(readback, "video")
    readback_audio = _readback_track(readback, "audio")
    for track in readback.get("tracks", []):
        if track.get("type") not in {"video", "audio"} and track.get("clips"):
            raise SyncError(
                f"unsupported non-empty readback track {track.get('type')!r}"
            )
    video_clips = [
        _normalize_clip(clip, "video", index, total_frames)
        for index, clip in enumerate(readback_video["clips"])
    ]
    audio_clips = [
        _normalize_clip(clip, "audio", index, total_frames)
        for index, clip in enumerate(readback_audio["clips"])
    ]
    if len({clip["clip_id"] for clip in video_clips}) != len(video_clips):
        raise SyncError("readback contains duplicate video clipId values")
    if len({clip["clip_id"] for clip in audio_clips}) != len(audio_clips):
        raise SyncError("readback contains duplicate audio clipId values")
    for kind, clips in (("video", video_clips), ("audio", audio_clips)):
        for clip in clips:
            if clip["media_ref"] not in asset_for_ref:
                raise SyncError(
                    f"unknown mediaRef {clip['media_ref']} on "
                    f"{kind} clip {clip['clip_id']}"
                )

    audio_by_group = defaultdict(list)
    for clip in audio_clips:
        audio_by_group[clip["link_group_id"]].append(clip)
    video_groups = set()
    paired_audio_ids = set()
    paired = {}
    for clip in video_clips:
        group = clip["link_group_id"]
        if not isinstance(group, str) or not group:
            raise SyncError(
                f"missing linked audio: video clip {clip['clip_id']} "
                "has no linkGroupId"
            )
        if group in video_groups:
            raise SyncError(
                f"ambiguous linked audio: multiple video clips use group {group}"
            )
        video_groups.add(group)
        partners = audio_by_group.get(group, [])
        if not partners:
            raise SyncError(f"missing linked audio for video clip {clip['clip_id']}")
        if len(partners) != 1:
            raise SyncError(
                f"ambiguous linked audio for video clip {clip['clip_id']}: "
                f"{len(partners)} partners"
            )
        partner = partners[0]
        if _geometry(clip) != _geometry(partner):
            raise SyncError(
                f"linked audio geometry mismatch for video clip {clip['clip_id']}"
            )
        paired[clip["clip_id"]] = partner
        paired_audio_ids.add(partner["clip_id"])
    orphan_audio = [
        clip["clip_id"]
        for clip in audio_clips
        if clip["clip_id"] not in paired_audio_ids
    ]
    if orphan_audio:
        raise SyncError(
            "audio clips have no linked video partner: " + ", ".join(orphan_audio)
        )

    descendants = defaultdict(list)
    for clip in video_clips:
        media_ref = clip["media_ref"]
        asset_id = asset_for_ref.get(media_ref)
        if asset_id is None:
            raise SyncError(
                f"unknown mediaRef {media_ref} on video clip {clip['clip_id']}"
            )
        exact = exact_by_clip.get(clip["clip_id"])
        if exact is not None:
            if exact["media_ref"] != media_ref:
                raise SyncError(
                    f"video clip {clip['clip_id']} mediaRef conflicts with its "
                    "bridge identity"
                )
            contained = (
                exact["source_start"] <= clip["trim_start"]
                and clip["source_end"] <= exact["source_end"]
            )
            if not contained:
                raise SyncError(
                    f"video clip {clip['clip_id']} source range "
                    f"[{clip['trim_start']},{clip['source_end']}) is outside "
                    f"bridged event {exact['event_id']} envelope "
                    f"[{exact['source_start']},{exact['source_end']})"
                )
            matches = [exact]
        else:
            matches = [
                info
                for info in infos_by_ref[media_ref]
                if info["source_start"] <= clip["trim_start"]
                and clip["source_end"] <= info["source_end"]
            ]
        if not matches:
            raise SyncError(
                f"video clip {clip['clip_id']} source range "
                f"[{clip['trim_start']},{clip['source_end']}) for mediaRef {media_ref} "
                "is outside every bridge event envelope"
            )
        if len(matches) > 1:
            event_ids = ", ".join(info["event_id"] for info in matches)
            raise SyncError(
                f"ambiguous event attribution for video clip {clip['clip_id']}: "
                f"{event_ids}"
            )
        info = matches[0]
        if info["asset_id"] != asset_id:
            raise SyncError(f"bridge media identity mismatch for mediaRef {media_ref}")
        descendants[info["event_id"]].append(
            {"video": clip, "audio": paired[clip["clip_id"]]}
        )

    for info in infos:
        items = descendants.get(info["event_id"], [])
        by_source = sorted(items, key=lambda item: item["video"]["trim_start"])
        for previous, current in zip(by_source, by_source[1:]):
            if current["video"]["trim_start"] < previous["video"]["source_end"]:
                raise SyncError(
                    f"descendants of {info['event_id']} overlap in source frames"
                )
        items.sort(
            key=lambda item: (
                item["video"]["start"],
                item["video"]["trim_start"],
                item["video"]["clip_id"],
            )
        )
        split = len(items) > 1
        for index, item in enumerate(items):
            suffix = f"__{_suffix(index)}" if split else ""
            item["info"] = info
            item["video_event_id"] = f"{info['video_event']['event_id']}{suffix}"
            item["audio_event_id"] = f"{info['audio_event']['event_id']}{suffix}"

    generated_video_ids = [
        item["video_event_id"] for items in descendants.values() for item in items
    ]
    generated_audio_ids = [
        item["audio_event_id"] for items in descendants.values() for item in items
    ]
    if len(set(generated_video_ids)) != len(generated_video_ids):
        raise SyncError("sync would generate duplicate video event ids")
    if len(set(generated_audio_ids)) != len(generated_audio_ids):
        raise SyncError("sync would generate duplicate audio event ids")

    ordered = sorted(
        (item for items in descendants.values() for item in items),
        key=lambda item: (
            item["video"]["start"],
            item["video"]["trim_start"],
            item["video"]["clip_id"],
        ),
    )
    rebuilt_video = []
    rebuilt_audio = []
    for item in ordered:
        info = item["info"]
        video_event = _rebuilt_event(
            info["video_event"], item["video_event_id"], item["video"], fps
        )
        audio_event = _rebuilt_event(
            info["audio_event"], item["audio_event_id"], item["video"], fps
        )
        video_event["asset_id"] = info["asset_id"]
        audio_event["asset_id"] = info["asset_id"]
        rebuilt_video.append(video_event)
        rebuilt_audio.append(audio_event)

    candidate = deepcopy(plan)
    for track in candidate["tracks"]:
        if track.get("kind") == "video":
            track["events"] = rebuilt_video
        elif track.get("kind") == "audio":
            track["events"] = rebuilt_audio
    candidate["revision"] = plan_revision + 1
    candidate["project"]["duration_seconds"] = _seconds(total_frames, fps)
    return candidate, _build_diff(infos, descendants)
