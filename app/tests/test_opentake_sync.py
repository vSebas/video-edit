from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from video_app.opentake_sync import SyncError, timeline_to_candidate_plan
from video_app.planning import validate_edit_plan


FIXTURES = Path(__file__).parent / "fixtures" / "opentake_sync"
SCHEMA = Path(__file__).parents[1] / "schemas" / "edit-plan.schema.json"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def track(document: dict, kind: str) -> dict:
    return next(item for item in document["tracks"] if item["kind"] == kind)


def readback_track(readback: dict, kind: str) -> dict:
    return next(item for item in readback["tracks"] if item["type"] == kind)


def event_frames(event: dict, fps: int) -> tuple[int, int, int]:
    start = round(event["source_start_seconds"] * fps)
    duration = round(event["duration_seconds"] * fps)
    timeline = round(event["timeline_start_seconds"] * fps)
    return start, start + duration, timeline


def validate(candidate: dict) -> None:
    inventory = load_fixture("inventory.json")
    validate_edit_plan(candidate, SCHEMA, {"inventory": inventory})


def linked_pair(readback: dict, video_index: int = 0) -> tuple[dict, dict]:
    video = readback_track(readback, "video")["clips"][video_index]
    audio = next(
        clip
        for clip in readback_track(readback, "audio")["clips"]
        if clip.get("linkGroupId") == video.get("linkGroupId")
    )
    return video, audio


def test_untouched_readback_round_trips_semantically() -> None:
    plan = load_fixture("plan.json")
    original = deepcopy(plan)
    candidate, diff = timeline_to_candidate_plan(
        plan, load_fixture("bridge.json"), load_fixture("readback-untouched.json")
    )

    assert plan == original
    assert candidate["revision"] == plan["revision"] + 1
    assert track(candidate, "title") == track(plan, "title")
    assert candidate["project"]["duration_seconds"] == 78.2

    for kind in ("video", "audio"):
        expected = track(plan, kind)["events"]
        actual = track(candidate, kind)["events"]
        assert [event["event_id"] for event in actual] == [
            event["event_id"] for event in expected
        ]
        for before, after in zip(expected, actual):
            assert event_frames(after, 30) == event_frames(before, 30)
            assert after["intent"] == before["intent"]
            assert after["confidence"] == before["confidence"]

    assert len(diff) == 22
    assert {item["kind"] for item in diff} == {"unchanged"}
    validate(candidate)


def test_cleanup_readback_becomes_split_and_shifted_plan() -> None:
    plan = load_fixture("plan.json")
    candidate, diff = timeline_to_candidate_plan(
        plan, load_fixture("bridge.json"), load_fixture("readback-cleanup.json")
    )
    video = track(candidate, "video")["events"]
    audio = track(candidate, "audio")["events"]

    assert len(video) == len(audio) == 23
    assert candidate["project"]["duration_seconds"] == round(2314 / 30, 6)
    assert track(candidate, "title") == track(plan, "title")

    descendants = [event for event in video if event["event_id"].startswith("v18_")]
    assert [event["event_id"] for event in descendants] == [
        "v18_startup_call__a",
        "v18_startup_call__b",
    ]
    assert [event_frames(event, 30)[:2] for event in descendants] == [
        (0, 59),
        (91, 754),
    ]
    assert 91 - 59 == 32

    original_video = {
        event["event_id"]: event for event in track(plan, "video")["events"]
    }
    for event in video:
        if event["event_id"].startswith("v18_"):
            continue
        before = original_video[event["event_id"]]
        assert event_frames(event, 30)[:2] == event_frames(before, 30)[:2]
        assert event["intent"] == before["intent"]
        assert event["confidence"] == before["confidence"]

    expected_moved = {
        "v19_startup_call",
        "v20_startup_call",
        "v21_closing",
        "v22_closing",
    }
    assert [item["event_id"] for item in diff if item["kind"] == "split"] == [
        "v18_startup_call"
    ]
    moved = {item["event_id"] for item in diff if item["kind"] == "moved"}
    assert moved == expected_moved
    assert len([item for item in diff if item["kind"] == "unchanged"]) == 17
    assert not [item for item in diff if item["kind"] in {"trimmed", "deleted"}]

    for video_event, audio_event in zip(video, audio):
        assert video_event["event_id"].replace("v", "a", 1) == audio_event["event_id"]
        for field in (
            "asset_id",
            "source_start_seconds",
            "source_end_seconds",
            "timeline_start_seconds",
            "duration_seconds",
        ):
            assert video_event[field] == audio_event[field]
        assert audio_event["volume_db"] == 0.0
    validate(candidate)


