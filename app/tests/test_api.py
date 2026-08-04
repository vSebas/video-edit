from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from video_app.config import Settings
from video_app.finalization import build_review_outcome, validate_review_outcome
from video_app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POC_ROOT = PROJECT_ROOT / "poc-morning-routine"


def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        root=PROJECT_ROOT,
        runtime=tmp_path / "runtime",
        poc_root=POC_ROOT,
        semantic_source_root=tmp_path / "openstoryline",
    )
    return TestClient(create_app(settings))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def wait_for_job(current: TestClient, job_id: str) -> dict:
    for _ in range(200):
        job = current.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"Job did not finish: {job_id}")


def make_saved_semantic_run(
    root: Path, provider: str, session_id: str, filename: str
) -> None:
    run = root / provider / "outputs" / session_id
    write_json(
        run / "session_state.json",
        {
            "session_id": session_id,
            "vlm_model_key": "custom",
            "custom_vlm_config": {"model": "test-vlm", "api_key": "must-not-persist"},
            "load_media": {
                "upload-1": {
                    "id": "upload-1",
                    "name": filename,
                    "path": f"/app/outputs/{session_id}/media/media_0001.mp4",
                }
            },
        },
    )
    write_json(
        run / "load_media" / "load_media_1.json",
        {
            "payload": {
                "media": [
                    {
                        "media_id": "media_0001",
                        "media_type": "video",
                        "metadata": {"duration": 1000},
                        "path": f"/app/outputs/{session_id}/media/media_0001.mp4",
                    }
                ]
            }
        },
    )
    write_json(
        run / "split_shots" / "split_shots_1.json",
        {
            "payload": {
                "clips": [
                    {
                        "clip_id": "clip_0001",
                        "source_ref": {
                            "media_id": "media_0001",
                            "start": 0,
                            "end": 1067,
                            "duration": 1067,
                        },
                    }
                ]
            }
        },
    )
    write_json(
        run / "understand_clips" / "understand_clips_1.json",
        {
            "payload": {
                "clip_captions": [
                    {
                        "clip_id": "clip_0001",
                        "caption": (
                            "A confused person appears to be speaking while holding Chobani."
                        ),
                        "source_ref": {"media_id": "media_0001"},
                    }
                ],
                "overall": "",
            }
        },
    )


def test_fixture_is_complete(tmp_path):
    with client(tmp_path) as current:
        response = current.get("/api/projects/morning-routine")
        assert response.status_code == 200
        project = response.json()
        assert project["status"] == "ready"
        assert len(project["inventory"]["assets"]) == 7
        assert len(project["concepts"]) == 3
        assert project["plan_summary"]["duration_seconds"] == 31.0
        if "render" in project["outputs"]:
            assert project["outputs"]["render"]["size_bytes"] > 0


def test_concept_selection_reports_plan_availability(tmp_path):
    with client(tmp_path) as current:
        ready = current.post(
            "/api/projects/morning-routine/selection",
            json={"concept_id": "concept_chronological_routine"},
        )
        assert ready.status_code == 200
        assert ready.json()["plan_available"] is True

        pending = current.post(
            "/api/projects/morning-routine/selection",
            json={"concept_id": "concept_comedy_friction"},
        )
        assert pending.status_code == 200
        assert pending.json()["plan_available"] is False


def test_generic_media_folder_is_indexed_without_inventing_semantics(tmp_path):
    source = PROJECT_ROOT / "runtime" / "test-fixtures" / tmp_path.name
    source.mkdir(parents=True, exist_ok=True)
    sample = source / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x335577:s=320x240:d=1:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(sample),
        ],
        check=True,
    )
    try:
        with client(tmp_path) as current:
            response = current.post(
                "/api/projects",
                json={
                    "name": "Technical ingest test",
                    "source_directory": str(source.relative_to(PROJECT_ROOT)),
                    "prompt": "Make a short clip.",
                },
            )
            assert response.status_code == 201
            project = response.json()
            assert project["status"] == "awaiting_semantic_analysis"
            assert project["analysis"]["technical"] == "completed"
            assert project["analysis"]["visual"] == "unavailable"
            assert project["concepts"] == []
            assert project["inventory"]["assets"][0]["sha256"]
            assert project["inventory"]["assets"][0]["thumbnail_available"] is True
    finally:
        if sample.exists():
            sample.unlink()
        if source.exists():
            source.rmdir()


