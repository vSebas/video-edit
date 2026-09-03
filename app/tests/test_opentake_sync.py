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


PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


class TestSyncEndpoints:
    """Preview/apply flow over the API, using the golden fixtures."""

    def _project(self, tmp_path):
        import json as _json
        import shutil
        from pathlib import Path as _P

        fx = _P(__file__).parent / "fixtures" / "opentake_sync"
        plan = _json.loads((fx / "plan.json").read_text())
        inventory = _json.loads((fx / "inventory.json").read_text())
        pid = "sync-endpoint-test"
        root = tmp_path / "runtime" / pid
        (root / "plan").mkdir(parents=True)
        (root / "plan" / "edit-plan.json").write_text(_json.dumps(plan))
        (root / "plan" / "media-inventory.json").write_text(_json.dumps(inventory))
        (root / "opentake-bridge.json").write_text((fx / "bridge.json").read_text())
        (root / "project.json").write_text(_json.dumps({
            "schema_version": "video-app-project.v1",
            "project_id": pid, "name": "sync test", "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z", "source_directory": "footage",
            "prompt": "", "status": "plan_ready", "footage_summary": "",
            "analysis": {"technical": "completed"},
            "inventory": inventory, "concepts": [], "selected_concept_id": None,
            "plan": plan, "outputs": {},
        }))
        return pid, fx


    @staticmethod
    def _live_mcp(readback):
        """Patch OpenTakeMcp so apply's live-fingerprint check sees the same
        timeline the preview used."""
        import contextlib

        import video_app.opentake_mcp as mcp_module

        class FakeMcp:
            def __init__(self, *a, **k): ...
            def get_timeline(self):
                return readback

        @contextlib.contextmanager
        def patched():
            original = mcp_module.OpenTakeMcp
            mcp_module.OpenTakeMcp = FakeMcp
            try:
                yield
            finally:
                mcp_module.OpenTakeMcp = original
        return patched()

    def test_preview_then_apply_installs_synced_revision(self, tmp_path) -> None:
        import json as _json
        from pathlib import Path as _P

        from fastapi.testclient import TestClient
        from video_app.config import Settings
        from video_app.main import create_app

        pid, fx = self._project(tmp_path)
        settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        readback = _json.loads((fx / "readback-cleanup.json").read_text())
        with TestClient(create_app(settings)) as client:
            preview = client.post(
                f"/api/projects/{pid}/opentake/sync", json={"readback": readback}
            )
            assert preview.status_code == 200, preview.text
            body = preview.json()
            assert body["duration_seconds"] == round(2314 / 30, 6)
            kinds = {c["kind"] for c in body["changes"]}
            assert "split" in kinds

            with self._live_mcp(readback):
                applied = client.post(f"/api/projects/{pid}/opentake/sync/apply")
            assert applied.status_code == 200, applied.text
            plan_now = _json.loads(
                (tmp_path / "runtime" / pid / "plan" / "edit-plan.json").read_text()
            )
            assert plan_now["revision"] == body["candidate_revision"]
            archived = tmp_path / "runtime" / pid / "plan" / "revisions"
            assert any(archived.glob("edit-plan.rev*.json"))
            # replay protection: applying again without a preview fails
            with self._live_mcp(readback):
                again = client.post(f"/api/projects/{pid}/opentake/sync/apply")
            assert again.status_code == 400

    def test_apply_rejects_when_plan_moved_after_preview(self, tmp_path) -> None:
        import json as _json

        from fastapi.testclient import TestClient
        from video_app.config import Settings
        from video_app.main import create_app

        pid, fx = self._project(tmp_path)
        settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        readback = _json.loads((fx / "readback-untouched.json").read_text())
        with TestClient(create_app(settings)) as client:
            assert client.post(
                f"/api/projects/{pid}/opentake/sync", json={"readback": readback}
            ).status_code == 200
            plan_path = tmp_path / "runtime" / pid / "plan" / "edit-plan.json"
            plan = _json.loads(plan_path.read_text())
            plan["revision"] = plan.get("revision", 1) + 1
            plan_path.write_text(_json.dumps(plan))
            with self._live_mcp(readback):
                stale = client.post(f"/api/projects/{pid}/opentake/sync/apply")
            assert stale.status_code == 400
            assert "changed since" in stale.json()["detail"]


class TestStalenessGuard:
    """A saved-bundle/live-view mismatch must warn, never stay silent."""

    def _bundle(self, tmp_path, video=20, audio=20):
        import json as _json
        bundle = tmp_path / "test.opentake"
        bundle.mkdir()
        (bundle / "project.json").write_text(_json.dumps({
            "timeline": {"tracks": [
                {"type": "video", "clips": [{}] * video},
                {"type": "audio", "clips": [{}] * audio},
            ]}
        }))
        (tmp_path / ".test.opentake.opentake-lock").write_text("")
        return tmp_path

    def test_mismatch_produces_warning(self, tmp_path) -> None:
        from video_app.opentake_bridge import saved_bundle_state, staleness_warning

        saved = saved_bundle_state(self._bundle(tmp_path, video=20, audio=20))
        assert saved and saved["saved_video_clips"] == 20
        live = {"tracks": [
            {"type": "video", "clips": [{}] * 22},
            {"type": "audio", "clips": [{}] * 22},
        ]}
        warning = staleness_warning(live, saved)
        assert warning and warning["live_clips"]["video"] == 22
        assert "save" in warning["advice"].lower()

    def test_matching_counts_stay_silent(self, tmp_path) -> None:
        from video_app.opentake_bridge import saved_bundle_state, staleness_warning

        saved = saved_bundle_state(self._bundle(tmp_path))
        live = {"tracks": [
            {"type": "video", "clips": [{}] * 20},
            {"type": "audio", "clips": [{}] * 20},
        ]}
        assert staleness_warning(live, saved) is None

    def test_missing_bundle_dir_is_none(self, tmp_path) -> None:
        from video_app.opentake_bridge import saved_bundle_state, staleness_warning

        assert saved_bundle_state(tmp_path / "nope") is None
        assert staleness_warning({"tracks": []}, None) is None


