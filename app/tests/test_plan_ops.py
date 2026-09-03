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
        assert candidate["tracks"][2]["events"][0]["text"] == "Nuevo título"

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

    def test_voiceover_requires_audio_asset(self) -> None:
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
