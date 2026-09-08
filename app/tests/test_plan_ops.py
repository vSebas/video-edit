"""P4: atomic natural-language edits — deterministic op appliers and the
instruction endpoints (LLM stubbed; the model only ever picks an op)."""

import json
from pathlib import Path

import pytest

from video_app.plan_ops import PlanOpError, apply_op, instruction_to_op

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "opentake_sync"


def _event(event_id, asset_id, src_start, timeline_start, duration,
           intent="scene", volume_db=None):
    return {
        "event_id": event_id, "asset_id": asset_id,
        "source_start_seconds": src_start,
        "source_end_seconds": round(src_start + duration, 6),
        "timeline_start_seconds": timeline_start,
        "duration_seconds": duration, "playback_rate": 1.0,
        "intent": intent, "observed_content": None, "confidence": 0.9,
        "reframe": None, "transition_out": None, "text": None,
        "volume_db": volume_db,
    }


def _plan(with_broll=False):
    video = [
        _event("v01", "clip_a", 0.0, 0.0, 3.0),
        _event("v02", "clip_b", 1.0, 3.0, 4.0),
        _event("v03", "clip_a", 5.0, 7.0, 3.0),
    ]
    audio = [json.loads(json.dumps(e)) for e in video]
    for e in audio:
        e["event_id"] = e["event_id"].replace("v", "a", 1)
    tracks = [
        {"track_id": "v1", "kind": "video", "events": video},
        {"track_id": "a1", "kind": "audio", "events": audio},
        {"track_id": "t1", "kind": "title", "events": [
            {"event_id": "t01", "asset_id": None,
             "source_start_seconds": None, "source_end_seconds": None,
             "timeline_start_seconds": 8.0, "duration_seconds": 2.0,
             "playback_rate": 1.0, "intent": "title",
             "observed_content": None, "confidence": 1.0, "reframe": None,
             "transition_out": None, "text": "Hola", "volume_db": None},
        ]},
    ]
    if with_broll:
        tracks.append({"track_id": "v2", "kind": "video", "role": "broll",
                       "events": [_event("bro-01", "clip_b", 0.0, 8.0, 1.5,
                                         intent="b-roll")]})
    return {
        "schema_version": "edit-plan.v1",
        "generated_at": "2026-09-01T00:00:00Z",
        "benchmark_id": "t", "concept_id": "t", "revision": 3,
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 10.0, "background_color": "black"},
        "tracks": tracks,
    }


INVENTORY = {"assets": [
    {"asset_id": "clip_a", "media_type": "video", "duration_seconds": 10.0,
     "filename": "clip_a.mp4"},
    {"asset_id": "clip_b", "media_type": "video", "duration_seconds": 6.0,
     "filename": "clip_b.mp4"},
]}


class TestApplyOp:
    def test_delete_ripples_everything_after(self) -> None:
        plan = _plan(with_broll=True)
        candidate, summary = apply_op(
            plan, {"op": "delete_event", "event_id": "v02"}, INVENTORY
        )
        video = candidate["tracks"][0]["events"]
        assert [e["event_id"] for e in video] == ["v01", "v03"]
        assert video[1]["timeline_start_seconds"] == 3.0
        assert candidate["project"]["duration_seconds"] == 6.0
        title = candidate["tracks"][2]["events"][0]
        assert title["timeline_start_seconds"] == 4.0
        broll = candidate["tracks"][3]["events"][0]
        assert broll["timeline_start_seconds"] == 4.0
        assert candidate["revision"] == 4
        assert plan["revision"] == 3  # original untouched
        assert "v02" in summary

    def test_delete_that_orphans_broll_is_refused(self) -> None:
        plan = _plan(with_broll=True)
        # dropping the LAST primary scene leaves the overlay hanging
        plan["tracks"][3]["events"][0]["timeline_start_seconds"] = 8.0
        with pytest.raises(PlanOpError, match="B-roll"):
            apply_op(plan, {"op": "delete_event", "event_id": "v03"}, INVENTORY)

    def test_trim_shorten_start_moves_source_window(self) -> None:
        candidate, _ = apply_op(_plan(), {
            "op": "trim_event", "event_id": "v02", "edge": "start",
            "direction": "shorten", "seconds": 1.0,
        }, INVENTORY)
        video = candidate["tracks"][0]["events"]
        assert video[1]["source_start_seconds"] == 2.0
        assert video[1]["duration_seconds"] == 3.0
        assert video[1]["timeline_start_seconds"] == 3.0
        assert video[2]["timeline_start_seconds"] == 6.0
        assert candidate["project"]["duration_seconds"] == 9.0
        audio = candidate["tracks"][1]["events"]
        assert audio[1]["source_start_seconds"] == 2.0

    def test_trim_extend_end_needs_source_material(self) -> None:
        with pytest.raises(PlanOpError, match="no source material after"):
            apply_op(_plan(), {
                "op": "trim_event", "event_id": "v02", "edge": "end",
                "direction": "extend", "seconds": 1.5,
            }, INVENTORY)  # clip_b is 6.0s; v02 already ends at source 5.0

    def test_trim_extend_start_at_source_zero_is_refused(self) -> None:
        with pytest.raises(PlanOpError, match="no source material before"):
            apply_op(_plan(), {
                "op": "trim_event", "event_id": "v01", "edge": "start",
                "direction": "extend", "seconds": 0.5,
            }, INVENTORY)

    def test_set_volume_accepts_video_event_name(self) -> None:
        candidate, summary = apply_op(_plan(), {
            "op": "set_volume", "event_id": "v02", "volume_db": -12,
        }, INVENTORY)
        assert candidate["tracks"][1]["events"][1]["volume_db"] == -12
        assert "a02" in summary

    def test_jl_cut_makes_audio_lead_picture(self) -> None:
        candidate, summary = apply_op(_plan(), {
            "op": "jl_cut", "event_id": "v02", "lead_seconds": 0.5,
        }, INVENTORY)
        video = candidate["tracks"][0]["events"]
        audio = candidate["tracks"][1]["events"]
        assert video[1]["timeline_start_seconds"] == 3.0  # picture unmoved
        assert audio[0]["duration_seconds"] == 2.5
        assert audio[1]["timeline_start_seconds"] == 2.5
        assert audio[1]["source_start_seconds"] == 0.5
        assert audio[1]["duration_seconds"] == 4.5
        assert "J-cut" in summary

    def test_structural_edit_on_jl_plan_is_refused(self) -> None:
        jl, _ = apply_op(_plan(), {
            "op": "jl_cut", "event_id": "v02", "lead_seconds": 0.5,
        }, INVENTORY)
        with pytest.raises(PlanOpError, match="J/L"):
            apply_op(jl, {"op": "delete_event", "event_id": "v01"}, INVENTORY)

    def test_set_title(self) -> None:
        candidate, _ = apply_op(_plan(), {
            "op": "set_title", "event_id": "t01", "text": "Nuevo título",
        }, INVENTORY)
        title = candidate["tracks"][2]["events"][0]
        assert title["text"] == "Nuevo título"
        # Instruction-set title text is MODEL-mediated, not typed verbatim, so it
        # must NOT be user_authored — that flag would exempt it from the title
        # claim gate (Codex review 2026-09-06).
        assert not title.get("user_authored")

    def test_set_title_style_validated(self) -> None:
        candidate, summary = apply_op(_plan(), {
            "op": "set_title", "event_id": "t01", "text": "Hola",
            "font": "handwritten", "size": 72, "position": "lower",
        }, INVENTORY)
        event = candidate["tracks"][2]["events"][0]
        assert event["text_style"] == {
            "font": "handwritten", "size": 72, "position": "lower"}
        assert "fuente handwritten" in summary and "abajo" in summary
        with pytest.raises(PlanOpError, match="font must be"):
            apply_op(_plan(), {"op": "set_title", "event_id": "t01",
                               "text": "x", "font": "comic-sans"}, INVENTORY)
        with pytest.raises(PlanOpError, match="size must be"):
            apply_op(_plan(), {"op": "set_title", "event_id": "t01",
                               "text": "x", "size": 500}, INVENTORY)
        # A JSON 1e309 parses to inf; int(inf) would raise OverflowError → 500.
        # It must degrade to a clean PlanOpError instead (Codex review).
        with pytest.raises(PlanOpError, match="finite"):
            apply_op(_plan(), {"op": "set_title", "event_id": "t01",
                               "text": "x", "size": float("inf")}, INVENTORY)

    def test_unknown_op_and_missing_fields_fail_closed(self) -> None:
        with pytest.raises(PlanOpError, match="Unknown operation"):
            apply_op(_plan(), {"op": "explode"}, INVENTORY)
        with pytest.raises(PlanOpError, match="missing"):
            apply_op(_plan(), {"op": "trim_event", "event_id": "v01"}, INVENTORY)
        with pytest.raises(PlanOpError, match="No event"):
            apply_op(_plan(), {"op": "delete_event", "event_id": "v99"}, INVENTORY)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return {"content": json.dumps(self.payload)}