class TestCleanupCandidates:
    """Pure candidate computation, revision binding, and the endpoints."""

    def _words(self):
        return {"img_a": [
            {"word": "hola", "start": 1.0, "end": 1.3},
            {"word": "este", "start": 1.4, "end": 1.7},   # + 0.8s gap -> filler
            {"word": "vamos", "start": 2.5, "end": 2.8},
            {"word": "ya", "start": 4.5, "end": 4.7},     # 1.7s gap before -> dead air
        ]}

    def _clips(self):
        return [{"clipId": "c1", "asset_id": "img_a", "startFrame": 0,
                 "durationFrames": 150, "trimStartFrame": 0}]

    def test_filler_and_dead_air_found(self) -> None:
        from video_app.cleanup import candidates_for

        found = candidates_for(self._words(), self._clips(), 30)
        reasons = [c["reason"] for c in found]
        assert any("este" in r for r in reasons)
        assert any("silencio" in r for r in reasons)

    def test_este_without_gap_is_not_filler(self) -> None:
        from video_app.cleanup import candidates_for

        words = {"img_a": [
            {"word": "este", "start": 1.0, "end": 1.2},
            {"word": "camino", "start": 1.25, "end": 1.6},  # tight -> demonstrative
        ]}
        found = candidates_for(words, self._clips(), 30)
        assert not any("este" in c["reason"] for c in found)

    def test_fingerprint_changes_with_timeline(self) -> None:
        from video_app.cleanup import timeline_fingerprint

        a = {"tracks": [{"type": "video", "clips": [
            {"clipId": "x", "startFrame": 0, "durationFrames": 10}]}]}
        b = {"tracks": [{"type": "video", "clips": [
            {"clipId": "x", "startFrame": 0, "durationFrames": 12}]}]}
        assert timeline_fingerprint(a) != timeline_fingerprint(b)


class TestCleanupEndpoints:
    def test_candidates_and_stale_apply(self, tmp_path) -> None:
        import json as _json

        from fastapi.testclient import TestClient
        from video_app.config import Settings
        from video_app.main import create_app

        fx = __import__("pathlib").Path(__file__).parent / "fixtures" / "opentake_sync"
        pid = "cleanup-test"
        root = tmp_path / "runtime" / pid
        (root / "plan").mkdir(parents=True)
        runs = root / "analysis" / "runs" / "asr-live-abc"
        (runs / "raw").mkdir(parents=True)
        (runs / "manifest.json").write_text(_json.dumps(
            {"run_key": "asr-live-abc", "imported_at": "2026-09-01T00:00:00Z",
             "provider": {"adapter": "local-asr"}}))
        (runs / "raw" / "transcripts.json").write_text(_json.dumps({"transcripts": [
            {"asset_id": "img_2539", "segments": [{"words": [
                {"word": "hola", "start_seconds": 13.2, "end_seconds": 13.4},
                {"word": "listo", "start_seconds": 15.0, "end_seconds": 15.2},
            ]}]}]}))
        bridge = _json.loads((fx / "bridge.json").read_text())
        (root / "opentake-bridge.json").write_text(_json.dumps(bridge))
        inventory = _json.loads((fx / "inventory.json").read_text())
        (root / "project.json").write_text(_json.dumps({
            "schema_version": "video-app-project.v1", "project_id": pid,
            "name": "t", "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z", "source_directory": "footage",
            "prompt": "", "status": "plan_ready", "footage_summary": "",
            "analysis": {}, "inventory": inventory, "concepts": [],
            "selected_concept_id": None, "plan": None, "outputs": {},
        }))
        readback = _json.loads((fx / "readback-untouched.json").read_text())
        settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        with TestClient(create_app(settings)) as client:
            listed = client.post(
                f"/api/projects/{pid}/opentake/cleanup", json={"readback": readback}
            )
            assert listed.status_code == 200, listed.text
            body = listed.json()
            # the 13.2-15.2s gap inside v01 (source 13.0-15.3s) -> dead air
            assert any("silencio" in c["reason"] for c in body["candidates"])
            # apply always verifies against a LIVE readback fetched on the
            # mutation session — a caller-supplied readback is ignored
            # (cross-review BLOCKER 1). Simulate a GUI edit via a fake MCP.
            import video_app.opentake_mcp as mcp_module

            mutated = _json.loads(_json.dumps(readback))
            mutated["tracks"][0]["clips"][0]["durationFrames"] += 1

            class FakeMcp:
                def __init__(self, *a, **k): ...
                def get_timeline(self):
                    return mutated
                def tool(self, *a, **k):
                    raise AssertionError("must refuse before mutating")

            original = mcp_module.OpenTakeMcp
            mcp_module.OpenTakeMcp = FakeMcp
            try:
                stale = client.post(
                    f"/api/projects/{pid}/opentake/cleanup/apply",
                    json={"indices": [0], "readback": readback},
                )
            finally:
                mcp_module.OpenTakeMcp = original
            assert stale.status_code == 400
            assert "changed" in stale.json()["detail"]


