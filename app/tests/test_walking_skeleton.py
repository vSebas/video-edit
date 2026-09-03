from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_app.planning import (
    PlanningError,
    _sanitize_concepts,
    compile_edit_plan,
    validate_edit_plan,
)
from video_app.providers import parse_json_content
from video_app.visual import auto_review_decisions, detect_shots, frame_timestamps

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EDIT_PLAN_SCHEMA = PROJECT_ROOT / "app" / "schemas" / "edit-plan.schema.json"


def sample_project() -> dict:
    return {
        "project_id": "unit-test",
        "inventory": {
            "assets": [
                {
                    "asset_id": "clip_a",
                    "filename": "a.mp4",
                    "media_type": "video",
                    "duration_seconds": 20.0,
                    "video": {"width": 1920, "height": 1080},
                    "audio": {"sample_rate": 48000, "channels": 2},
                },
                {
                    "asset_id": "clip_b",
                    "filename": "b.mp4",
                    "media_type": "video",
                    "duration_seconds": 10.0,
                    "video": {"width": 1080, "height": 1920},
                    "audio": None,
                },
            ]
        },
    }


def concept_beat(asset_id: str, start: float, end: float, beat_id: str) -> dict:
    return {
        "beat_id": beat_id,
        "purpose": f"Beat {beat_id}",
        "target_duration_seconds": end - start,
        "evidence": [
            {
                "asset_id": asset_id,
                "start_seconds": start,
                "end_seconds": end,
                "observed_content": "Test content.",
                "confidence": 0.9,
            }
        ],
    }


def sample_concepts_document() -> dict:
    concepts = []
    for index in ("one", "two"):
        concepts.append(
            {
                "concept_id": f"concept_{index}",
                "title": f"Concept {index}",
                "topic": "Test topic.",
                "audience": "Test audience.",
                "platforms": ["instagram_reel"],
                "target_duration_seconds": 15,
                "hook": "Open on the first clip.",
                "structure": [
                    concept_beat("clip_a", 0.0, 4.0, "hook"),
                    concept_beat("clip_b", 2.0, 6.0, "middle"),
                    concept_beat("clip_a", 10.0, 14.0, "ending"),
                ],
                "strengths": ["clear"],
                "weaknesses": ["short"],
                "missing_shots": [],
            }
        )
    return {
        "schema_version": "creative-concepts.v1",
        "generated_at": "2026-08-04T00:00:00Z",
        "benchmark_id": "unit-test-auto-v1",
        "footage_summary": "Two clips.",
        "concepts": concepts,
    }


