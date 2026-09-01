"""P6: durable jobs and the render artifact cache."""

import json
import threading
import time

from video_app.jobs import JobManager


class TestDurableJobs:
    def test_history_survives_restart_and_marks_interrupted(self, tmp_path) -> None:
        store = tmp_path / "jobs.json"
        manager = JobManager(store=store)
        done = manager.submit("render", "p1", lambda: "ok")
        for _ in range(100):
            if manager.get(done["job_id"])["status"] == "completed":
                break
            time.sleep(0.02)
        # a job still running when the process dies…
        gate = threading.Event()
        stuck = manager.submit("exports", "p1", lambda: gate.wait(5))
        for _ in range(100):
            if manager.get(stuck["job_id"])["status"] == "running":
                break
            time.sleep(0.02)

        reborn = JobManager(store=store)  # …reloads as interrupted
        gate.set()
        jobs = {j["job_id"]: j for j in reborn.list()}
        assert jobs[done["job_id"]]["status"] == "completed"
        assert jobs[done["job_id"]]["result"] == "ok"
        assert jobs[stuck["job_id"]]["status"] == "interrupted"
        assert "restarted" in jobs[stuck["job_id"]]["error"]

    def test_duplicate_active_job_is_not_stacked(self, tmp_path) -> None:
        manager = JobManager(store=tmp_path / "jobs.json")
        gate = threading.Event()
        first = manager.submit("render", "p1", lambda: gate.wait(5))
        second = manager.submit("render", "p1", lambda: "should not run")
        other = manager.submit("render", "p2", lambda: "fine")
        gate.set()
        assert second["job_id"] == first["job_id"]
        assert second["already_running"] is True
        assert other["job_id"] != first["job_id"]

    def test_corrupt_store_is_ignored(self, tmp_path) -> None:
        store = tmp_path / "jobs.json"
        store.write_text("{not json")
        manager = JobManager(store=store)
        assert manager.list() == []


class TestRenderCache:
    def test_identical_plan_skips_the_render(self, tmp_path, monkeypatch) -> None:
        import video_app.projects as projects_module
        from video_app.config import Settings
        from video_app.projects import ProjectService

        from pathlib import Path
        root = tmp_path / "runtime" / "cache-test"
        (root / "plan").mkdir(parents=True)
        plan = {"schema_version": "edit-plan.v1", "revision": 1,
                "concept_id": "c", "project": {}, "tracks": []}
        (root / "plan" / "edit-plan.json").write_text(json.dumps(plan))
        (root / "project.json").write_text(json.dumps({
            "schema_version": "video-app-project.v1",
            "project_id": "cache-test", "name": "t",
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z",
            "source_directory": "footage", "prompt": "",
            "status": "plan_ready", "footage_summary": "", "analysis": {},
            "inventory": {"assets": []}, "concepts": [],
            "selected_concept_id": None, "plan": plan, "outputs": {},
        }))
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            Path(command[command.index("--output") + 1]).write_bytes(b"mp4")

            class Result:
                returncode = 0
                stdout = stderr = ""
            return Result()

        monkeypatch.setattr(projects_module.subprocess, "run", fake_run)
        service = ProjectService(
            Settings(root=Path(__file__).resolve().parents[2],
                     runtime=tmp_path / "runtime")
        )
        monkeypatch.setattr(
            service, "_selection", lambda pid: {"concept_id": "c"}
        )
        first = service.render("cache-test")
        assert "cached" not in first and len(calls) == 1
        second = service.render("cache-test")
        assert second["cached"] is True and len(calls) == 1
        # a plan change invalidates the cache
        plan["revision"] = 2
        (root / "plan" / "edit-plan.json").write_text(json.dumps(plan))
        state = json.loads((root / "project.json").read_text())
        state["plan"] = plan
        (root / "project.json").write_text(json.dumps(state))
        third = service.render("cache-test")
        assert "cached" not in third and len(calls) == 2
        # captions need a transcript — actionable refusal, not a render
        import pytest
        from video_app.projects import ProjectError
        with pytest.raises(ProjectError, match="speech analysis"):
            service.render("cache-test", burn_captions=True)