class TestInstructionToOp:
    def test_prompt_carries_timeline_and_returns_op(self) -> None:
        client = FakeClient({"op": "delete_event", "event_id": "v02"})
        op = instruction_to_op(client, _plan(), "quita la segunda escena")
        assert op == {"op": "delete_event", "event_id": "v02"}
        system = client.messages[0]["content"]
        assert "v02 [3.0-7.0s]" in system and "t01" in system

    def test_non_object_reply_fails_closed(self) -> None:
        client = FakeClient(["not", "an", "op"])
        with pytest.raises(PlanOpError, match="no operation"):
            instruction_to_op(client, _plan(), "haz algo")

    def test_server_only_op_is_refused_not_applied(self) -> None:
        # A prompted/hallucinating model must NOT be able to reach the
        # destructive, server-computed cleanup_voiceover op through the
        # instruction path — it is rejected, never returned for apply.
        client = FakeClient({"op": "cleanup_voiceover", "event_id": "vo-01",
                             "remove_ranges": [[0, 20]]})
        op = instruction_to_op(client, _plan(), "borra toda la voz")
        assert op["op"] == "reject"


class TestPlanCommandEndpoints:
    def _scaffold(self, tmp_path):
        fx_plan = _plan()
        root = tmp_path / "runtime" / "cmd-test"
        (root / "plan").mkdir(parents=True)
        (root / "plan" / "edit-plan.json").write_text(json.dumps(fx_plan))
        (root / "project.json").write_text(json.dumps({
            "schema_version": "video-app-project.v1", "project_id": "cmd-test",
            "name": "t", "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z", "source_directory": "footage",
            "prompt": "", "status": "plan_ready", "footage_summary": "",
            "analysis": {}, "inventory": INVENTORY, "concepts": [],
            "selected_concept_id": None, "plan": fx_plan, "outputs": {},
        }))
        return root

    def test_propose_then_apply_installs_revision(self, tmp_path, monkeypatch) -> None:
        from fastapi.testclient import TestClient
        from video_app import projects as projects_module
        from video_app.config import Settings
        from video_app.main import create_app

        self._scaffold(tmp_path)
        monkeypatch.setattr(projects_module, "resolve_provider", lambda *a: None)
        monkeypatch.setattr(
            projects_module, "ChatClient",
            lambda *a, **k: FakeClient({"op": "set_volume",
                                        "event_id": "v02", "volume_db": -12}),
        )
        settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        with TestClient(create_app(settings)) as client:
            proposed = client.post(
                "/api/projects/cmd-test/plan/command",
                json={"instruction": "baja el volumen de la segunda escena"},
            )
            assert proposed.status_code == 200, proposed.text
            body = proposed.json()
            assert body["status"] == "proposed"
            assert body["revision_preview"] == 4
            applied = client.post(
                "/api/projects/cmd-test/plan/command/apply",
                params={"proposal_id": body["proposal_id"]},
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["revision"] == 4
            plan = json.loads(
                (tmp_path / "runtime" / "cmd-test" / "plan" / "edit-plan.json")
                .read_text()
            )
            assert plan["revision"] == 4
            assert plan["tracks"][1]["events"][1]["volume_db"] == -12
            # single-use: a second apply has nothing to install
            again = client.post("/api/projects/cmd-test/plan/command/apply")
            assert again.status_code == 400

    def test_reject_passes_reason_through(self, tmp_path, monkeypatch) -> None:
        from fastapi.testclient import TestClient
        from video_app import projects as projects_module
        from video_app.config import Settings
        from video_app.main import create_app

        self._scaffold(tmp_path)
        monkeypatch.setattr(projects_module, "resolve_provider", lambda *a: None)
        monkeypatch.setattr(
            projects_module, "ChatClient",
            lambda *a, **k: FakeClient({"op": "reject",
                                        "reason": "pide dos cambios a la vez"}),
        )
        settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        with TestClient(create_app(settings)) as client:
            proposed = client.post(
                "/api/projects/cmd-test/plan/command",
                json={"instruction": "quita v01 y v02"},
            )
            assert proposed.status_code == 200
            assert proposed.json() == {
                "status": "rejected", "reason": "pide dos cambios a la vez",
            }


class TestPlannerCutaways:
    """P8 step 1: concepts may propose B-roll; the compiler lays it on v2."""

    def _document(self):
        return {"concepts": [{
            "concept_id": "c1", "title": "T", "topic": "x",
            "target_duration_seconds": 10,
            "structure": [
                {"beat_id": "talk", "purpose": "hablar del dia",
                 "target_duration_seconds": 6.0,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 0.0,
                               "end_seconds": 6.0,
                               "observed_content": "habla", "confidence": 0.9}],
                 "cutaways": [{"asset_id": "clip_b", "start_seconds": 1.0,
                               "end_seconds": 3.5,
                               "observed_content": "comida", "confidence": 0.8}]},
                {"beat_id": "b2", "purpose": "p",
                 "target_duration_seconds": 2.0,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 6.0,
                               "end_seconds": 8.0,
                               "observed_content": "x", "confidence": 0.9}]},
                {"beat_id": "b3", "purpose": "p",
                 "target_duration_seconds": 2.0,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 8.0,
                               "end_seconds": 10.0,
                               "observed_content": "x", "confidence": 0.9}]},
            ],
        }]}

    def _project(self):
        return {"project_id": "p", "inventory": {"assets": [
            {"asset_id": "clip_a", "media_type": "video",
             "duration_seconds": 10.0},
            {"asset_id": "clip_b", "media_type": "video",
             "duration_seconds": 6.0},
        ]}}

    def test_cutaway_compiles_onto_v2_inside_its_beat(self) -> None:
        from video_app.planning import compile_edit_plan

        plan = compile_edit_plan(self._project(), self._document(), "c1")
        videos = [t for t in plan["tracks"] if t["kind"] == "video"]
        assert len(videos) == 2 and videos[1]["role"] == "broll"
        (shot,) = videos[1]["events"]
        assert shot["asset_id"] == "clip_b"
        assert shot["intent"].startswith("b-roll")
        # inside the talk beat's window [0, 6), with edge margins
        assert 0.4 <= shot["timeline_start_seconds"]
        end = shot["timeline_start_seconds"] + shot["duration_seconds"]
        assert end <= 6.0
        # audio untouched: still exactly the primary events
        audio = next(t for t in plan["tracks"] if t["kind"] == "audio")
        assert len(audio["events"]) == 3

    def test_unsupported_cutaway_drops_but_story_survives(self) -> None:
        from video_app.planning import compile_edit_plan

        approved = {"clip_a": [(0.0, 10.0)]}  # nothing approved on clip_b
        plan = compile_edit_plan(
            self._project(), self._document(), "c1", approved_ranges=approved
        )
        videos = [t for t in plan["tracks"] if t["kind"] == "video"]
        assert len(videos) == 1  # no v2 track at all

    def test_sanitizer_keeps_cutaways_and_drops_self_referencing(self) -> None:
        from video_app.planning import _sanitize_concepts

        document = self._document()
        beat = document["concepts"][0]["structure"][0]
        beat["cutaways"].append({
            "asset_id": "clip_a", "start_seconds": 0.0, "end_seconds": 2.0,
            "observed_content": "same footage", "confidence": 0.9,
        })
        _sanitize_concepts(document, self._project())
        cutaways = document["concepts"][0]["structure"][0]["cutaways"]
        assert [c["asset_id"] for c in cutaways] == ["clip_b"]


