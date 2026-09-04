from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from video_app.config import Settings
from video_app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        root=PROJECT_ROOT,
        runtime=tmp_path / "runtime",
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



def test_pwa_shell_is_served(tmp_path):
    """The installable-PWA shell files serve with the right content types."""
    c = client(tmp_path)
    manifest = c.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    body = manifest.json()
    assert body["display"] == "standalone"
    assert any(i["sizes"] == "512x512" for i in body["icons"])

    sw = c.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in sw.headers["content-type"]
    assert sw.headers.get("Service-Worker-Allowed") == "/"
    # the worker must never cache dynamic /api content (review freshness)
    assert "/api/" in sw.text

    assert c.get("/icons/icon-192.png").status_code == 200


def test_opentake_status_best_effort(tmp_path):
    """opentake_status reports placement/staleness from disk without a live MCP
    call: no bridge -> not placed; with a bridge from an older revision ->
    plan_advanced, and no false 'changed' when the bundle isn't visible."""
    from video_app.config import Settings
    from video_app.projects import ProjectService

    runtime = tmp_path / "runtime"
    pid = "vlog-x"
    pdir = runtime / pid
    (pdir / "plan").mkdir(parents=True)
    write_json(pdir / "project.json", {"project_id": pid, "name": "Vlog X"})
    write_json(pdir / "plan" / "edit-plan.json", {
        "schema_version": "edit-plan.v1", "revision": 5,
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 10.0, "background_color": "black"},
        "tracks": [],
    })
    svc = ProjectService(Settings(root=PROJECT_ROOT, runtime=runtime))

    before = svc.opentake_status(pid)
    assert before["placed"] is False
    assert before["opentake_changed"] is False

    write_json(pdir / "opentake-bridge.json", {
        "schema_version": "opentake-bridge.v1", "plan_revision": 3,
        "events": [{"event_id": "v01"}, {"event_id": "v02"}],
    })
    after = svc.opentake_status(pid)
    assert after["placed"] is True
    assert after["plan_revision"] == 5
    assert after["bridge_revision"] == 3
    assert after["plan_advanced"] is True
    # no OPENTAKE_PROJECTS_DIR bundle visible in a test -> never a false positive
    assert after["opentake_changed"] is False
    assert after["bundle_visible"] is False
