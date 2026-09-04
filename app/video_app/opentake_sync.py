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


def _readback_tracks(readback: dict, kind: str) -> list[dict]:
    tracks = readback.get("tracks")
    if not isinstance(tracks, list):
        raise SyncError("readback tracks must be a list")
    matches = [track for track in tracks if track.get("type") == kind]
    if not matches:
        raise SyncError(f"readback must contain a {kind} track")
    for match in matches:
        if not isinstance(match.get("clips"), list):
            raise SyncError(f"readback {kind} clips must be a list")
    if len(matches) > 1 and kind == "video":
        # Which track is the A-roll decides which grounding rules apply;
        # that must never be settled by array order (cross-review 3).
        indices = [track.get("trackIndex") for track in matches]
        if any(not isinstance(index, int) for index in indices):
            raise SyncError(
                "readback video tracks must carry integer trackIndex values"
            )
        if len(set(indices)) != len(indices):
            raise SyncError("readback video tracks have duplicate trackIndex")
    return sorted(
        matches,
        key=lambda track: (
            track.get("trackIndex")
            if isinstance(track.get("trackIndex"), int)
            else len(tracks)
        ),
    )


def _plan_audio_tracks(plan: dict) -> tuple[dict, dict | None]:
    """The primary audio track plus an optional role-declared voiceover.

    Role-based selection: plans now also carry a music track (role 'music'),
    which OpenTake does not consume — the bed/recommendation is a render-time
    concern, so it is ignored here rather than treated as an illegal track.
    """
    tracks = plan.get("tracks")
    if not isinstance(tracks, list):
        raise SyncError("plan tracks must be a list")
    audio = [track for track in tracks if track.get("kind") == "audio"]
    primary = [t for t in audio if t.get("role") in (None, "primary")]
    voiceovers = [t for t in audio if t.get("role") == "voiceover"]
    if len(primary) != 1:
        raise SyncError("plan must contain exactly one primary audio track")
    if len(voiceovers) > 1:
        raise SyncError("plan must contain at most one voiceover track")
    for match in primary + voiceovers:
        if not isinstance(match.get("events"), list):
            raise SyncError("plan audio events must be a list")
    return primary[0], voiceovers[0] if voiceovers else None