class TestRotationDetection:
    def test_detected_rotation_reaches_the_compiled_plan(self) -> None:
        from video_app.planning import compile_edit_plan

        project = {"project_id": "p", "inventory": {"assets": [
            {"asset_id": "clip_a", "media_type": "video",
             "duration_seconds": 10.0, "suggested_rotation_degrees": 90},
        ]}}
        document = {"concepts": [{
            "concept_id": "c1", "title": "T", "topic": "x",
            "target_duration_seconds": 6,
            "structure": [
                {"beat_id": f"b{i}", "purpose": "p",
                 "target_duration_seconds": 2.0,
                 "evidence": [{"asset_id": "clip_a",
                               "start_seconds": i * 2.0,
                               "end_seconds": i * 2.0 + 2.0,
                               "observed_content": "x", "confidence": 0.9}]}
                for i in range(3)
            ],
        }]}
        plan = compile_edit_plan(project, document, "c1")
        for event in plan["tracks"][0]["events"]:
            assert event["reframe"]["rotation_degrees"] == 90
            assert event["reframe"]["manual_review"] is True

    def test_orientation_parser_rejects_bad_degrees(self) -> None:
        import json as _json

        from video_app.visual import detect_orientation

        class Client:
            def chat(self, messages, **kwargs):
                return {"content": _json.dumps(
                    {"rotation_degrees_clockwise_needed": 45,
                     "confidence": 0.99})}

        import video_app.visual as visual_module
        original = visual_module.extract_frame
        visual_module.extract_frame = lambda *a: b"jpeg"
        try:
            degrees, confidence = detect_orientation(
                Client(), __import__("pathlib").Path("x.mp4"), 4.0
            )
        finally:
            visual_module.extract_frame = original
        assert (degrees, confidence) == (0, 0.0)