class TestBrollSync:
    """P2: a second video track in OpenTake becomes a plan B-roll track."""

    def _load(self):
        import json
        fx = __import__("pathlib").Path(__file__).parent / "fixtures" / "opentake_sync"
        plan = json.loads((fx / "plan.json").read_text())
        bridge = json.loads((fx / "bridge.json").read_text())
        readback = json.loads((fx / "readback-untouched.json").read_text())
        return plan, bridge, readback

    @staticmethod
    def _add_v2(readback, clips, with_audio=True):
        readback["tracks"].append(
            {"track": "V2", "trackIndex": 2, "type": "video", "clips": clips}
        )
        if with_audio:
            audio = next(t for t in readback["tracks"] if t["type"] == "audio")
            for clip in clips:
                if clip.get("linkGroupId"):
                    audio["clips"].append({
                        "clipId": f"au-{clip['clipId']}",
                        "linkGroupId": clip["linkGroupId"],
                        "mediaRef": clip["mediaRef"],
                        "startFrame": clip["startFrame"],
                        "durationFrames": clip["durationFrames"],
                        "trimStartFrame": clip.get("trimStartFrame", 0),
                        "mediaType": "audio",
                    })

    def test_gui_added_broll_becomes_v2_event(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        self._add_v2(readback, [{
            "clipId": "brand-new", "linkGroupId": "lg-broll",
            "mediaRef": bridge["media"]["img_2540"],
            "startFrame": 120, "durationFrames": 90, "trimStartFrame": 30,
        }])
        candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        assert len(videos) == 2 and videos[1]["role"] == "broll"
        (event,) = videos[1]["events"]
        assert event["event_id"] == "bro-01"
        assert event["asset_id"] == "img_2540"
        assert event["timeline_start_seconds"] == 4.0
        assert event["duration_seconds"] == 3.0
        assert event["intent"] == "b-roll"
        kinds = {d["kind"] for d in diff}
        assert "broll_added" in kinds and "broll_audio_ignored" in kinds
        # the primary tracks are untouched by an overlay-only edit
        assert len(videos[0]["events"]) == 22
        audio = next(t for t in candidate["tracks"] if t["kind"] == "audio")
        assert len(audio["events"]) == 22

    def test_two_extra_video_tracks_rejected(self) -> None:
        import pytest
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        self._add_v2(readback, [], with_audio=False)
        readback["tracks"].append(
            {"track": "V3", "trackIndex": 3, "type": "video", "clips": []}
        )
        with pytest.raises(SyncError, match="more than one B-roll"):
            timeline_to_candidate_plan(plan, bridge, readback)

    def test_broll_past_primary_end_rejected(self) -> None:
        import pytest
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        readback["totalFrames"] += 300
        self._add_v2(readback, [{
            "clipId": "hangover", "linkGroupId": "lg-x",
            "mediaRef": bridge["media"]["img_2540"],
            "startFrame": 2340, "durationFrames": 90, "trimStartFrame": 0,
        }], with_audio=False)
        with pytest.raises(SyncError, match="past the primary track end"):
            timeline_to_candidate_plan(plan, bridge, readback)

    def _with_plan_broll(self, plan, bridge):
        plan["tracks"].append({
            "track_id": "v2", "kind": "video", "role": "broll", "events": [{
                "event_id": "bro-01", "asset_id": "img_2540",
                "source_start_seconds": 1.0, "source_end_seconds": 4.0,
                "timeline_start_seconds": 4.0, "duration_seconds": 3.0,
                "playback_rate": 1.0, "intent": "b-roll",
                "observed_content": None, "confidence": 0.5, "reframe": None,
                "transition_out": None, "text": None, "volume_db": None,
            }],
        })
        bridge["broll_events"] = [{
            "event_id": "bro-01", "clip_id": "placed-broll",
            "link_group_id": None, "source_start_frame": 30,
            "source_end_frame": 120, "timeline_start_frame": 120,
        }]

    def test_bridged_broll_keeps_identity_and_reports_trim(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        self._with_plan_broll(plan, bridge)
        self._add_v2(readback, [{
            "clipId": "placed-broll",
            "mediaRef": bridge["media"]["img_2540"],
            "startFrame": 120, "durationFrames": 60, "trimStartFrame": 30,
        }], with_audio=False)
        candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        (event,) = videos[1]["events"]
        assert event["event_id"] == "bro-01"
        assert event["duration_seconds"] == 2.0
        assert any(d["kind"] == "broll_trimmed" for d in diff)
        assert not any(d["kind"] == "broll_added" for d in diff)

    def test_broll_removed_in_gui_is_reported(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        self._with_plan_broll(plan, bridge)
        self._add_v2(readback, [], with_audio=False)
        candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        assert videos[1]["events"] == []
        assert any(d["kind"] == "broll_removed" for d in diff)

    def test_plan_broll_without_bridge_coverage_fails_closed(self) -> None:
        import pytest
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        self._with_plan_broll(plan, bridge)
        del bridge["broll_events"]
        self._add_v2(readback, [], with_audio=False)
        with pytest.raises(SyncError, match="does not cover plan B-roll"):
            timeline_to_candidate_plan(plan, bridge, readback)


class TestVolumeSync:
    """P3: clip volume set in the OpenTake GUI rides into the plan."""

    def _load(self):
        import json
        fx = __import__("pathlib").Path(__file__).parent / "fixtures" / "opentake_sync"
        return (
            json.loads((fx / "plan.json").read_text()),
            json.loads((fx / "bridge.json").read_text()),
            json.loads((fx / "readback-untouched.json").read_text()),
        )

    def test_volume_maps_to_db_and_is_reported(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        audio = next(t for t in readback["tracks"] if t["type"] == "audio")
        audio["clips"][0]["volume"] = 0.5
        audio["clips"][1]["volume"] = 0.0
        candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        events = next(
            t for t in candidate["tracks"] if t["kind"] == "audio"
        )["events"]
        by_start = sorted(events, key=lambda e: e["timeline_start_seconds"])
        assert by_start[0]["volume_db"] == -6.02
        assert by_start[1]["volume_db"] == -96.0
        # untouched clips keep the plan's stored value (0.0 == unity)
        assert by_start[2]["volume_db"] == 0.0
        changes = [d for d in diff if d["kind"] == "volume_changed"]
        assert len(changes) == 2

    def test_out_of_range_volume_fails_closed(self) -> None:
        import pytest
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan, bridge, readback = self._load()
        audio = next(t for t in readback["tracks"] if t["type"] == "audio")
        audio["clips"][0]["volume"] = 1.5
        with pytest.raises(SyncError, match="volume must be within"):
            timeline_to_candidate_plan(plan, bridge, readback)


class TestJLPreflight:
    def test_unrepresentable_layout_refused_before_touching_opentake(self) -> None:
        import json

        import pytest
        from video_app.opentake_bridge import BridgeError, place_plan

        fx = __import__("pathlib").Path(__file__).parent / "fixtures" / "opentake_sync"
        plan = json.loads((fx / "plan.json").read_text())
        inventory = json.loads((fx / "inventory.json").read_text())
        audio = next(t for t in plan["tracks"] if t["kind"] == "audio")
        # a GAP in the audio tiling cannot ride on linked pairs
        audio["events"][1]["timeline_start_seconds"] += 0.4
        audio["events"][1]["duration_seconds"] -= 0.4
        audio["events"][1]["source_start_seconds"] += 0.4

        class Untouchable:
            def __getattr__(self, name):  # any client call is a failure
                raise AssertionError(f"client.{name} was called")

        with pytest.raises(BridgeError, match="gaps"):
            place_plan(plan, inventory, "p", client=Untouchable())


class FakeOpenTake:
    """Scripted MCP client: enough of OpenTake's semantics for placement —
    linked-pair auto-creation for video media, unlinked clips for audio
    media, add_track with zone-partitioned append, volume set."""

    def __init__(self, media):
        self.media = media  # asset stem -> {"id", "kind"}
        self.tracks = [
            {"track": "V1", "trackIndex": 0, "type": "video", "clips": []},
            {"track": "A1", "trackIndex": 1, "type": "audio", "clips": []},
        ]
        self.counter = 0
        self.fps = 30

    def _next(self, prefix):
        self.counter += 1
        return f"{prefix}{self.counter:02d}"

    def get_timeline(self):
        end = max(
            (c["startFrame"] + c["durationFrames"]
             for t in self.tracks for c in t["clips"]),
            default=0,
        )
        return {
            "fps": self.fps, "width": 320, "height": 240,
            "totalFrames": end,
            "tracks": [
                {**t, "clips": [dict(c) for c in t["clips"]]}
                for t in self.tracks
            ],
        }

    def tool(self, name, arguments=None):
        arguments = arguments or {}
        if name == "list_projects":
            if getattr(self, "missing_bundle", False):
                return {"projects": []}
            return {"projects": [
                {"name": getattr(self, "bundle_name", "mi-vlog"),
                 "open": False},
            ]}
        if name == "open_project":
            self.opened_project = arguments["name"]
            return {"name": arguments["name"]}
        if name == "new_project":
            self.created_project = arguments["name"]
            self.opened_project = arguments["name"]
            return {"name": arguments["name"]}
        if name == "save_project":
            self.saved = True
            return {"savedTo": f"/fake/{getattr(self, 'opened_project', 'x')}.opentake"}
        if name == "import_media":
            from pathlib import Path as _P
            stem = _P(arguments["source"]["path"]).stem
            self.media[stem] = {"id": f"media-{stem}"}
            self.imported = getattr(self, "imported", []) + [stem]
            return {"assetId": f"media-{stem}"}
        if name == "set_project_settings":
            self.fps = arguments["fps"]
            self.width = arguments["width"]
            self.height = arguments["height"]
            return dict(arguments)
        if name == "get_media":
            return {"entries": [
                {"name": stem, "id": info["id"]}
                for stem, info in self.media.items()
            ]}
        if name == "remove_clips":
            ids = set(arguments["clipIds"])
            for track in self.tracks:
                track["clips"] = [
                    c for c in track["clips"] if c["clipId"] not in ids
                ]
            return {}
        if name == "add_track":
            kind = arguments["type"]
            if kind == "video":
                index = max(
                    i for i, t in enumerate(self.tracks)
                    if t["type"] == "video"
                ) + 1
            else:
                index = len(self.tracks)
            self.tracks.insert(
                index,
                {"track": f"{kind[0].upper()}?", "trackIndex": index,
                 "type": kind, "clips": []},
            )
            for i, track in enumerate(self.tracks):
                track["trackIndex"] = i
            return {"trackIndex": index}
        if name == "add_clips":
            for entry in arguments["entries"]:
                kind = next(
                    info["kind"] for info in self.media.values()
                    if info["id"] == entry["mediaRef"]
                )
                index = entry.get("trackIndex")
                clip = {
                    "clipId": self._next("c"),
                    "mediaRef": entry["mediaRef"],
                    "startFrame": entry["startFrame"],
                    "durationFrames": entry["durationFrames"],
                    "trimStartFrame": entry.get("trimStartFrame", 0),
                }
                if index is None and kind == "video":
                    group = self._next("lg")
                    clip["linkGroupId"] = group
                    self.tracks[0]["clips"].append(clip)
                    self._free_audio_track(clip).append({
                        **clip, "clipId": self._next("c"),
                        "mediaType": "audio",
                    })
                else:
                    target = self.tracks[index]
                    if target["type"] == "video":
                        clip["linkGroupId"] = self._next("lg")
                        # linked audio lands on the first FREE audio track
                        # over the span (real available_audio_track_index)
                        self._free_audio_track(clip).append({
                            **clip, "clipId": self._next("c"),
                            "mediaType": "audio",
                        })
                    target["clips"].append(clip)
            return {}
        if name == "set_clip_properties":
            timing = {"durationFrames", "trimStartFrame", "trimEndFrame", "speed"}
            changes_timing = bool(timing & set(arguments))
            for track in self.tracks:
                for clip in track["clips"]:
                    if clip["clipId"] not in arguments["clipIds"]:
                        continue
                    if changes_timing and clip.get("linkGroupId"):
                        partner_ids = {
                            c["clipId"] for t in self.tracks
                            for c in t["clips"]
                            if c.get("linkGroupId") == clip["linkGroupId"]
                        } - {clip["clipId"]}
                        if partner_ids - set(arguments["clipIds"]) and \
                                not arguments.get("allowLinkDivergence"):
                            raise AssertionError(
                                "fork refuses linked timing change without "
                                "allowLinkDivergence"
                            )
                    if "volume" in arguments:
                        clip["volume"] = arguments["volume"]
                    if "durationFrames" in arguments:
                        clip["durationFrames"] = arguments["durationFrames"]
                    if "trimStartFrame" in arguments:
                        clip["trimStartFrame"] = arguments["trimStartFrame"]
            self._assert_no_overlap()
            return {}
        if name == "move_clips":
            for m in arguments["moves"]:
                for track in self.tracks:
                    for clip in track["clips"]:
                        if clip["clipId"] == m["clipId"]:
                            clip["startFrame"] = m["toFrame"]
            self._assert_no_overlap()
            return {}
        raise AssertionError(f"unexpected tool {name}")

    def _free_audio_track(self, clip):
        span = (clip["startFrame"],
                clip["startFrame"] + clip["durationFrames"])
        for track in self.tracks:
            if track["type"] != "audio":
                continue
            busy = any(
                c["startFrame"] < span[1]
                and span[0] < c["startFrame"] + c["durationFrames"]
                for c in track["clips"]
            )
            if not busy:
                return track["clips"]
        index = len(self.tracks)
        self.tracks.append({
            "track": f"A{index}", "trackIndex": index, "type": "audio",
            "clips": [],
        })
        return self.tracks[-1]["clips"]

    def _assert_no_overlap(self):
        # Stricter than real OpenTake (which overwrites): the retiler
        # promises NO transient overlap, so any overlap is a bug.
        for track in self.tracks:
            ordered = sorted(track["clips"], key=lambda c: c["startFrame"])
            for a, b in zip(ordered, ordered[1:]):
                if b["startFrame"] < a["startFrame"] + a["durationFrames"]:
                    raise AssertionError(
                        f"overlap on {track['track']}: {a['clipId']}/{b['clipId']}"
                    )


def _mini_plan_with_voiceover():
    def event(eid, asset, src, start, dur, intent, volume=None):
        return {
            "event_id": eid, "asset_id": asset,
            "source_start_seconds": src,
            "source_end_seconds": round(src + dur, 6),
            "timeline_start_seconds": start, "duration_seconds": dur,
            "playback_rate": 1.0, "intent": intent,
            "observed_content": None, "confidence": 0.9, "reframe": None,
            "transition_out": None, "text": None, "volume_db": volume,
        }
    return {
        "schema_version": "edit-plan.v1",
        "generated_at": "2026-09-01T00:00:00Z",
        "benchmark_id": "t", "concept_id": "t", "revision": 1,
        "project": {"width": 320, "height": 240, "fps": 30,
                    "duration_seconds": 4.0, "background_color": "black"},
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [
                event("v01", "red", 0.0, 0.0, 4.0, "base")]},
            {"track_id": "a1", "kind": "audio", "events": [
                event("a01", "red", 0.0, 0.0, 4.0, "base")]},
            {"track_id": "t1", "kind": "title", "events": []},
            {"track_id": "v2", "kind": "video", "role": "broll", "events": [
                event("bro-01", "blue", 0.5, 1.0, 1.0, "b-roll")]},
            {"track_id": "a2", "kind": "audio", "role": "voiceover",
             "events": [event("vo-01", "memo", 0.0, 2.0, 1.5, "voiceover",
                              volume=-6.0)]},
        ],
    }


MINI_INVENTORY = {"assets": [
    {"asset_id": "red", "filename": "red.mp4", "media_type": "video",
     "duration_seconds": 6.0},
    {"asset_id": "blue", "filename": "blue.mp4", "media_type": "video",
     "duration_seconds": 6.0},
    {"asset_id": "memo", "filename": "memo.m4a", "media_type": "audio",
     "duration_seconds": 3.0},
]}


class TestFullPlacement:
    def test_placement_creates_tracks_and_places_broll_and_voiceover(self) -> None:
        from video_app.opentake_bridge import place_plan

        client = FakeOpenTake({
            "red": {"id": "ref-red", "kind": "video"},
            "blue": {"id": "ref-blue", "kind": "video"},
            "memo": {"id": "ref-memo", "kind": "audio"},
        })
        summary, bridge = place_plan(
            _mini_plan_with_voiceover(), MINI_INVENTORY, "p", client=client
        )
        assert summary["placed_clips"] == 1
        assert summary["placed_broll_clips"] == 1
        assert summary["placed_voiceover_clips"] == 1
        assert len(bridge["broll_events"]) == 1
        assert len(bridge["voiceover_events"]) == 1
        # voiceover clip landed unlinked with its volume set
        timeline = client.get_timeline()
        unlinked = [
            c for t in timeline["tracks"] if t["type"] == "audio"
            for c in t["clips"] if not c.get("linkGroupId")
        ]
        assert len(unlinked) == 1
        assert unlinked[0].get("volume") == round(10 ** (-6.0 / 20), 4)

    def test_positive_voiceover_gain_refused_in_preflight(self) -> None:
        import pytest
        from video_app.opentake_bridge import BridgeError, place_plan

        plan = _mini_plan_with_voiceover()
        plan["tracks"][4]["events"][0]["volume_db"] = 6.0

        class Untouchable:
            def __getattr__(self, name):
                raise AssertionError(f"client.{name} was called")

        with pytest.raises(BridgeError, match="gain"):
            place_plan(plan, MINI_INVENTORY, "p", client=Untouchable())


class TestVoiceoverSync:
    def test_round_trip_and_gui_added_voiceover(self) -> None:
        from video_app.opentake_bridge import place_plan
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan = _mini_plan_with_voiceover()
        client = FakeOpenTake({
            "red": {"id": "ref-red", "kind": "video"},
            "blue": {"id": "ref-blue", "kind": "video"},
            "memo": {"id": "ref-memo", "kind": "audio"},
        })
        _, bridge = place_plan(plan, MINI_INVENTORY, "p", client=client)
        readback = client.get_timeline()
        candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        audios = [t for t in candidate["tracks"] if t["kind"] == "audio"]
        assert audios[1]["role"] == "voiceover"
        (vo,) = audios[1]["events"]
        assert vo["event_id"] == "vo-01"  # identity preserved via bridge
        # untouched round trip: no voiceover changes reported
        assert not [d for d in diff if d["kind"].startswith("voiceover")]

        # a NEW unlinked audio clip (GUI-added voiceover) becomes vo-02
        audio_track = next(t for t in client.tracks if t["type"] == "audio")
        audio_track["clips"].append({
            "clipId": "gui-vo", "mediaRef": "ref-memo",
            "startFrame": 60, "durationFrames": 30, "trimStartFrame": 0,
        })
        candidate, diff = timeline_to_candidate_plan(
            plan, bridge, client.get_timeline()
        )
        audios = [t for t in candidate["tracks"] if t["kind"] == "audio"]
        ids = [e["event_id"] for e in audios[1]["events"]]
        assert "vo-02" in ids and "vo-01" in ids
        assert any(d["kind"] == "voiceover_added" for d in diff)

    def test_voiceover_past_primary_end_fails_closed(self) -> None:
        import pytest
        from video_app.opentake_bridge import place_plan
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan = _mini_plan_with_voiceover()
        client = FakeOpenTake({
            "red": {"id": "ref-red", "kind": "video"},
            "blue": {"id": "ref-blue", "kind": "video"},
            "memo": {"id": "ref-memo", "kind": "audio"},
        })
        _, bridge = place_plan(plan, MINI_INVENTORY, "p", client=client)
        audio_track = next(t for t in client.tracks if t["type"] == "audio")
        audio_track["clips"].append({
            "clipId": "hangover", "mediaRef": "ref-memo",
            "startFrame": 115, "durationFrames": 30, "trimStartFrame": 0,
        })
        with pytest.raises(SyncError, match="past the primary"):
            timeline_to_candidate_plan(plan, bridge, client.get_timeline())


class TestJLPlacement:
    """J/L plans place onto linked pairs via the no-overlap retiler."""

    def _mirrored_plan(self):
        def event(eid, asset, src, start, dur):
            return {
                "event_id": eid, "asset_id": asset,
                "source_start_seconds": src,
                "source_end_seconds": round(src + dur, 6),
                "timeline_start_seconds": start, "duration_seconds": dur,
                "playback_rate": 1.0, "intent": "base",
                "observed_content": None, "confidence": 0.9, "reframe": None,
                "transition_out": None, "text": None, "volume_db": None,
            }
        return {
            "schema_version": "edit-plan.v1",
            "generated_at": "2026-09-01T00:00:00Z",
            "benchmark_id": "t", "concept_id": "t", "revision": 1,
            "project": {"width": 320, "height": 240, "fps": 30,
                        "duration_seconds": 6.0,
                        "background_color": "black"},
            "tracks": [
                {"track_id": "v1", "kind": "video", "events": [
                    event("v01", "red", 0.0, 0.0, 2.0),
                    event("v02", "blue", 1.0, 2.0, 2.0),
                    event("v03", "red", 3.0, 4.0, 2.0)]},
                {"track_id": "a1", "kind": "audio", "events": [
                    event("a01", "red", 0.0, 0.0, 2.0),
                    event("a02", "blue", 1.0, 2.0, 2.0),
                    event("a03", "red", 3.0, 4.0, 2.0)]},
                {"track_id": "t1", "kind": "title", "events": []},
            ],
        }

    def _client(self):
        return FakeOpenTake({
            "red": {"id": "ref-red", "kind": "video"},
            "blue": {"id": "ref-blue", "kind": "video"},
        })

    def _place_and_check(self, plan):
        from video_app.opentake_bridge import place_plan

        client = self._client()
        summary, bridge = place_plan(plan, MINI_INVENTORY, "p", client=client)
        fps = plan["project"]["fps"]
        audio_events = next(
            t for t in plan["tracks"] if t["kind"] == "audio"
        )["events"]
        audio_clips = sorted(
            (c for t in client.tracks if t["type"] == "audio"
             for c in t["clips"]),
            key=lambda c: c["startFrame"],
        )
        for event, clip in zip(audio_events, audio_clips):
            assert clip["startFrame"] == round(
                event["timeline_start_seconds"] * fps), event["event_id"]
            assert clip["durationFrames"] == round(
                event["duration_seconds"] * fps), event["event_id"]
            assert clip["trimStartFrame"] == round(
                event["source_start_seconds"] * fps), event["event_id"]
            assert clip.get("linkGroupId"), "pair must stay linked"
        # video untouched
        video_clips = sorted(
            client.tracks[0]["clips"], key=lambda c: c["startFrame"])
        video_events = plan["tracks"][0]["events"]
        for event, clip in zip(video_events, video_clips):
            assert clip["startFrame"] == round(
                event["timeline_start_seconds"] * fps)
        return summary

    def test_j_cut_places(self) -> None:
        from video_app.plan_ops import apply_op

        plan, _ = apply_op(self._mirrored_plan(), {
            "op": "jl_cut", "event_id": "v02", "lead_seconds": 0.4,
        }, MINI_INVENTORY)
        self._place_and_check(plan)

    def test_l_cut_places(self) -> None:
        from video_app.plan_ops import apply_op

        plan, _ = apply_op(self._mirrored_plan(), {
            "op": "jl_cut", "event_id": "v03", "lead_seconds": -0.4,
        }, MINI_INVENTORY)
        self._place_and_check(plan)

    def test_mixed_j_and_l_places(self) -> None:
        # J-cut at boundary 1 (audio of v02 leads 0.5s) AND L-cut at
        # boundary 2 (audio of v02 trails 0.3s into v03) — built by hand
        # since the op enforces one J/L per revision.
        plan = self._mirrored_plan()
        audio = next(t for t in plan["tracks"] if t["kind"] == "audio")
        a01, a02, a03 = audio["events"]
        a01["duration_seconds"] = 1.5
        a01["source_end_seconds"] = 1.5
        a02["timeline_start_seconds"] = 1.5
        a02["source_start_seconds"] = 0.5
        a02["duration_seconds"] = 2.8
        a02["source_end_seconds"] = 3.3
        a03["timeline_start_seconds"] = 4.3
        a03["source_start_seconds"] = 3.3
        a03["duration_seconds"] = 1.7
        a03["source_end_seconds"] = 5.0
        self._place_and_check(plan)

    def test_unrepresentable_audio_layout_refused(self) -> None:
        import pytest
        from video_app.opentake_bridge import BridgeError, place_plan

        plan = self._mirrored_plan()
        audio = next(t for t in plan["tracks"] if t["kind"] == "audio")
        audio["events"][1]["asset_id"] = "red"  # asset differs from video slot
        audio["events"][1]["timeline_start_seconds"] += 0.1  # break mirror
        with pytest.raises(BridgeError, match="different asset"):
            place_plan(plan, MINI_INVENTORY, "p", client=self._client())


class TestJLRoundTrip:
    """The last round-trip gap: sync FROM a J/L-placed timeline."""

    def _placed(self):
        from video_app.opentake_bridge import place_plan
        from video_app.plan_ops import apply_op

        plan, _ = apply_op(TestJLPlacement()._mirrored_plan(), {
            "op": "jl_cut", "event_id": "v02", "lead_seconds": 0.4,
        }, MINI_INVENTORY)
        client = FakeOpenTake({
            "red": {"id": "ref-red", "kind": "video"},
            "blue": {"id": "ref-blue", "kind": "video"},
        })
        _, bridge = place_plan(plan, MINI_INVENTORY, "p", client=client)
        assert "audio_events" in bridge
        return plan, bridge, client

    def test_untouched_jl_timeline_round_trips(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, client = self._placed()
        candidate, diff = timeline_to_candidate_plan(
            plan, bridge, client.get_timeline()
        )
        original_audio = next(
            t for t in plan["tracks"] if t["kind"] == "audio"
        )["events"]
        candidate_audio = next(
            t for t in candidate["tracks"] if t["kind"] == "audio"
        )["events"]
        for before, after in zip(original_audio, candidate_audio):
            for field in ("source_start_seconds", "source_end_seconds",
                          "timeline_start_seconds", "duration_seconds"):
                assert after[field] == before[field], (field, before, after)
        assert not [d for d in diff if d["kind"] != "unchanged"]

    def test_gui_edit_on_jl_pair_syncs_both_sides(self) -> None:
        from video_app.opentake_sync import timeline_to_candidate_plan

        plan, bridge, client = self._placed()
        # Simulate a GUI trim of pair 2's head by 6 frames: propagation
        # applies the same delta to both clips, preserving the J offset.
        video_clip = sorted(
            client.tracks[0]["clips"], key=lambda c: c["startFrame"]
        )[1]
        partner = next(
            c for t in client.tracks if t["type"] == "audio"
            for c in t["clips"]
            if c.get("linkGroupId") == video_clip["linkGroupId"]
        )
        for clip in (video_clip, partner):
            clip["startFrame"] += 6
            clip["durationFrames"] -= 6
            clip["trimStartFrame"] += 6
        candidate, diff = timeline_to_candidate_plan(
            plan, bridge, client.get_timeline()
        )
        video_events = next(
            t for t in candidate["tracks"] if t["kind"] == "video"
        )["events"]
        audio_events = next(
            t for t in candidate["tracks"] if t["kind"] == "audio"
        )["events"]
        assert video_events[1]["source_start_seconds"] == 1.2
        assert audio_events[1]["source_start_seconds"] == 0.8  # 0.6 + 0.2
        # the J offset survives: audio still leads by 0.4s
        assert round(
            video_events[1]["timeline_start_seconds"]
            - audio_events[1]["timeline_start_seconds"], 6
        ) == 0.4
        assert any(d["kind"] == "trimmed" for d in diff)

    def test_partner_outside_audio_envelope_fails_closed(self) -> None:
        import pytest
        from video_app.opentake_sync import SyncError, timeline_to_candidate_plan

        plan, bridge, client = self._placed()
        partner = next(
            c for t in client.tracks if t["type"] == "audio"
            for c in t["clips"] if c.get("linkGroupId")
        )
        # give the partner a foreign identity AND out-of-envelope source
        partner["clipId"] = "foreign"
        partner["trimStartFrame"] = 5000
        with pytest.raises(SyncError, match="outside the placed audio envelope"):
            timeline_to_candidate_plan(plan, bridge, client.get_timeline())


class TestEnsureProject:
    def test_opens_existing_or_creates_missing(self) -> None:
        from video_app.opentake_bridge import ensure_project

        fake = FakeOpenTake({})
        assert ensure_project(fake, "mi-vlog") == "opened"
        assert fake.opened_project == "mi-vlog"

        fake2 = FakeOpenTake({})
        fake2.missing_bundle = True
        assert ensure_project(fake2, "mi-vlog") == "created"
        assert fake2.created_project == "mi-vlog"

    def test_existence_never_relies_on_redacted_errors(self) -> None:
        # external MCP redacts tool errors — ensure_project must decide
        # open-vs-create from list_projects, so open_project errors that
        # ARE raised surface as real failures, never trigger create
        from video_app.opentake_bridge import BridgeError, ensure_project
        from video_app.opentake_mcp import OpenTakeMcpError

        fake = FakeOpenTake({})

        def failing_tool(name, arguments=None, _orig=fake.tool):
            if name == "open_project":
                raise OpenTakeMcpError("MCP_TOOL_ERROR_REDACTED")
            return _orig(name, arguments)

        fake.tool = failing_tool
        import pytest as _pytest
        with _pytest.raises(BridgeError, match="abrir el proyecto"):
            ensure_project(fake, "mi-vlog")


class TestAutoImport:
    def test_missing_needed_media_is_imported_per_file(self) -> None:
        from video_app.opentake_bridge import map_media

        fake = FakeOpenTake({})  # empty library
        inventory = {"assets": [
            {"asset_id": "clip_0", "filename": "IMG_2343.MOV",
             "source_path": "IMG_2343.MOV"},
            {"asset_id": "clip_1", "filename": "IMG_2345.MOV",
             "source_path": "IMG_2345.MOV"},
        ]}
        refs = map_media(fake, inventory, ["clip_0", "clip_1"],
                         media_root="/footage/x")
        assert set(refs) == {"clip_0", "clip_1"}
        assert sorted(fake.imported) == ["IMG_2343", "IMG_2345"]

    def test_without_media_root_the_manual_message_remains(self) -> None:
        import pytest as _pytest
        from video_app.opentake_bridge import BridgeError, map_media

        fake = FakeOpenTake({})
        inventory = {"assets": [
            {"asset_id": "clip_0", "filename": "IMG_2343.MOV",
             "source_path": "IMG_2343.MOV"},
        ]}
        with _pytest.raises(BridgeError, match="Import these files"):
            map_media(fake, inventory, ["clip_0"])