def test_parse_json_content_tolerates_fences() -> None:
    assert parse_json_content('{"a": 1}') == {"a": 1}
    assert parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_content('noise {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    with pytest.raises(json.JSONDecodeError):
        parse_json_content("not json at all")


def test_sanitize_concepts_drops_unknown_assets_and_clamps() -> None:
    project = sample_project()
    document = sample_concepts_document()
    document["concepts"][0]["structure"][1]["evidence"][0]["asset_id"] = "ghost"
    document["concepts"][0]["structure"].append(
        concept_beat("clip_b", 8.0, 99.0, "overlong")
    )
    _sanitize_concepts(document, project)

    first = document["concepts"][0]
    beat_ids = [beat["beat_id"] for beat in first["structure"]]
    assert "middle" not in beat_ids  # unknown asset dropped the whole beat
    overlong = next(beat for beat in first["structure"] if beat["beat_id"] == "overlong")
    assert overlong["evidence"][0]["end_seconds"] == 10.0  # clamped to duration


def test_sanitize_concepts_requires_three_grounded_beats() -> None:
    project = sample_project()
    document = sample_concepts_document()
    document["concepts"][1]["structure"] = document["concepts"][1]["structure"][:2]
    _sanitize_concepts(document, project)
    assert [item["concept_id"] for item in document["concepts"]] == ["concept_one"]


def test_compile_edit_plan_produces_valid_linked_plan() -> None:
    project = sample_project()
    document = sample_concepts_document()
    _sanitize_concepts(document, project)
    plan = compile_edit_plan(project, document, "concept_one")
    validate_edit_plan(plan, EDIT_PLAN_SCHEMA, project)

    video_track = next(t for t in plan["tracks"] if t["kind"] == "video")
    primary_audio = next(
        t for t in plan["tracks"]
        if t["kind"] == "audio" and t.get("role") in (None, "primary")
    )
    assert len(video_track["events"]) == len(primary_audio["events"]) == 3
    for video, audio in zip(video_track["events"], primary_audio["events"]):
        assert video["asset_id"] == audio["asset_id"]
        assert video["source_start_seconds"] == audio["source_start_seconds"]
        assert video["source_end_seconds"] == audio["source_end_seconds"]
    assert plan["project"]["duration_seconds"] == pytest.approx(12.0)
    starts = [event["timeline_start_seconds"] for event in video_track["events"]]
    assert starts == [0.0, 4.0, 8.0]
    title_track = next(t for t in plan["tracks"] if t["kind"] == "title")
    assert title_track["events"][0]["text"] == "Concept one"
    # Vocabulary expansion: every compiled plan carries a caption track and a
    # default (recommended-mode) music track.
    caption_track = next(t for t in plan["tracks"] if t["kind"] == "caption")
    assert caption_track is not None
    music_track = next(t for t in plan["tracks"] if t.get("role") == "music")
    assert music_track["events"][0]["music"]["mode"] == "recommended"


def test_compile_edit_plan_rejects_unknown_concept() -> None:
    project = sample_project()
    document = sample_concepts_document()
    _sanitize_concepts(document, project)
    with pytest.raises(PlanningError):
        compile_edit_plan(project, document, "concept_missing")


def test_validate_edit_plan_rejects_range_overrun() -> None:
    project = sample_project()
    document = sample_concepts_document()
    _sanitize_concepts(document, project)
    plan = compile_edit_plan(project, document, "concept_one")
    plan["tracks"][0]["events"][0]["source_end_seconds"] = 99.0
    with pytest.raises(PlanningError):
        validate_edit_plan(plan, EDIT_PLAN_SCHEMA, project)


def test_auto_review_approves_only_confident_unflagged() -> None:
    normalized = {
        "project_id": "unit-test",
        "observations": [
            {
                "evidence_id": "e1",
                "caption": "Clear action.",
                "risk_flags": [],
                "model_confidence": 0.9,
            },
            {
                "evidence_id": "e2",
                "caption": "The person seems happy.",
                "risk_flags": ["intent_or_emotion_inference"],
                "model_confidence": 0.95,
            },
            {
                "evidence_id": "e3",
                "caption": "Blurry something.",
                "risk_flags": [],
                "model_confidence": 0.4,
            },
        ],
    }
    reviews = auto_review_decisions(normalized)
    assert set(reviews["decisions"]) == {"e1"}
    assert reviews["events"][0]["action"] == "approve"
    assert "auto-approved" in reviews["events"][0]["note"]


def test_frame_timestamps_short_and_normal_shots() -> None:
    assert frame_timestamps(3.0, 3.4) == [3.0]
    stamps = frame_timestamps(0.0, 10.0)
    assert len(stamps) == 3
    assert stamps[0] < stamps[1] < stamps[2] <= 10.0


def test_detect_shots_on_synthetic_video(tmp_path: Path) -> None:
    sample = tmp_path / "sample.mp4"
    # Two visually distinct halves so scene detection has a real boundary.
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:size=320x240:duration=3:rate=10",
            "-f", "lavfi", "-i", "color=blue:size=320x240:duration=3:rate=10",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1[out]",
            "-map", "[out]", str(sample),
        ],
        check=True,
    )
    shots = detect_shots(sample, 6.0)
    assert shots
    assert shots[0][0] == 0.0
    assert shots[-1][1] == 6.0
    for start, end in shots:
        assert end - start > 0