class TestVoiceoverOps:
    def _inventory(self):
        return {"assets": [
            {"asset_id": "clip_a", "media_type": "video",
             "duration_seconds": 10.0},
            {"asset_id": "clip_b", "media_type": "video",
             "duration_seconds": 6.0},
            {"asset_id": "memo", "media_type": "audio",
             "duration_seconds": 3.0, "filename": "memo.m4a"},
        ]}

    def test_add_voiceover_creates_a2_track(self) -> None:
        candidate, summary = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0,
        }, self._inventory())
        audios = [t for t in candidate["tracks"] if t["kind"] == "audio"]
        assert len(audios) == 2 and audios[1]["role"] == "voiceover"
        (event,) = audios[1]["events"]
        assert event["event_id"] == "vo-01"
        assert event["duration_seconds"] == 3.0
        assert "voz en off" in summary.lower()

    def test_add_voiceover_max_duration_caps_the_window(self) -> None:
        # Without a cap the whole recording (bounded by the cut) is placed;
        # max_duration_seconds trims it to the drafted window (Codex review).
        inv = self._inventory()
        full, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0}, inv)
        vo_full = next(t for t in full["tracks"]
                       if t.get("role") == "voiceover")["events"][0]
        capped, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0, "max_duration_seconds": 1.0}, inv)
        vo_cap = next(t for t in capped["tracks"]
                      if t.get("role") == "voiceover")["events"][0]
        assert vo_cap["duration_seconds"] <= 1.05
        assert vo_cap["duration_seconds"] < vo_full["duration_seconds"]

    def test_cleanup_voiceover_splits_into_compacted_segments(self) -> None:
        # place a 3s voiceover at timeline 2, then remove source 1.0-1.5
        placed, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0}, self._inventory())
        cleaned, summary = apply_op(placed, {
            "op": "cleanup_voiceover", "event_id": "vo-01",
            "remove_ranges": [[1.0, 1.5]]}, self._inventory())
        vo = next(t for t in cleaned["tracks"] if t.get("role") == "voiceover")
        # two kept segments, compacted back-to-back from the original start
        assert len(vo["events"]) == 2
        a, b = vo["events"]
        assert a["source_start_seconds"] == 0.0 and a["source_end_seconds"] == 1.0
        assert a["timeline_start_seconds"] == 2.0 and a["duration_seconds"] == 1.0
        assert b["source_start_seconds"] == 1.5 and b["source_end_seconds"] == 3.0
        assert b["timeline_start_seconds"] == 3.0   # compacted (no 0.5s gap)
        # the FIRST segment keeps the parent id (stable anchor); the second does not
        assert a["event_id"] == "vo-01" and b["event_id"] != "vo-01"
        assert "limpiada" in summary
        # empty removal set is refused
        with pytest.raises(PlanOpError):
            apply_op(placed, {"op": "cleanup_voiceover", "event_id": "vo-01",
                              "remove_ranges": []}, self._inventory())

    def _placed_vo(self):
        """A 3s voiceover placed at timeline 2 (frame-aligned at 30fps)."""
        placed, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0}, self._inventory())
        return placed

    def test_cleanup_voiceover_quantizes_removals_inward(self) -> None:
        # A non-frame-aligned removal must never bite into UN-selected audio:
        # start rounds later, end rounds earlier, so we keep >= the request.
        cleaned, _ = apply_op(self._placed_vo(), {
            "op": "cleanup_voiceover", "event_id": "vo-01",
            "remove_ranges": [[1.001, 1.499]]}, self._inventory())
        a, b = next(t for t in cleaned["tracks"]
                    if t.get("role") == "voiceover")["events"]
        # removed frames = [31, 44) -> kept ...1.033333 and 1.466667...
        assert a["source_end_seconds"] == round(31 / 30, 6)   # >= requested 1.001
        assert b["source_start_seconds"] == round(44 / 30, 6)  # <= requested 1.499
        # every boundary lands on a frame (6-decimal storage keeps it well
        # inside half a frame, so OpenTake's seconds->nearest-frame agrees)
        for e in (a, b):
            for k in ("source_start_seconds", "source_end_seconds",
                      "timeline_start_seconds", "duration_seconds"):
                assert abs(e[k] * 30 - round(e[k] * 30)) < 1e-3

    def test_cleanup_voiceover_keeps_one_frame_sliver(self) -> None:
        # A single kept frame between two removals survives (no sliver-discard
        # that would silently delete a short real word).
        cleaned, _ = apply_op(self._placed_vo(), {
            "op": "cleanup_voiceover", "event_id": "vo-01",
            "remove_ranges": [[0.0, 1.0], [1.0333, 3.0]]}, self._inventory())
        evs = next(t for t in cleaned["tracks"]
                   if t.get("role") == "voiceover")["events"]
        assert len(evs) == 1
        assert evs[0]["duration_seconds"] == round(1 / 30, 6)

    def test_cleanup_voiceover_refuses_subframe_and_full_removals(self) -> None:
        # sub-frame removal collapses to nothing -> refused (not a silent no-op)
        with pytest.raises(PlanOpError, match="válidos"):
            apply_op(self._placed_vo(), {
                "op": "cleanup_voiceover", "event_id": "vo-01",
                "remove_ranges": [[1.00, 1.01]]}, self._inventory())
        # removing the whole event -> refused (never an empty track)
        with pytest.raises(PlanOpError, match="vacía"):
            apply_op(self._placed_vo(), {
                "op": "cleanup_voiceover", "event_id": "vo-01",
                "remove_ranges": [[0.0, 3.0]]}, self._inventory())

    def test_cleanup_voiceover_recompacts_the_whole_group(self) -> None:
        # A split voiceover (two vo_group segments); removing filler from the
        # SECOND segment recompacts the WHOLE group with no gap.
        plan = _plan()
        vo = {"track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
            {"event_id": "vo-01", "asset_id": "memo", "vo_group": "g",
             "source_start_seconds": 0.0, "source_end_seconds": 2.0,
             "timeline_start_seconds": 2.0, "duration_seconds": 2.0,
             "playback_rate": 1.0, "intent": "voiceover", "observed_content": None,
             "confidence": 0.9, "reframe": None, "transition_out": None,
             "text": None, "volume_db": None},
            {"event_id": "vo-02", "asset_id": "memo", "vo_group": "g",
             "source_start_seconds": 4.0, "source_end_seconds": 8.0,
             "timeline_start_seconds": 4.0, "duration_seconds": 4.0,
             "playback_rate": 1.0, "intent": "voiceover", "observed_content": None,
             "confidence": 0.9, "reframe": None, "transition_out": None,
             "text": None, "volume_db": None}]}
        plan["tracks"].append(vo)
        inv = {"assets": [{"asset_id": "memo", "media_type": "audio",
                           "duration_seconds": 8.0}]}
        cleaned, _ = apply_op(plan, {
            "op": "cleanup_voiceover", "event_id": "vo-02",
            "remove_ranges": [[5.0, 6.0]]}, inv)
        segs = next(t for t in cleaned["tracks"]
                    if t.get("role") == "voiceover")["events"]
        # kept source [0-2],[4-5],[6-8] compacted from tl 2 → 2-4, 4-5, 5-7
        assert [(e["source_start_seconds"], e["source_end_seconds"]) for e in segs] \
            == [(0.0, 2.0), (4.0, 5.0), (6.0, 8.0)]
        starts = [e["timeline_start_seconds"] for e in segs]
        durs = [e["duration_seconds"] for e in segs]
        assert starts == [2.0, 4.0, 5.0]          # contiguous, no gap
        assert durs == [2.0, 1.0, 2.0]
        assert all(e["vo_group"] == "g" for e in segs)   # group id preserved
        assert segs[0]["event_id"] == "vo-01"            # anchor kept

    def test_cleanup_voiceover_handles_legacy_group_without_vo_group(self) -> None:
        # A split created BEFORE vo_group existed (no field): the op must fall
        # back to the same geometric grouping the service uses, so a removal in
        # the SECOND segment is applied — never silently clamped away.
        plan = _plan()
        base = {"asset_id": "memo", "playback_rate": 1.0, "intent": "voiceover",
                "observed_content": None, "confidence": 0.9, "reframe": None,
                "transition_out": None, "text": None, "volume_db": None}
        plan["tracks"].append({
            "track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
                {**base, "event_id": "vo-01", "source_start_seconds": 0.0,
                 "source_end_seconds": 2.0, "timeline_start_seconds": 2.0,
                 "duration_seconds": 2.0},
                {**base, "event_id": "vo-02", "source_start_seconds": 4.0,
                 "source_end_seconds": 8.0, "timeline_start_seconds": 4.0,
                 "duration_seconds": 4.0}]})
        inv = {"assets": [{"asset_id": "memo", "media_type": "audio",
                           "duration_seconds": 8.0}]}
        cleaned, _ = apply_op(plan, {
            "op": "cleanup_voiceover", "event_id": "vo-01",
            "remove_ranges": [[5.0, 6.0]]}, inv)   # range in the SECOND segment
        segs = next(t for t in cleaned["tracks"]
                    if t.get("role") == "voiceover")["events"]
        assert [(e["source_start_seconds"], e["source_end_seconds"]) for e in segs] \
            == [(0.0, 2.0), (4.0, 5.0), (6.0, 8.0)]   # removal actually applied
        assert all(e.get("vo_group") for e in segs)     # group id now stamped
        # ...and a fresh vg-NN, never the reusable anchor event id
        assert segs[0]["vo_group"].startswith("vg-")

    def test_cleanup_voiceover_refuses_non_frame_aligned_source(self) -> None:
        placed = self._placed_vo()
        vo = next(t for t in placed["tracks"] if t.get("role") == "voiceover")
        vo["events"][0]["source_end_seconds"] = 2.99   # off the frame grid
        with pytest.raises(PlanOpError, match="alineada"):
            apply_op(placed, {"op": "cleanup_voiceover", "event_id": "vo-01",
                              "remove_ranges": [[1.0, 1.5]]}, self._inventory())

    def test_back_to_back_parts_never_phantom_overlap(self) -> None:
        # 6-decimal storage makes float sums drift: 5.933333+4.666667 =
        # 10.600000000000001, so placing the next part at the gridded 10.6
        # falsely tripped "Overlaps voiceover" on EVERY device (2026-09-08).
        plan = _plan()
        vo = {"track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
            {"event_id": "vo-01", "asset_id": "memo",
             "source_start_seconds": 0.0, "source_end_seconds": 5.933333,
             "timeline_start_seconds": 0.0, "duration_seconds": 5.933333,
             "playback_rate": 1.0, "intent": "voiceover", "observed_content": None,
             "confidence": 1.0, "reframe": None, "transition_out": None,
             "text": None, "volume_db": None},
            {"event_id": "vo-02", "asset_id": "memo",
             "source_start_seconds": 0.0, "source_end_seconds": 4.666667,
             "timeline_start_seconds": 5.933333, "duration_seconds": 4.666667,
             "playback_rate": 1.0, "intent": "voiceover", "observed_content": None,
             "confidence": 1.0, "reframe": None, "transition_out": None,
             "text": None, "volume_db": None}]}
        plan["tracks"].append(vo)
        assert 5.933333 + 4.666667 > 10.6      # the float drift is real
        placed, _ = apply_op(plan, {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 10.6}, self._inventory())
        events = next(t for t in placed["tracks"]
                      if t.get("role") == "voiceover")["events"]
        assert len(events) == 3                # placed, no phantom overlap
        # a REAL overlap is still refused
        with pytest.raises(PlanOpError, match="Overlaps"):
            apply_op(plan, {"op": "add_voiceover", "asset_id": "memo",
                            "timeline_start_seconds": 8.0}, self._inventory())

    def test_voiceover_may_run_past_the_cut_and_grows_the_canvas(self) -> None:
        # The narration is the spine: a part placed at/beyond the picture end is
        # LEGAL — the canvas grows to the voiceover end and the retime/new
        # scenes fill the picture later (multi-part flow, 2026-09-08).
        plan = _plan()                       # 10s cut
        placed, summary = apply_op(plan, {
            "op": "add_voiceover", "asset_id": "memo",   # 3s recording
            "timeline_start_seconds": 9.5}, self._inventory())
        vo = next(t for t in placed["tracks"] if t.get("role") == "voiceover")
        (event,) = vo["events"]
        assert event["duration_seconds"] == 3.0          # NOT clipped to the cut
        assert placed["project"]["duration_seconds"] == 12.5   # canvas follows
        assert "creció" in summary
        with pytest.raises(PlanOpError, match="not an audio asset"):
            apply_op(_plan(), {
                "op": "add_voiceover", "asset_id": "clip_b",
                "timeline_start_seconds": 0.0,
            }, self._inventory())

    def test_overlapping_voiceovers_refused_and_removal_works(self) -> None:
        with_vo, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 2.0,
        }, self._inventory())
        with pytest.raises(PlanOpError, match="Overlaps"):
            apply_op(with_vo, {
                "op": "add_voiceover", "asset_id": "memo",
                "timeline_start_seconds": 3.0,
            }, self._inventory())
        removed, _ = apply_op(with_vo, {
            "op": "remove_voiceover", "event_id": "vo-01",
        }, self._inventory())
        # an empty A2 would break the later sync round-trip — it must go
        audios = [t for t in removed["tracks"] if t["kind"] == "audio"]
        assert len(audios) == 1

    def test_delete_pushing_voiceover_past_end_is_refused(self) -> None:
        with_vo, _ = apply_op(_plan(), {
            "op": "add_voiceover", "asset_id": "memo",
            "timeline_start_seconds": 6.5,
        }, self._inventory())
        with pytest.raises(PlanOpError, match="voiceover"):
            apply_op(with_vo, {"op": "delete_event", "event_id": "v03"},
                     self._inventory())

    def test_instruction_table_lists_audio_assets(self) -> None:
        client = FakeClient({"op": "add_voiceover", "asset_id": "memo",
                             "timeline_start_seconds": 0.0})
        instruction_to_op(client, _plan(), "pon la nota de voz",
                          self._inventory())
        system = client.messages[0]["content"]
        assert "memo (3.0s)" in system and "memo.m4a" in system