def test_saved_provider_output_is_normalized_as_review_only_evidence(tmp_path):
    source = PROJECT_ROOT / "runtime" / "test-fixtures" / f"semantic-{tmp_path.name}"
    source.mkdir(parents=True, exist_ok=True)
    sample = source / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x335577:s=320x240:d=1:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(sample),
        ],
        check=True,
    )
    session_id = "a" * 32
    make_saved_semantic_run(tmp_path / "openstoryline", "qwen", session_id, sample.name)

    try:
        with client(tmp_path) as current:
            created = current.post(
                "/api/projects",
                json={
                    "name": "Semantic import test",
                    "source_directory": str(source.relative_to(PROJECT_ROOT)),
                    "prompt": "Make a short clip.",
                },
            )
            assert created.status_code == 201
            created_project = created.json()
            project_id = created_project["project_id"]
            source_duration = created_project["inventory"]["assets"][0]["duration_seconds"]

            queued = current.post(
                f"/api/projects/{project_id}/analysis/openstoryline",
                json={"provider": "qwen", "session_id": session_id},
            )
            assert queued.status_code == 202
            job = wait_for_job(current, queued.json()["job_id"])
            assert job["status"] == "completed", job["error"]

            project = current.get(f"/api/projects/{project_id}").json()
            assert project["status"] == "semantic_review_required"
            assert project["analysis"]["visual"] == "awaiting_review"
            assert project["inventory"]["assets"][0]["analysis_status"] == "candidate_semantics"
            assert len(project["provider_runs"]) == 1

            run_key = project["provider_runs"][0]["run_key"]
            detail = current.get(
                f"/api/projects/{project_id}/analysis/runs/{run_key}"
            ).json()
            assert detail["safe_for_edit_plan"] is False
            assert detail["summary"]["mapped_media_count"] == 1
            assert detail["summary"]["clamped_count"] == 1
            observation = detail["observations"][0]
            assert observation["normalization_status"] == "accepted"
            assert observation["raw_end_seconds"] == 1.067
            assert observation["end_seconds"] == source_duration
            assert "end_clamped_to_source_duration" in observation["adjustments"]
            assert "brand_or_product_claim" in observation["risk_flags"]
            assert "intent_or_emotion_inference" in observation["risk_flags"]
            assert "unverified_speech_claim" in observation["risk_flags"]

            reviewed = current.post(
                f"/api/projects/{project_id}/analysis/runs/{run_key}/reviews",
                json={
                    "evidence_id": observation["evidence_id"],
                    "action": "approve",
                    "caption": "A person holds a container.",
                    "note": "Removed unsupported intent, speech, and brand claims.",
                },
            )
            assert reviewed.status_code == 200
            reviewed_run = reviewed.json()
            assert reviewed_run["review_status"] == "reviewed"
            assert reviewed_run["safe_for_edit_plan"] is True
            assert reviewed_run["summary"]["pending_review_count"] == 0
            assert reviewed_run["summary"]["approved_count"] == 1
            assert reviewed_run["observations"][0]["reviewed_caption"] == (
                "A person holds a container."
            )

            persisted = tmp_path / "runtime" / project_id / "analysis" / "runs" / run_key
            assert not (persisted / "raw" / "session_state.json").exists()
            assert (persisted / "reviews.json").is_file()
            normalized_persisted = json.loads(
                (persisted / "normalized.json").read_text(encoding="utf-8")
            )
            assert normalized_persisted["observations"][0]["caption"].startswith(
                "A confused person"
            )
            assert "must-not-persist" not in "".join(
                path.read_text(encoding="utf-8") for path in persisted.rglob("*.json")
            )

            finalized = current.post(
                f"/api/projects/{project_id}/analysis/finalized"
            )
            assert finalized.status_code == 200
            outcome = finalized.json()
            assert outcome["schema_version"] == "reviewed-evidence-set.v1"
            assert outcome["status"] == "provider_selection_required"
            assert outcome["planning_eligible"] is True
            assert outcome["freshness"] == "current"
            assert outcome["candidate_sets"][0]["accepted_evidence"][0][
                "observation"
            ] == "A person holds a container."
            assert outcome["candidate_sets"][0]["review"]["edited_count"] == 1
            assert outcome["material_conflicts"] == []

            fetched = current.get(
                f"/api/projects/{project_id}/analysis/finalized"
            )
            assert fetched.status_code == 200
            assert fetched.json()["revision_id"] == outcome["revision_id"]
            versioned = (
                tmp_path
                / "runtime"
                / project_id
                / "analysis"
                / "finalized"
                / "versions"
                / f"{outcome['revision_id']}.json"
            )
            assert versioned.is_file()
    finally:
        if sample.exists():
            sample.unlink()
        if source.exists():
            source.rmdir()


def test_finalized_outcome_surfaces_conflict_without_overriding_approval():
    run = {
        "run_key": "gemini-vlm-example",
        "run_id": "b" * 32,
        "provider": {
            "adapter": "openstoryline",
            "id": "gemini-vlm",
            "model": "gemini-test",
        },
        "review_status": "reviewed",
        "summary": {
            "pending_review_count": 0,
            "clamped_count": 1,
            "risk_flagged_count": 1,
        },
        "observations": [
            {
                "evidence_id": "evidence-1",
                "asset_id": "asset-1",
                "filename": "source.mp4",
                "start_seconds": 1.0,
                "end_seconds": 2.0,
                "caption": "An invented event occurs.",
                "reviewed_caption": "An invented event occurs.",
                "review_note": None,
                "reviewed_at": "2026-07-18T12:00:00Z",
                "normalization_status": "accepted",
                "review_status": "reviewed",
                "adjustments": ["end_clamped_to_source_duration"],
                "risk_flags": ["intent_or_emotion_inference"],
            }
        ],
    }
    benchmark = {
        "comparison": {"directly_comparable": False, "reason": "Different ranges."},
        "runs": {},
        "findings": [
            {
                "finding_id": "finding-1",
                "run_key": "gemini-vlm-example",
                "evidence_id": "evidence-1",
                "severity": "material",
                "summary": "The event is absent.",
                "verified_observation": "Only ordinary movement occurs.",
                "verification_source": "verified.json#asset-1",
            }
        ],
    }

    outcome = build_review_outcome(
        "example", [run], benchmark, "2026-07-18T12:05:00Z"
    )
    validate_review_outcome(
        outcome, POC_ROOT / "schemas" / "reviewed-evidence-set.schema.json"
    )

    candidate = outcome["candidate_sets"][0]
    assert candidate["review"]["approved_count"] == 1
    assert candidate["accepted_evidence"][0]["observation"] == (
        "An invented event occurs."
    )
    assert candidate["quality_signals"]["material_conflict_count"] == 1
    assert candidate["eligible_for_planning"] is False
    assert outcome["status"] == "conflicts_require_resolution"
    assert outcome["planning_eligible"] is False