def test_browse_lists_workspace_directories(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_app.config import Settings
    from video_app.main import create_app

    settings = Settings(
        root=PROJECT_ROOT, runtime=tmp_path / "runtime"
    )
    with TestClient(create_app(settings)) as current:
        listing = current.get("/api/browse").json()
        names = [item["name"] for item in listing["directories"]]
        assert "footage" in names
        # Internal directories are hidden from the clip-folder picker.
        assert "runtime" not in names
        assert "app" not in names
        assert current.get("/api/browse", params={"path": "../etc"}).status_code == 400


def test_delete_project_removes_runtime_state_only(tmp_path: Path) -> None:
    import subprocess as sp

    from fastapi.testclient import TestClient
    from video_app.config import Settings
    from video_app.main import create_app

    source = PROJECT_ROOT / "runtime" / "test-fixtures" / f"delete-{tmp_path.name}"
    source.mkdir(parents=True, exist_ok=True)
    sample = source / "clip.mp4"
    sp.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0x223344:s=320x240:d=1:r=30",
            "-c:v", "libx264", str(sample),
        ],
        check=True,
    )
    settings = Settings(
        root=PROJECT_ROOT, runtime=tmp_path / "runtime"
    )
    try:
        with TestClient(create_app(settings)) as current:
            created = current.post(
                "/api/projects",
                json={
                    "name": "Delete me",
                    "source_directory": str(source.relative_to(PROJECT_ROOT)),
                    "prompt": "",
                },
            )
            assert created.status_code == 201
            project_id = created.json()["project_id"]
            assert (tmp_path / "runtime" / project_id / "project.json").is_file()

            assert current.delete("/api/projects/no-such-project").status_code == 400
            deleted = current.delete(f"/api/projects/{project_id}")
            assert deleted.status_code == 200
            assert not (tmp_path / "runtime" / project_id).exists()
            assert sample.is_file()  # source media untouched
    finally:
        if sample.exists():
            sample.unlink()
        if source.exists():
            source.rmdir()


def test_shot_moments_converts_and_clamps_relative_timestamps() -> None:
    from video_app.visual import shot_moments

    parsed = {
        "best_moment": {"start_seconds": 1.5, "end_seconds": 2.5, "why": "waves at camera"},
        "moments": [
            {"start_seconds": 1.5, "end_seconds": 2.5, "label": "duplicate of best"},
            {"start_seconds": 5.0, "end_seconds": 99.0, "label": "runs past shot end"},
            {"start_seconds": 3.0, "end_seconds": 3.1, "label": "too short"},
        ],
    }
    moments = shot_moments(parsed, start=10.0, end=18.0)
    assert moments[0] == {"start": 11.5, "end": 12.5, "label": "waves at camera", "is_best": True}
    assert len(moments) == 2  # duplicate and too-short dropped
    assert moments[1]["end"] == 18.0  # clamped to shot end


def test_snap_spans_moves_cut_out_of_spoken_words() -> None:
    from video_app.planning import snap_spans_to_speech

    project = sample_project()
    words = {
        "clip_a": [
            {"start_seconds": 3.8, "end_seconds": 4.4},   # word straddles span end 4.0
            {"start_seconds": 9.9, "end_seconds": 10.6},  # word straddles span start 10.0
        ]
    }
    spans = [
        {"label": "a", "asset_id": "clip_a", "source_start_seconds": 0.0,
         "source_end_seconds": 4.0, "intent": "x", "observed_content": "y", "confidence": 0.9},
        {"label": "b", "asset_id": "clip_a", "source_start_seconds": 10.0,
         "source_end_seconds": 14.0, "intent": "x", "observed_content": "y", "confidence": 0.9},
    ]
    snapped = snap_spans_to_speech(spans, words, project)
    assert snapped[0]["source_end_seconds"] == pytest.approx(4.52)   # finishes the word + pad
    # start boundary inside a word pulls back before the word begins
    assert snapped[1]["source_start_seconds"] == pytest.approx(9.78)
    # untouched boundaries stay put
    assert snapped[0]["source_start_seconds"] == 0.0