class TestConceptTrust:
    """In-range hallucinations get flagged by caption cross-check."""

    def test_claim_matching_caption_is_clean(self) -> None:
        from video_app.planning import _claim_unsupported

        assert not _claim_unsupported(
            "camina por el pasillo hablando del examen",
            "una persona camina por un pasillo mientras habla; menciona un examen",
        )

    def test_fabricated_claim_is_flagged(self) -> None:
        from video_app.planning import _claim_unsupported

        assert _claim_unsupported(
            "ella llora emocionada recibiendo el premio",
            "una persona camina por un pasillo con una mochila",
        )

    def test_short_abstract_claims_get_benefit_of_doubt(self) -> None:
        from video_app.planning import _claim_unsupported

        assert not _claim_unsupported("buen ambiente", "cualquier cosa")

    def test_sanitizer_marks_needs_review(self) -> None:
        from video_app.planning import _sanitize_concepts

        project = {"project_id": "p", "inventory": {"assets": [
            {"asset_id": "clip_a", "media_type": "video",
             "duration_seconds": 10.0},
        ]}}
        evidence = [{"asset_id": "clip_a", "start_seconds": 0.0,
                     "end_seconds": 10.0,
                     "caption": "una persona camina por un pasillo"}]
        document = {"concepts": [{
            "concept_id": "c1", "title": "T", "topic": "x",
            "structure": [
                {"beat_id": "b1", "purpose": "p", "target_duration_seconds": 2,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 0.0,
                               "end_seconds": 2.0,
                               "observed_content": "camina por el pasillo",
                               "confidence": 0.9}]},
                {"beat_id": "b2", "purpose": "p", "target_duration_seconds": 2,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 2.0,
                               "end_seconds": 4.0,
                               "observed_content":
                                   "abraza llorando a sus amigos del equipo",
                               "confidence": 0.9}]},
                {"beat_id": "b3", "purpose": "p", "target_duration_seconds": 2,
                 "evidence": [{"asset_id": "clip_a", "start_seconds": 4.0,
                               "end_seconds": 6.0,
                               "observed_content": "sigue caminando pasillo",
                               "confidence": 0.9}]},
            ],
        }]}
        _sanitize_concepts(document, project, evidence)
        beats = document["concepts"][0]["structure"]
        assert "needs_review" not in beats[0]["evidence"][0]
        assert beats[1]["evidence"][0].get("needs_review") is True
        assert "needs_review" not in beats[2]["evidence"][0]


    def test_flagged_spans_stay_schema_valid(self) -> None:
        """The live P7 harness caught sanitizer output the concepts schema
        rejected (needs_review); pin the two in agreement."""
        import json
        from pathlib import Path

        from jsonschema import Draft202012Validator

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas"
             / "creative-concepts.schema.json").read_text()
        )
        span = {
            "asset_id": "a", "start_seconds": 0.0, "end_seconds": 2.0,
            "observed_content": "x", "confidence": 0.5,
            "needs_review": True,
        }
        validator = Draft202012Validator(
            {"$ref": "#/$defs/evidence", "$defs": schema["$defs"]}
        )
        assert not list(validator.iter_errors(span))


class TestBrollOps:
    """Conversational B-roll: the vocabulary gap the second assessment found."""

    def test_add_broll_defaults_and_track_creation(self) -> None:
        candidate, summary = apply_op(_plan(), {
            "op": "add_broll", "asset_id": "clip_b",
            "timeline_start_seconds": 3.0,
        }, INVENTORY)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        assert len(videos) == 2 and videos[1]["role"] == "broll"
        (event,) = videos[1]["events"]
        assert event["event_id"] == "bro-01"
        assert event["duration_seconds"] == 4.0  # default cap
        assert event["source_start_seconds"] == 0.0
        assert "audio original" in summary

    def test_overlapping_broll_refused(self) -> None:
        with_one, _ = apply_op(_plan(with_broll=True), {
            "op": "add_broll", "asset_id": "clip_b",
            "timeline_start_seconds": 0.5, "duration_seconds": 1.0,
        }, INVENTORY)
        with pytest.raises(PlanOpError, match="overlap"):
            apply_op(with_one, {
                "op": "add_broll", "asset_id": "clip_b",
                "timeline_start_seconds": 1.0, "duration_seconds": 1.0,
            }, INVENTORY)

    def test_add_broll_needs_video_asset_and_source_material(self) -> None:
        inventory = {"assets": INVENTORY["assets"] + [
            {"asset_id": "memo", "media_type": "audio",
             "duration_seconds": 3.0},
        ]}
        with pytest.raises(PlanOpError, match="not a video asset"):
            apply_op(_plan(), {
                "op": "add_broll", "asset_id": "memo",
                "timeline_start_seconds": 1.0,
            }, inventory)
        with pytest.raises(PlanOpError, match="source material"):
            apply_op(_plan(), {
                "op": "add_broll", "asset_id": "clip_b",
                "timeline_start_seconds": 1.0,
                "source_start_seconds": 4.0, "duration_seconds": 3.0,
            }, INVENTORY)

    def test_remove_broll_drops_empty_track(self) -> None:
        candidate, _ = apply_op(_plan(with_broll=True), {
            "op": "remove_broll", "event_id": "bro-01",
        }, INVENTORY)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        assert len(videos) == 1

    def test_replace_broll_keeps_slot(self) -> None:
        candidate, summary = apply_op(_plan(with_broll=True), {
            "op": "replace_broll", "event_id": "bro-01", "asset_id": "clip_a",
            "source_start_seconds": 2.0,
        }, INVENTORY)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        (event,) = videos[1]["events"]
        assert event["asset_id"] == "clip_a"
        assert event["source_start_seconds"] == 2.0
        assert event["timeline_start_seconds"] == 8.0  # slot unchanged
        assert event["duration_seconds"] == 1.5
        assert "mismo hueco" in summary

    def test_move_broll_bounds_and_ripple_safety(self) -> None:
        candidate, _ = apply_op(_plan(with_broll=True), {
            "op": "move_broll", "event_id": "bro-01",
            "timeline_start_seconds": 2.0,
        }, INVENTORY)
        videos = [t for t in candidate["tracks"] if t["kind"] == "video"]
        assert videos[1]["events"][0]["timeline_start_seconds"] == 2.0
        with pytest.raises(PlanOpError, match="within the video"):
            apply_op(_plan(with_broll=True), {
                "op": "move_broll", "event_id": "bro-01",
                "timeline_start_seconds": 9.5,
            }, INVENTORY)

    def test_instruction_table_lists_footage_assets(self) -> None:
        client = FakeClient({"op": "add_broll", "asset_id": "clip_b",
                             "timeline_start_seconds": 1.0})
        instruction_to_op(client, _plan(), "muestra la comida",
                          INVENTORY)
        system = client.messages[0]["content"]
        assert "Available footage assets" in system
        assert "clip_b (6.0s)" in system
        assert "add_broll" in system