def test_diff_reports_trimmed_and_deleted_events() -> None:
    readback = load_fixture("readback-untouched.json")
    video, audio = linked_pair(readback)
    video["trimStartFrame"] = audio["trimStartFrame"] = 391
    video["durationFrames"] = audio["durationFrames"] = 68

    removed = readback_track(readback, "video")["clips"].pop(1)
    audio_clips = readback_track(readback, "audio")["clips"]
    audio_clips[:] = [
        clip for clip in audio_clips if clip["linkGroupId"] != removed["linkGroupId"]
    ]

    candidate, diff = timeline_to_candidate_plan(
        load_fixture("plan.json"), load_fixture("bridge.json"), readback
    )

    assert len(track(candidate, "video")["events"]) == 21
    assert [item["event_id"] for item in diff if item["kind"] == "trimmed"] == [
        "v01_failed_intros"
    ]
    assert [item["event_id"] for item in diff if item["kind"] == "deleted"] == [
        "v02_failed_intros"
    ]
    validate(candidate)


def test_unknown_media_ref_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    video, _ = linked_pair(readback)
    video["mediaRef"] = "unknown-ref"
    with pytest.raises(SyncError, match="unknown mediaRef unknown-ref"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_out_of_envelope_source_range_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    video, audio = linked_pair(readback)
    video["trimStartFrame"] = audio["trimStartFrame"] = 389
    with pytest.raises(SyncError, match="outside bridged event"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_missing_audio_partner_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    group = readback_track(readback, "video")["clips"][0]["linkGroupId"]
    audio = readback_track(readback, "audio")["clips"]
    audio[:] = [clip for clip in audio if clip.get("linkGroupId") != group]
    with pytest.raises(SyncError, match="missing linked audio"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_ambiguous_audio_partner_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    _, audio = linked_pair(readback)
    duplicate = deepcopy(audio)
    duplicate["clipId"] = "duplicate-audio"
    readback_track(readback, "audio")["clips"].append(duplicate)
    with pytest.raises(SyncError, match="ambiguous linked audio"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_audio_geometry_mismatch_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    _, audio = linked_pair(readback)
    audio["durationFrames"] -= 1
    with pytest.raises(SyncError, match="linked audio geometry mismatch"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_fps_mismatch_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    readback["fps"] = 29.97
    with pytest.raises(SyncError, match="fps mismatch"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_revision_mismatch_fails_closed() -> None:
    bridge = load_fixture("bridge.json")
    bridge["plan_revision"] += 1
    with pytest.raises(SyncError, match="plan revision mismatch"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), bridge, load_fixture("readback-untouched.json")
        )


@pytest.mark.parametrize(
    ("field", "value"), [("playbackRate", 1.0), ("reverse", False)]
)
def test_speed_or_reverse_field_fails_closed(field: str, value) -> None:
    readback = load_fixture("readback-untouched.json")
    readback_track(readback, "video")["clips"][0][field] = value
    with pytest.raises(SyncError, match="unsupported speed/reverse field"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


@pytest.mark.parametrize(("field", "value"), [("width", 1920), ("height", 1080)])
def test_dimension_mismatch_fails_closed(field: str, value: int) -> None:
    readback = load_fixture("readback-untouched.json")
    readback[field] = value
    with pytest.raises(SyncError, match="readback dimensions mismatch"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )


def test_ambiguous_descendant_attribution_fails_closed() -> None:
    readback = load_fixture("readback-untouched.json")
    video = readback_track(readback, "video")["clips"][18]
    group = video["linkGroupId"]
    audio = next(
        clip
        for clip in readback_track(readback, "audio")["clips"]
        if clip["linkGroupId"] == group
    )
    video["clipId"] = "new-overlapping-video"
    audio["clipId"] = "new-overlapping-audio"
    with pytest.raises(SyncError, match="ambiguous event attribution"):
        timeline_to_candidate_plan(
            load_fixture("plan.json"), load_fixture("bridge.json"), readback
        )