def _plan_video_tracks(plan: dict) -> tuple[dict, dict | None]:
    """The primary video track plus an optional role-declared B-roll track."""
    tracks = plan.get("tracks")
    if not isinstance(tracks, list):
        raise SyncError("plan tracks must be a list")
    matches = [track for track in tracks if track.get("kind") == "video"]
    if not matches or len(matches) > 2:
        raise SyncError("plan must contain one video track plus at most one B-roll")
    for match in matches:
        if not isinstance(match.get("events"), list):
            raise SyncError("plan video events must be a list")
    if len(matches) == 2 and matches[1].get("role") != "broll":
        raise SyncError("a second plan video track must declare role 'broll'")
    return matches[0], matches[1] if len(matches) == 2 else None


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
    volume = clip.get("volume")
    if volume is not None:
        volume = _number(volume, f"{label} volume")
        if not 0.0 <= volume <= 1.0:
            raise SyncError(f"{label} volume must be within [0, 1]")
    return {
        "volume": volume,
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


def _volume_db(volume: float | None) -> float | None:
    """OpenTake clip volume (0..1, absent means unity) → plan volume_db."""
    if volume is None:
        return None
    if volume <= 0.0:
        return -96.0
    return round(20 * math.log10(volume), 2)


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
    plan: dict, bridge: dict, readback: dict,
    speech_words: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Convert one supported OpenTake timeline readback into a plan revision.

    When speech_words is supplied, the caption track is REGENERATED from ASR
    through the rebuilt clip geometry — a hand-rearrange in OpenTake changes
    which footage sits where, so captions must follow the footage rather than
    stay frozen at their old timeline positions (which would leave them over
    the wrong scene)."""
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

    video_track, plan_broll_track = _plan_video_tracks(plan)
    audio_track, plan_voiceover_track = _plan_audio_tracks(plan)
    original_video = video_track["events"]
    original_audio = audio_track["events"]
    original_broll = plan_broll_track["events"] if plan_broll_track else []
    broll_by_id = {}
    for event in original_broll:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise SyncError("plan B-roll event has no event_id")
        if event_id in broll_by_id:
            raise SyncError(f"duplicate plan B-roll event_id {event_id}")
        if _number(event.get("playback_rate", 1.0), event_id) != 1.0:
            raise SyncError(f"unsupported playback_rate on plan event {event_id}")
        broll_by_id[event_id] = event
    if len(original_video) != len(original_audio):
        raise SyncError("plan video/audio event counts do not match")

    video_by_id = {}
    audio_for_video = {}
    audio_event_ids = set()
    plan_mirrored = True
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
        if video_event.get("asset_id") != audio_event.get("asset_id"):
            raise SyncError(
                f"plan video/audio assets do not match for {event_id}"
            )
        if _event_frames(video_event, fps, event_id) != _event_frames(
            audio_event, fps, f"linked audio for {event_id}"
        ):
            plan_mirrored = False  # J/L plan: audio diverges by design
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

    # J/L plans need the partners' own identity: bridge audio_events map
    # each plan audio event to its placed clip and envelope.
    audio_env_for_video: dict[str, dict] = {}
    if not plan_mirrored:
        audio_bridge_events = bridge.get("audio_events")
        if not isinstance(audio_bridge_events, list):
            raise SyncError(
                "this plan has J/L cuts but the bridge has no audio_events — "
                "re-place the plan into OpenTake, then sync"
            )
        audio_env_by_event = {}
        for item in audio_bridge_events:
            if not isinstance(item, dict):
                raise SyncError("bridge audio event must be an object")
            audio_event_id = item.get("event_id")
            clip_id = item.get("clip_id")
            if not isinstance(clip_id, str) or not clip_id:
                raise SyncError(
                    f"bridge audio event {audio_event_id} has no clip_id"
                )
            if audio_event_id in audio_env_by_event:
                raise SyncError(f"duplicate bridge audio event {audio_event_id}")
            source_start = _frame(
                item.get("source_start_frame"),
                f"bridge audio event {audio_event_id} source_start_frame",
            )
            source_end = _frame(
                item.get("source_end_frame"),
                f"bridge audio event {audio_event_id} source_end_frame",
            )
            timeline_start = _frame(
                item.get("timeline_start_frame"),
                f"bridge audio event {audio_event_id} timeline_start_frame",
            )
            audio_env_by_event[audio_event_id] = {
                "clip_id": clip_id,
                "source_start": source_start,
                "source_end": source_end,
                "timeline_start": timeline_start,
            }
        for video_event in original_video:
            audio_event = audio_for_video[video_event["event_id"]]
            env = audio_env_by_event.get(audio_event.get("event_id"))
            if env is None:
                raise SyncError(
                    "bridge audio_events do not cover plan audio event "
                    f"{audio_event.get('event_id')}"
                )
            expected = _event_frames(
                audio_event, fps, f"plan audio event {audio_event['event_id']}"
            )
            if (env["source_start"], env["source_end"], env["timeline_start"]) != (
                expected[0], expected[1], expected[2],
            ):
                raise SyncError(
                    "bridge audio envelope does not match plan audio event "
                    f"{audio_event['event_id']}"
                )
            audio_env_for_video[video_event["event_id"]] = env

    broll_bridge_events = bridge.get("broll_events", [])
    if not isinstance(broll_bridge_events, list):
        raise SyncError("bridge broll_events must be a list")
    broll_exact_by_clip = {}
    broll_bridged_ids = set()
    for item in broll_bridge_events:
        if not isinstance(item, dict):
            raise SyncError("bridge broll event must be an object")
        event_id = item.get("event_id")
        clip_id = item.get("clip_id")
        if event_id not in broll_by_id:
            raise SyncError(f"bridge references unknown plan B-roll event {event_id}")
        if event_id in broll_bridged_ids:
            raise SyncError(f"duplicate bridge B-roll event {event_id}")
        broll_bridged_ids.add(event_id)
        if not isinstance(clip_id, str) or not clip_id:
            raise SyncError(f"bridge B-roll event {event_id} has no clip_id")
        if clip_id in broll_exact_by_clip or clip_id in exact_by_clip:
            raise SyncError(f"duplicate bridge clip_id {clip_id}")
        broll_exact_by_clip[clip_id] = broll_by_id[event_id]
    missing_broll = [eid for eid in broll_by_id if eid not in broll_bridged_ids]
    if missing_broll:
        raise SyncError(
            "bridge does not cover plan B-roll events: " + ", ".join(missing_broll)
        )

    original_voiceover = (
        plan_voiceover_track["events"] if plan_voiceover_track else []
    )
    voiceover_by_id = {}
    for event in original_voiceover:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise SyncError("plan voiceover event has no event_id")
        if event_id in voiceover_by_id:
            raise SyncError(f"duplicate plan voiceover event_id {event_id}")
        if _number(event.get("playback_rate", 1.0), event_id) != 1.0:
            raise SyncError(f"unsupported playback_rate on plan event {event_id}")
        voiceover_by_id[event_id] = event
    vo_bridge_events = bridge.get("voiceover_events", [])
    if not isinstance(vo_bridge_events, list):
        raise SyncError("bridge voiceover_events must be a list")
    vo_exact_by_clip = {}
    vo_bridged_ids = set()
    for item in vo_bridge_events:
        if not isinstance(item, dict):
            raise SyncError("bridge voiceover event must be an object")
        event_id = item.get("event_id")
        clip_id = item.get("clip_id")
        if event_id not in voiceover_by_id:
            raise SyncError(
                f"bridge references unknown plan voiceover event {event_id}"
            )
        if event_id in vo_bridged_ids:
            raise SyncError(f"duplicate bridge voiceover event {event_id}")
        vo_bridged_ids.add(event_id)
        if not isinstance(clip_id, str) or not clip_id:
            raise SyncError(f"bridge voiceover event {event_id} has no clip_id")
        if (clip_id in vo_exact_by_clip or clip_id in exact_by_clip
                or clip_id in broll_exact_by_clip):
            raise SyncError(f"duplicate bridge clip_id {clip_id}")
        vo_exact_by_clip[clip_id] = voiceover_by_id[event_id]
    missing_vo = [eid for eid in voiceover_by_id if eid not in vo_bridged_ids]
    if missing_vo:
        raise SyncError(
            "bridge does not cover plan voiceover events: " + ", ".join(missing_vo)
        )

    readback_videos = _readback_tracks(readback, "video")
    readback_audios = _readback_tracks(readback, "audio")
    if len(readback_videos) > 2:
        raise SyncError("readback has more than one B-roll video track")
    for track in readback.get("tracks", []):
        if track.get("type") not in {"video", "audio"} and track.get("clips"):
            raise SyncError(
                f"unsupported non-empty readback track {track.get('type')!r}"
            )
    video_clips = [
        _normalize_clip(clip, "video", index, total_frames)
        for index, clip in enumerate(readback_videos[0]["clips"])
    ]
    broll_clips = [
        _normalize_clip(clip, "broll", index, total_frames)
        for track in readback_videos[1:]
        for index, clip in enumerate(track["clips"])
    ]
    audio_clips = [
        _normalize_clip(clip, "audio", index, total_frames)
        for track in readback_audios
        for index, clip in enumerate(track["clips"])
    ]
    all_video_ids = [clip["clip_id"] for clip in video_clips + broll_clips]
    if len(set(all_video_ids)) != len(all_video_ids):
        raise SyncError("readback contains duplicate video clipId values")
    if len({clip["clip_id"] for clip in audio_clips}) != len(audio_clips):
        raise SyncError("readback contains duplicate audio clipId values")
    for kind, clips in (
        ("video", video_clips),
        ("broll", broll_clips),
        ("audio", audio_clips),
    ):
        for clip in clips:
            if clip["media_ref"] not in asset_for_ref:
                raise SyncError(
                    f"unknown mediaRef {clip['media_ref']} on "
                    f"{kind} clip {clip['clip_id']}"
                )

    broll_groups = {
        clip["link_group_id"]
        for clip in broll_clips
        if isinstance(clip["link_group_id"], str) and clip["link_group_id"]
    }
    broll_notes = []
    for clip in broll_clips:
        partners = [
            audio
            for audio in audio_clips
            if audio["link_group_id"] == clip["link_group_id"]
            and clip["link_group_id"] in broll_groups
        ]
        if partners:
            broll_notes.append(
                {
                    "kind": "broll_audio_ignored",
                    "clip_id": clip["clip_id"],
                    "detail": (
                        "B-roll is picture-only: linked audio "
                        f"{', '.join(a['clip_id'] for a in partners)} is ignored"
                    ),
                }
            )
    audio_clips = [
        clip for clip in audio_clips if clip["link_group_id"] not in broll_groups
    ]
    # Unlinked audio clips are the voiceover lane (placement creates them
    # unlinked; linked clips always belong to a primary or B-roll pair).
    vo_clips = [
        clip for clip in audio_clips
        if not isinstance(clip["link_group_id"], str) or not clip["link_group_id"]
    ]
    audio_clips = [
        clip for clip in audio_clips
        if isinstance(clip["link_group_id"], str) and clip["link_group_id"]
    ]
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
        if plan_mirrored:
            if _geometry(clip) != _geometry(partner):
                raise SyncError(
                    f"linked audio geometry mismatch for video clip "
                    f"{clip['clip_id']} — this plan is mirrored, so a "
                    "divergent pair means the timeline no longer matches "
                    "the placed revision; re-place and sync again"
                )
        elif partner["media_ref"] != clip["media_ref"]:
            raise SyncError(
                f"linked audio media mismatch for video clip {clip['clip_id']}"
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
        partner = paired[clip["clip_id"]]
        if not plan_mirrored:
            env = audio_env_for_video[info["event_id"]]
            known_identity = partner["clip_id"] == env["clip_id"]
            contained = (
                env["source_start"] <= partner["trim_start"]
                and partner["source_end"] <= env["source_end"]
            )
            if not known_identity and not contained:
                raise SyncError(
                    f"audio partner {partner['clip_id']} of video clip "
                    f"{clip['clip_id']} is outside the placed audio envelope "
                    f"[{env['source_start']},{env['source_end']})"
                )
        descendants[info["event_id"]].append(
            {"video": clip, "audio": partner}
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
            # A transition_out belongs to the ORIGINAL clip's outgoing seam, so
            # only the final descendant of a split inherits it — the internal
            # seams between the pieces are plain cuts, not repeated dips.
            item["keeps_transition"] = index == len(items) - 1

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
    volume_notes = []
    for item in ordered:
        info = item["info"]
        video_event = _rebuilt_event(
            info["video_event"], item["video_event_id"], item["video"], fps
        )
        if not item.get("keeps_transition"):
            video_event["transition_out"] = {"type": "cut", "duration_seconds": 0.0}
        audio_event = _rebuilt_event(
            info["audio_event"], item["audio_event_id"], item["audio"], fps
        )
        video_event["asset_id"] = info["asset_id"]
        audio_event["asset_id"] = info["asset_id"]
        new_db = _volume_db(item["audio"].get("volume"))
        old_db = info["audio_event"].get("volume_db")
        changed = (new_db if new_db is not None else 0.0) != (
            old_db if old_db is not None else 0.0
        )
        if changed:
            audio_event["volume_db"] = new_db
        if changed:
            volume_notes.append(
                {
                    "kind": "volume_changed",
                    "event_id": item["audio_event_id"],
                    "clip_id": item["audio"]["clip_id"],
                    "detail": (
                        f"{old_db if old_db is not None else 0}dB -> "
                        f"{new_db if new_db is not None else 0}dB"
                    ),
                }
            )
        rebuilt_video.append(video_event)
        rebuilt_audio.append(audio_event)

    primary_end = max(
        (clip["start"] + clip["duration"] for clip in video_clips), default=0
    )
    broll_diff = []
    rebuilt_broll = []
    used_broll_ids = set(broll_by_id)  # never reuse an existing identity
    added_count = 0
    surviving_broll_ids = set()
    for clip in sorted(
        broll_clips, key=lambda c: (c["start"], c["trim_start"], c["clip_id"])
    ):
        if clip["start"] + clip["duration"] > primary_end:
            raise SyncError(
                f"B-roll clip {clip['clip_id']} ends at frame "
                f"{clip['start'] + clip['duration']}, past the primary track end "
                f"{primary_end} — an overlay needs a base underneath"
            )
        original = broll_exact_by_clip.get(clip["clip_id"])
        if original is not None:
            event = _rebuilt_event(original, original["event_id"], clip, fps)
            event["asset_id"] = asset_for_ref[clip["media_ref"]]
            surviving_broll_ids.add(original["event_id"])
            expected = _event_frames(original, fps, original["event_id"])
            same = expected == (
                clip["trim_start"],
                clip["source_end"],
                clip["start"],
            )
            if not same:
                if (clip["trim_start"], clip["source_end"]) != expected[:2]:
                    broll_diff.append(
                        {
                            "kind": "broll_trimmed",
                            "event_id": original["event_id"],
                            "clip_id": clip["clip_id"],
                            "detail": (
                                f"source frames [{expected[0]},{expected[1]}) -> "
                                f"[{clip['trim_start']},{clip['source_end']})"
                            ),
                        }
                    )
                if clip["start"] != expected[2]:
                    broll_diff.append(
                        {
                            "kind": "broll_moved",
                            "event_id": original["event_id"],
                            "clip_id": clip["clip_id"],
                            "detail": (
                                f"timeline frame {expected[2]} -> {clip['start']}"
                            ),
                        }
                    )
        else:
            added_count += 1
            while f"bro-{added_count:02d}" in used_broll_ids:
                added_count += 1
            used_broll_ids.add(f"bro-{added_count:02d}")
            source_start = _seconds(clip["trim_start"], fps)
            duration = _seconds(clip["duration"], fps)
            event = {
                "event_id": f"bro-{added_count:02d}",
                "asset_id": asset_for_ref[clip["media_ref"]],
                "source_start_seconds": source_start,
                "source_end_seconds": round(source_start + duration, 6),
                "timeline_start_seconds": _seconds(clip["start"], fps),
                "duration_seconds": duration,
                "playback_rate": 1.0,
                "intent": "b-roll",
                "observed_content": None,
                "confidence": 0.5,
                "reframe": None,
                "transition_out": None,
                "text": None,
                "volume_db": None,
            }
            broll_diff.append(
                {
                    "kind": "broll_added",
                    "event_id": event["event_id"],
                    "clip_id": clip["clip_id"],
                    "detail": (
                        f"{event['asset_id']} source frames "
                        f"[{clip['trim_start']},{clip['source_end']}) at "
                        f"timeline frame {clip['start']}"
                    ),
                }
            )
        rebuilt_broll.append(event)
    if len({e["event_id"] for e in rebuilt_broll}) != len(rebuilt_broll):
        raise SyncError("sync would generate duplicate B-roll event ids")
    for event_id in broll_by_id:
        if event_id not in surviving_broll_ids:
            broll_diff.append(
                {
                    "kind": "broll_removed",
                    "event_id": event_id,
                    "detail": "B-roll clip removed from the overlay track",
                }
            )

    vo_diff = []
    rebuilt_vo = []
    used_vo_ids = set(voiceover_by_id)
    vo_added = 0
    surviving_vo_ids = set()
    for clip in sorted(
        vo_clips, key=lambda c: (c["start"], c["trim_start"], c["clip_id"])
    ):
        if clip["start"] + clip["duration"] > primary_end:
            raise SyncError(
                f"voiceover clip {clip['clip_id']} ends past the primary "
                f"track end {primary_end}"
            )
        new_db = _volume_db(clip.get("volume"))
        original = vo_exact_by_clip.get(clip["clip_id"])
        if original is not None:
            event = _rebuilt_event(original, original["event_id"], clip, fps)
            event["asset_id"] = asset_for_ref[clip["media_ref"]]
            old_db = original.get("volume_db")
            if (new_db if new_db is not None else 0.0) != (
                old_db if old_db is not None else 0.0
            ):
                event["volume_db"] = new_db
                vo_diff.append(
                    {
                        "kind": "volume_changed",
                        "event_id": original["event_id"],
                        "clip_id": clip["clip_id"],
                        "detail": (
                            f"{old_db if old_db is not None else 0}dB -> "
                            f"{new_db if new_db is not None else 0}dB"
                        ),
                    }
                )
            surviving_vo_ids.add(original["event_id"])
            expected = _event_frames(original, fps, original["event_id"])
            if expected != (clip["trim_start"], clip["source_end"], clip["start"]):
                vo_diff.append(
                    {
                        "kind": "voiceover_changed",
                        "event_id": original["event_id"],
                        "clip_id": clip["clip_id"],
                        "detail": (
                            f"source [{expected[0]},{expected[1]}) at "
                            f"{expected[2]} -> [{clip['trim_start']},"
                            f"{clip['source_end']}) at {clip['start']}"
                        ),
                    }
                )
        else:
            vo_added += 1
            while f"vo-{vo_added:02d}" in used_vo_ids:
                vo_added += 1
            used_vo_ids.add(f"vo-{vo_added:02d}")
            source_start = _seconds(clip["trim_start"], fps)
            duration = _seconds(clip["duration"], fps)
            event = {
                "event_id": f"vo-{vo_added:02d}",
                "asset_id": asset_for_ref[clip["media_ref"]],
                "source_start_seconds": source_start,
                "source_end_seconds": round(source_start + duration, 6),
                "timeline_start_seconds": _seconds(clip["start"], fps),
                "duration_seconds": duration,
                "playback_rate": 1.0,
                "intent": "voiceover",
                "observed_content": None,
                "confidence": 1.0,
                "reframe": None,
                "transition_out": None,
                "text": None,
                "volume_db": new_db,
            }
            vo_diff.append(
                {
                    "kind": "voiceover_added",
                    "event_id": event["event_id"],
                    "clip_id": clip["clip_id"],
                    "detail": (
                        f"{event['asset_id']} at timeline frame {clip['start']}"
                    ),
                }
            )
        rebuilt_vo.append(event)
    for event_id in voiceover_by_id:
        if event_id not in surviving_vo_ids:
            vo_diff.append(
                {
                    "kind": "voiceover_removed",
                    "event_id": event_id,
                    "detail": "voiceover clip removed",
                }
            )

    candidate = deepcopy(plan)
    candidate_videos = [t for t in candidate["tracks"] if t.get("kind") == "video"]
    primary_video = next(
        t for t in candidate_videos if t.get("role") in (None, "primary")
    )
    primary_video["events"] = rebuilt_video
    broll_track = next(
        (t for t in candidate_videos if t.get("role") == "broll"), None
    )
    if broll_track is not None:
        broll_track["events"] = rebuilt_broll
    elif rebuilt_broll:
        candidate["tracks"].append(
            {
                "track_id": "v2",
                "kind": "video",
                "role": "broll",
                "events": rebuilt_broll,
            }
        )
    # Role-based, not positional: a music track may sit among the audio tracks.
    # It is not touched by sync-back (OpenTake never carried it), so we must not
    # overwrite it with the voiceover or duplicate the voiceover past it.
    candidate_audios = [t for t in candidate["tracks"] if t.get("kind") == "audio"]
    primary_audio = next(
        t for t in candidate_audios if t.get("role") in (None, "primary")
    )
    primary_audio["events"] = rebuilt_audio
    vo_track = next(
        (t for t in candidate_audios if t.get("role") == "voiceover"), None
    )
    if vo_track is not None:
        vo_track["events"] = rebuilt_vo
    elif rebuilt_vo:
        candidate["tracks"].append(
            {
                "track_id": "a2",
                "kind": "audio",
                "role": "voiceover",
                "events": rebuilt_vo,
            }
        )
    candidate["revision"] = plan_revision + 1
    # Duration follows the primary story, not the canvas: an empty tail
    # after the last A-roll clip must not become black/silent seconds
    # (cross-review 4). totalFrames stays a bound check via _normalize_clip.
    candidate["project"]["duration_seconds"] = _seconds(primary_end, fps)
    # Open/close fades must fit the NEW duration — a hand-edit that shortened the
    # cut in OpenTake could otherwise leave a fade longer than half of it.
    if candidate.get("transitions"):
        from .planning import _scale_transitions

        candidate["transitions"] = _scale_transitions(
            candidate["transitions"].get("intro_fade_seconds") or 0.0,
            candidate["transitions"].get("outro_fade_seconds") or 0.0,
            candidate["project"]["duration_seconds"],
        )
    # Per-cut dips, the music bed span, and titles must all match the rearranged
    # geometry and the new duration — re-fit them exactly as an in-app structural
    # edit does (a dip whose seam vanished becomes a cut; a looping bed follows
    # the new length; a title that would overrun the shorter cut is clamped or
    # dropped). Voiceover ducking is not stored — the renderer derives it live
    # from the rebuilt voiceover events — so it follows automatically.
    from .plan_ops import (
        _clamp_titles_to_duration,
        _reconcile_dips,
        _refit_music_bed,
    )

    _reconcile_dips(candidate)
    _refit_music_bed(candidate)
    _clamp_titles_to_duration(candidate)
    # Captions follow the (possibly rearranged) footage: regenerate them from
    # ASR through the rebuilt geometry so they never sit over the wrong scene.
    caption_track = next(
        (t for t in candidate["tracks"] if t.get("kind") == "caption"), None
    )
    if caption_track is not None and speech_words is not None:
        from .planning import _caption_events_from_speech

        caption_track["events"] = _caption_events_from_speech(
            rebuilt_video, speech_words
        )
    # derived style grammar follows the mutation (stale numbers lie)
    from .planning import refresh_style_application

    refresh_style_application(candidate)
    diff = (
        _build_diff(infos, descendants)
        + volume_notes + broll_diff + broll_notes + vo_diff
    )
    return candidate, diff