class TestMusicAndCaptionOps:
    def _plan_with_captions_and_music_source(self):
        plan = _plan()
        plan["tracks"].append({
            "track_id": "cap1", "kind": "caption", "events": [{
                "event_id": "cap-001", "asset_id": None,
                "source_start_seconds": None, "source_end_seconds": None,
                "timeline_start_seconds": 0.5, "duration_seconds": 2.0,
                "playback_rate": 1.0, "intent": "caption",
                "observed_content": None, "confidence": 1.0,
                "text": "hla mundo", "volume_db": None,
            }]})
        return plan

    def test_set_and_remove_music_bed(self) -> None:
        inv = {"assets": [{"asset_id": "song", "filename": "song.mp3",
                           "media_type": "audio", "duration_seconds": 90,
                           "audio": True}]}
        candidate, summary = apply_op(
            _plan(), {"op": "set_music_bed", "asset_id": "song",
                      "gain_db": -12, "duck_db": -10}, inv)
        music = next(t for t in candidate["tracks"]
                     if t.get("role") == "music")
        assert music["events"][0]["music"]["mode"] == "bed"
        assert music["events"][0]["music"]["bed"]["duck_db"] == -10
        assert "Música de fondo" in summary
        cleared, _ = apply_op(candidate, {"op": "remove_music"}, inv)
        assert not any(t.get("role") == "music" for t in cleared["tracks"])

    def test_refit_music_bed_preserves_source_capacity(self) -> None:
        # A one-shot bed shortened then re-lengthened must REGROW from its
        # immutable source capacity, not stay stuck at the shrunk span
        # (Codex review 2026-09-06).
        from video_app.plan_ops import _refit_music_bed
        inv = {"assets": [{"asset_id": "song", "filename": "song.mp3",
                           "media_type": "audio", "duration_seconds": 90,
                           "audio": True}]}
        plan, _ = apply_op(_plan(), {"op": "set_music_bed",
                                     "asset_id": "song", "loop": False}, inv)

        def bed_span(p):
            music = next(t for t in p["tracks"] if t.get("role") == "music")
            return music["events"][0]["duration_seconds"]

        plan["project"]["duration_seconds"] = 5.0
        _refit_music_bed(plan)
        assert bed_span(plan) == 5.0
        # Grow the cut BEYOND its original length: the 90s song's capacity was
        # stamped at creation, so the bed regrows (not stuck at 5, nor capped at
        # the original short cut).
        plan["project"]["duration_seconds"] = 60.0
        _refit_music_bed(plan)
        assert bed_span(plan) == 60.0

    def test_music_source_must_be_audio_or_video(self) -> None:
        inv = {"assets": [{"asset_id": "x", "filename": "x.txt",
                           "media_type": "document", "duration_seconds": 1}]}
        with pytest.raises(PlanOpError, match="not a usable music source"):
            apply_op(_plan(), {"op": "set_music_bed", "asset_id": "x"}, inv)

    def test_edit_and_remove_caption(self) -> None:
        plan = self._plan_with_captions_and_music_source()
        candidate, summary = apply_op(
            plan, {"op": "edit_caption", "event_id": "cap-001",
                   "text": "hola mundo"}, INVENTORY)
        cap = next(t for t in candidate["tracks"] if t["kind"] == "caption")
        assert cap["events"][0]["text"] == "hola mundo"
        assert "«hola mundo»" in summary
        removed, _ = apply_op(candidate, {"op": "remove_caption",
                                          "event_id": "cap-001"}, INVENTORY)
        cap2 = next(t for t in removed["tracks"] if t["kind"] == "caption")
        assert cap2["events"] == []

    def test_looping_short_song_is_accepted(self) -> None:
        # A 4s song on a 10s cut with loop:true must NOT be rejected for being
        # shorter than the timeline — the renderer loops it (-stream_loop).
        inv = {"assets": [{"asset_id": "loopsong", "filename": "loop.mp3",
                           "media_type": "audio", "duration_seconds": 4.0,
                           "audio": True}]}
        candidate, _ = apply_op(
            _plan(), {"op": "set_music_bed", "asset_id": "loopsong",
                      "loop": True}, inv)
        # bed source range exceeds asset duration on purpose; validation
        # exempts looping beds elsewhere, so this must not raise.
        ev = _music_events_of(candidate)[0]
        assert ev["duration_seconds"] == 10.0
        assert ev["music"]["bed"]["loop"] is True

    def test_silent_video_is_rejected_as_music(self) -> None:
        inv = {"assets": [{"asset_id": "silent", "filename": "broll.mp4",
                           "media_type": "video", "duration_seconds": 20.0,
                           "audio": False}]}
        with pytest.raises(PlanOpError, match="no audio"):
            apply_op(_plan(), {"op": "set_music_bed", "asset_id": "silent"}, inv)

    def test_non_numeric_gain_is_rejected(self) -> None:
        inv = {"assets": [{"asset_id": "song", "filename": "s.mp3",
                           "media_type": "audio", "duration_seconds": 90,
                           "audio": True}]}
        with pytest.raises(PlanOpError, match="number"):
            apply_op(_plan(), {"op": "set_music_bed", "asset_id": "song",
                               "gain_db": "loud"}, inv)

    def test_delete_ripples_captions(self) -> None:
        plan = _plan()
        plan["tracks"].append({
            "track_id": "cap1", "kind": "caption", "events": [
                {"event_id": "cap-001", "asset_id": None,
                 "source_start_seconds": None, "source_end_seconds": None,
                 "timeline_start_seconds": 8.0, "duration_seconds": 1.5,
                 "playback_rate": 1.0, "intent": "caption",
                 "observed_content": None, "confidence": 1.0,
                 "text": "última escena", "volume_db": None},
            ]})
        # Delete v01 (0-3s): the 4s clip length ripples everything after by -3s.
        candidate, _ = apply_op(
            plan, {"op": "delete_event", "event_id": "v01"}, INVENTORY)
        cap = next(t for t in candidate["tracks"] if t["kind"] == "caption")
        assert cap["events"][0]["timeline_start_seconds"] == 5.0

    def test_caption_edit_is_marked_user_authored(self) -> None:
        plan = self._plan_with_captions_and_music_source()
        candidate, _ = apply_op(
            plan, {"op": "edit_caption", "event_id": "cap-001",
                   "text": "texto corregido"}, INVENTORY)
        cap = next(t for t in candidate["tracks"] if t["kind"] == "caption")
        assert cap["events"][0]["user_authored"] is True


def _music_events_of(plan):
    mus = next(t for t in plan["tracks"]
               if t.get("kind") == "audio" and t.get("role") == "music")
    return mus["events"]


class TestCaptionOpsNotModelAuthored:
    def test_model_cannot_author_caption_text(self) -> None:
        # Even if the instruction model emits edit_caption, it is refused —
        # caption text is a rendered claim, authored only by the user.
        class _StubClient:
            def chat(self, *a, **k):
                return {"content": json.dumps(
                    {"op": "edit_caption", "event_id": "cap-001",
                     "text": "Ganamos el premio"})}
        op = instruction_to_op(_StubClient(), _plan(),
                               "corrige el subtítulo", INVENTORY)
        assert op["op"] == "reject"


class TestPartialFixes:
    def _plan_with_caption_at(self, t, dur=1.5, text="hola"):
        plan = _plan()
        plan["tracks"].append({
            "track_id": "cap1", "kind": "caption", "events": [{
                "event_id": "cap-001", "asset_id": None,
                "source_start_seconds": None, "source_end_seconds": None,
                "timeline_start_seconds": t, "duration_seconds": dur,
                "playback_rate": 1.0, "intent": "caption",
                "observed_content": None, "confidence": 1.0,
                "text": text, "volume_db": None,
            }]})
        return plan

    def test_delete_drops_captions_in_deleted_window(self) -> None:
        # v01 spans [0,3); a caption at 1.0 belongs to it and must be dropped.
        plan = self._plan_with_caption_at(1.0)
        candidate, _ = apply_op(
            plan, {"op": "delete_event", "event_id": "v01"}, INVENTORY)
        cap = next(t for t in candidate["tracks"] if t["kind"] == "caption")
        assert cap["events"] == []

    def test_bed_asset_consistency_enforced(self) -> None:
        from video_app.planning import validate_edit_plan
        from video_app.projects import ProjectService  # noqa
        plan = _plan()
        plan["tracks"].append({
            "track_id": "mus1", "kind": "audio", "role": "music", "events": [{
                "event_id": "mus-01", "asset_id": "clip_a",
                "source_start_seconds": 0.0, "source_end_seconds": 10.0,
                "timeline_start_seconds": 0.0, "duration_seconds": 10.0,
                "playback_rate": 1.0, "intent": "music",
                "observed_content": None, "confidence": 1.0, "text": None,
                "volume_db": -14,
                "music": {"mode": "bed", "recommended": None,
                          "bed": {"asset_id": "clip_b", "gain_db": -14,
                                  "duck_db": -12, "loop": True}}},
            ]})
        schema = PROJECT_ROOT / "app" / "schemas" / "edit-plan.schema.json"
        project = {"inventory": {"assets": [
            {"asset_id": "clip_a", "media_type": "video",
             "duration_seconds": 10.0, "audio": True},
            {"asset_id": "clip_b", "media_type": "audio",
             "duration_seconds": 10.0, "audio": True}]}}
        with pytest.raises(Exception, match="disagrees"):
            validate_edit_plan(plan, schema, project)

    def test_loop_false_string_is_respected(self) -> None:
        inv = {"assets": [{"asset_id": "song", "filename": "s.mp3",
                           "media_type": "audio", "duration_seconds": 90,
                           "audio": True}]}
        candidate, _ = apply_op(
            _plan(), {"op": "set_music_bed", "asset_id": "song",
                      "loop": "false"}, inv)
        ev = _music_events_of(candidate)[0]
        assert ev["music"]["bed"]["loop"] is False


