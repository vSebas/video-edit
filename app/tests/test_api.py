from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from video_app.config import Settings
from video_app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POC_ROOT = PROJECT_ROOT / "poc-morning-routine"


def client(tmp_path: Path) -> TestClient:
    settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime", poc_root=POC_ROOT)
    return TestClient(create_app(settings))


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