class TestNewDefectFixes:
    def test_extend_end_keeps_following_scene_captions(self) -> None:
        # v01 spans [0,3); a caption at 3.2 belongs to v02, not v01. Extending
        # v01's end must ripple it forward, NOT delete it.
        plan = _plan()
        plan["tracks"].append({
            "track_id": "cap1", "kind": "caption", "events": [{
                "event_id": "cap-001", "asset_id": None,
                "source_start_seconds": None, "source_end_seconds": None,
                "timeline_start_seconds": 3.2, "duration_seconds": 1.0,
                "playback_rate": 1.0, "intent": "caption",
                "observed_content": None, "confidence": 1.0,
                "text": "de la segunda", "volume_db": None}]})
        candidate, _ = apply_op(plan, {
            "op": "trim_event", "event_id": "v01", "edge": "end",
            "direction": "extend", "seconds": 0.5,
        }, INVENTORY)
        cap = next(t for t in candidate["tracks"] if t["kind"] == "caption")
        assert len(cap["events"]) == 1
        assert cap["events"][0]["timeline_start_seconds"] == 3.7  # rippled +0.5

    def test_as_bool_rejects_garbage(self) -> None:
        from video_app.plan_ops import _as_bool
        assert _as_bool("no", default=True) is False
        assert _as_bool(None, default=True) is True
        with pytest.raises(PlanOpError):
            _as_bool("maybe", default=True)


class TestTransitionOps:
    def test_set_fades(self) -> None:
        candidate, summary = apply_op(
            _plan(), {"op": "set_fades", "intro_seconds": 0.5,
                      "outro_seconds": 0.8}, INVENTORY)
        assert candidate["transitions"] == {
            "intro_fade_seconds": 0.5, "outro_fade_seconds": 0.8}
        assert "apertura 0.5s" in summary

    def test_set_fades_zero_removes(self) -> None:
        candidate, summary = apply_op(
            _plan(), {"op": "set_fades", "intro_seconds": 0,
                      "outro_seconds": 0}, INVENTORY)
        assert candidate["transitions"] == {
            "intro_fade_seconds": 0.0, "outro_fade_seconds": 0.0}
        assert "quitados" in summary

    def test_set_fades_out_of_range(self) -> None:
        with pytest.raises(PlanOpError, match="0..3"):
            apply_op(_plan(), {"op": "set_fades", "intro_seconds": 9}, INVENTORY)

    def test_set_transition_dip(self) -> None:
        candidate, summary = apply_op(
            _plan(), {"op": "set_transition", "event_id": "v02",
                      "type": "fade_black", "duration_seconds": 0.6}, INVENTORY)
        v02 = next(e for e in candidate["tracks"][0]["events"]
                   if e["event_id"] == "v02")
        assert v02["transition_out"] == {"type": "fade_black",
                                         "duration_seconds": 0.6}
        assert "negro" in summary

    def test_set_transition_cut(self) -> None:
        candidate, _ = apply_op(
            _plan(), {"op": "set_transition", "event_id": "v01",
                      "type": "cut"}, INVENTORY)
        v01 = next(e for e in candidate["tracks"][0]["events"]
                   if e["event_id"] == "v01")
        assert v01["transition_out"]["type"] == "cut"

    def test_dissolve_is_refused_for_now(self) -> None:
        with pytest.raises(PlanOpError, match="dissolve"):
            apply_op(_plan(), {"op": "set_transition", "event_id": "v01",
                               "type": "dissolve"}, INVENTORY)


class TestTransitionReviewFixes:
    def test_partial_set_fades_keeps_other_side(self) -> None:
        plan = _plan()
        plan["transitions"] = {"intro_fade_seconds": 0.4, "outro_fade_seconds": 0.6}
        candidate, _ = apply_op(
            plan, {"op": "set_fades", "intro_seconds": 0.0}, INVENTORY)
        assert candidate["transitions"] == {
            "intro_fade_seconds": 0.0, "outro_fade_seconds": 0.6}

    def test_set_fades_needs_a_field(self) -> None:
        with pytest.raises(PlanOpError, match="intro_seconds"):
            apply_op(_plan(), {"op": "set_fades"}, INVENTORY)

    def test_set_transition_non_string_type_rejected(self) -> None:
        with pytest.raises(PlanOpError, match="must be a string"):
            apply_op(_plan(), {"op": "set_transition", "event_id": "v01",
                               "type": []}, INVENTORY)


class TestTransitionClamping:
    def test_set_transition_clamps_to_short_neighbor(self) -> None:
        # v01 is 3s, v02 is 4s; a 3s dip is split across them but the renderer
        # can only deliver up to the shorter clip — store the achievable value.
        plan = _plan()
        candidate, summary = apply_op(
            plan, {"op": "set_transition", "event_id": "v01",
                   "type": "fade_black", "duration_seconds": 3.0}, INVENTORY)
        v01 = next(e for e in candidate["tracks"][0]["events"]
                   if e["event_id"] == "v01")
        assert v01["transition_out"]["duration_seconds"] <= 3.0
        assert v01["transition_out"]["duration_seconds"] == 3.0  # min(3, v01=3, v02=4)

    def test_set_fades_clamps_per_side_to_half(self) -> None:
        # Each side is clamped to half the cut (the renderer's cap); the sides
        # are clamped INDEPENDENTLY so one never mutates the other.
        plan = _plan()
        plan["project"]["duration_seconds"] = 3.0  # per-side cap = 1.5s
        candidate, _ = apply_op(
            plan, {"op": "set_fades", "intro_seconds": 3, "outro_seconds": 0},
            INVENTORY)
        t = candidate["transitions"]
        assert t["intro_fade_seconds"] == 1.5
        assert t["outro_fade_seconds"] == 0.0

    def test_partial_set_fades_never_mutates_other_side(self) -> None:
        # Regression: rescaling a pair silently changed the untouched side.
        plan = _plan()
        plan["project"]["duration_seconds"] = 1.5
        plan["transitions"] = {"intro_fade_seconds": 0.2, "outro_fade_seconds": 0.3}
        candidate, _ = apply_op(
            plan, {"op": "set_fades", "intro_seconds": 0.4}, INVENTORY)
        assert candidate["transitions"]["outro_fade_seconds"] == 0.3  # untouched


class TestTransitionSeamGuards:
    def test_set_transition_refused_on_final_scene(self) -> None:
        with pytest.raises(PlanOpError, match="última"):
            apply_op(_plan(), {"op": "set_transition", "event_id": "v03",
                               "type": "fade_black"}, INVENTORY)

    def test_set_transition_refused_across_gap(self) -> None:
        plan = _plan()
        # push v02 later so v01 -> v02 is no longer contiguous
        plan["tracks"][0]["events"][1]["timeline_start_seconds"] = 4.0
        with pytest.raises(PlanOpError, match="hueco"):
            apply_op(plan, {"op": "set_transition", "event_id": "v01",
                            "type": "fade_black"}, INVENTORY)


class TestFadePolicyConsistency:
    def test_intro_only_update_keeps_large_outro(self) -> None:
        # one policy everywhere: an intro-only update must not clamp/alter the
        # stored outro, even if the outro is at the per-side cap.
        plan = _plan()
        plan["project"]["duration_seconds"] = 4.0  # cap 2.0
        plan["transitions"] = {"intro_fade_seconds": 0.4, "outro_fade_seconds": 2.0}
        candidate, _ = apply_op(
            plan, {"op": "set_fades", "intro_seconds": 0.5}, INVENTORY)
        assert candidate["transitions"] == {
            "intro_fade_seconds": 0.5, "outro_fade_seconds": 2.0}


class TestRippleRescalesFades:
    def test_delete_reclamps_large_fade_to_new_duration(self) -> None:
        plan = _plan()  # 10s
        plan["transitions"] = {"intro_fade_seconds": 0.4, "outro_fade_seconds": 3.0}
        # delete v02 (4s) -> duration 6s, cap 3.0; outro 3.0 still fits
        c1, _ = apply_op(plan, {"op": "delete_event", "event_id": "v02"}, INVENTORY)
        assert c1["transitions"]["outro_fade_seconds"] == 3.0
        # delete another (v03, 3s) -> duration 3s, cap 1.5; outro clamps to 1.5
        c2, _ = apply_op(c1, {"op": "delete_event", "event_id": "v03"}, INVENTORY)
        assert c2["project"]["duration_seconds"] == 3.0
        assert c2["transitions"]["outro_fade_seconds"] == 1.5


class TestDipReconciliation:
    def test_deleting_successor_resets_predecessor_dip_to_cut(self) -> None:
        plan = _plan()
        # v01 dips into v02; delete v02 so v01's seam partner is gone
        plan["tracks"][0]["events"][0]["transition_out"] = {
            "type": "fade_black", "duration_seconds": 0.5}
        candidate, _ = apply_op(
            plan, {"op": "delete_event", "event_id": "v02"}, INVENTORY)
        v01 = next(e for e in candidate["tracks"][0]["events"]
                   if e["event_id"] == "v01")
        # v01 is still contiguous with v03 (which shifted up), so the dip stays a
        # dip — but its duration is re-clamped to the surviving neighbours' spans
        assert v01["transition_out"]["type"] in ("fade_black", "cut")
        assert v01["transition_out"]["duration_seconds"] <= 0.5

    def test_deleting_final_clip_drops_predecessor_dip(self) -> None:
        plan = _plan()
        # v02 dips into v03 (the last clip); delete v03 so v02 becomes last
        plan["tracks"][0]["events"][1]["transition_out"] = {
            "type": "fade_white", "duration_seconds": 0.5}
        candidate, _ = apply_op(
            plan, {"op": "delete_event", "event_id": "v03"}, INVENTORY)
        v02 = next(e for e in candidate["tracks"][0]["events"]
                   if e["event_id"] == "v02")
        assert v02["transition_out"]["type"] == "cut"  # no successor -> no dip


class TestTransitionPrecision:
    def test_floor_ms_keeps_grid_values(self) -> None:
        from video_app.plan_ops import _floor_ms
        assert _floor_ms(1.005) == 1.005      # already on grid, not under-floored
        assert _floor_ms(0.5166665) == 0.516  # floored below half, never above
        assert _floor_ms(0.4) == 0.4

    def test_24fps_frame_contiguous_dip_survives(self) -> None:
        from video_app.plan_ops import _reconcile_dips
        # two frame-quantized 24fps clips whose float ends accumulate error > 1e-6
        plan = {
            "project": {"fps": 24, "duration_seconds": 0.625},
            "tracks": [{"track_id": "v1", "kind": "video", "events": [
                {"event_id": "v01", "asset_id": "a", "source_start_seconds": 0.0,
                 "source_end_seconds": 0.208333, "timeline_start_seconds": 0.0,
                 "duration_seconds": 0.208333,
                 "transition_out": {"type": "fade_black", "duration_seconds": 0.1}},
                {"event_id": "v02", "asset_id": "a", "source_start_seconds": 0.0,
                 "source_end_seconds": 0.416667, "timeline_start_seconds": 0.208333,
                 "duration_seconds": 0.416667, "transition_out": None}]},
                {"track_id": "a1", "kind": "audio", "events": []}]}
        _reconcile_dips(plan)
        v01 = plan["tracks"][0]["events"][0]
        assert v01["transition_out"]["type"] == "fade_black"  # NOT reset to cut


class TestSyncBackRefitHelpers:
    def _music_plan(self, mode, loop=True, dur=10.0, span=10.0):
        bed = {"asset_id": "song", "gain_db": -14, "duck_db": -12, "loop": loop}
        music = {"mode": mode, "recommended": None if mode == "bed" else {},
                 "bed": bed if mode == "bed" else None}
        plan = _plan()
        plan["tracks"].append({
            "track_id": "mus1", "kind": "audio", "role": "music", "events": [{
                "event_id": "mus-01",
                "asset_id": "song" if mode == "bed" else None,
                "source_start_seconds": 0.0 if mode == "bed" else None,
                "source_end_seconds": span if mode == "bed" else None,
                "timeline_start_seconds": 0.0, "duration_seconds": dur,
                "playback_rate": 1.0, "intent": "music", "observed_content": None,
                "confidence": 1.0, "text": None, "volume_db": -14, "music": music}]})
        return plan

    def test_refit_looping_bed_follows_new_duration(self) -> None:
        from video_app.plan_ops import _refit_music_bed
        plan = self._music_plan("bed", loop=True, dur=10.0, span=4.0)
        plan["project"]["duration_seconds"] = 6.0  # cut lengthened
        _refit_music_bed(plan)
        ev = _music_events_of(plan)[0]
        assert ev["duration_seconds"] == 6.0        # timeline span follows
        assert ev["source_end_seconds"] == 4.0      # loop source range unchanged

    def test_refit_one_shot_bed_clamps_to_new_duration(self) -> None:
        from video_app.plan_ops import _refit_music_bed
        plan = self._music_plan("bed", loop=False, dur=8.0, span=8.0)
        plan["project"]["duration_seconds"] = 5.0   # cut shortened
        _refit_music_bed(plan)
        ev = _music_events_of(plan)[0]
        assert ev["duration_seconds"] == 5.0
        assert ev["source_end_seconds"] == 5.0

    def test_refit_recommended_annotation_follows_duration(self) -> None:
        from video_app.plan_ops import _refit_music_bed
        plan = self._music_plan("recommended", dur=10.0)
        plan["project"]["duration_seconds"] = 7.0
        _refit_music_bed(plan)
        assert _music_events_of(plan)[0]["duration_seconds"] == 7.0

    def test_clamp_hook_title_to_short_cut(self) -> None:
        from video_app.plan_ops import _clamp_titles_to_duration
        plan = _plan()
        plan["tracks"][2]["events"][0]["timeline_start_seconds"] = 0.0
        plan["tracks"][2]["events"][0]["duration_seconds"] = 2.5
        plan["project"]["duration_seconds"] = 2.0  # cut shorter than the title
        _clamp_titles_to_duration(plan)
        assert plan["tracks"][2]["events"][0]["duration_seconds"] == 2.0

    def test_title_past_new_end_is_dropped(self) -> None:
        from video_app.plan_ops import _clamp_titles_to_duration
        plan = _plan()
        plan["tracks"][2]["events"][0]["timeline_start_seconds"] = 8.0
        plan["project"]["duration_seconds"] = 6.0  # title starts past the end
        _clamp_titles_to_duration(plan)
        assert plan["tracks"][2]["events"] == []


class TestMusicRecommendationOp:
    def test_set_music_recommendation_installs_recommended_track(self) -> None:
        candidate, summary = apply_op(
            _plan(), {"op": "set_music_recommendation",
                      "name": "Sunset Lover — Petit Biscuit", "vibe": "chill",
                      "bpm": 95, "energy": "medium"}, INVENTORY)
        ev = _music_events_of(candidate)[0]
        assert ev["music"]["mode"] == "recommended"
        assert ev["music"]["recommended"]["name"] == "Sunset Lover — Petit Biscuit"
        assert ev["music"]["recommended"]["bpm"] == 95
        assert ev["asset_id"] is None            # nothing burned
        assert "Petit Biscuit" in summary

    def test_recommendation_bounds_bpm_and_energy(self) -> None:
        candidate, _ = apply_op(
            _plan(), {"op": "set_music_recommendation", "name": "x",
                      "bpm": 999, "energy": "supersonic"}, INVENTORY)
        rec = _music_events_of(candidate)[0]["music"]["recommended"]
        assert rec.get("bpm") is None            # out of [30,300] -> dropped
        assert rec.get("energy") is None         # not a valid enum -> dropped

    def test_recommendation_replaces_existing_music(self) -> None:
        c1, _ = apply_op(_plan(), {"op": "set_music_recommendation",
                                   "name": "first"}, INVENTORY)
        c2, _ = apply_op(c1, {"op": "set_music_recommendation",
                              "name": "second"}, INVENTORY)
        music_tracks = [t for t in c2["tracks"] if t.get("role") == "music"]
        assert len(music_tracks) == 1            # overwrites, never stacks
        assert music_tracks[0]["events"][0]["music"]["recommended"]["name"] == "second"
