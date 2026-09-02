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


class TestAnalysisArtifactIdentity:
    def test_same_media_same_computation_is_reused(self, tmp_path, monkeypatch) -> None:
        from pathlib import Path

        from video_app.config import Settings
        from video_app.projects import ProjectService

        service = ProjectService(
            Settings(root=Path(__file__).resolve().parents[2],
                     runtime=tmp_path / "runtime")
        )
        assets = [{"asset_id": "a1", "sha256": "aa" * 32},
                  {"asset_id": "a2", "sha256": "bb" * 32}]
        key = service._analysis_content_key(
            assets, adapter="owned-live-visual",
            model="gemini-x", prompt_version="live-visual-v2-video",
        )
        # stable under asset order; sensitive to media, model, prompt
        assert key == service._analysis_content_key(
            list(reversed(assets)), adapter="owned-live-visual",
            model="gemini-x", prompt_version="live-visual-v2-video",
        )
        assert key != service._analysis_content_key(
            assets[:1], adapter="owned-live-visual",
            model="gemini-x", prompt_version="live-visual-v2-video",
        )
        assert key != service._analysis_content_key(
            assets, adapter="owned-live-visual",
            model="gemini-y", prompt_version="live-visual-v2-video",
        )

        runs = tmp_path / "runtime" / "p1" / "analysis" / "runs" / "gemini-live-x"
        runs.mkdir(parents=True)
        (runs / "manifest.json").write_text(json.dumps(
            {"run_key": "gemini-live-x", "content_key": key}
        ))
        monkeypatch.setattr(
            service, "semantic_run",
            lambda pid, run_key: {"run_key": run_key},
        )
        hit = service._existing_run_for("p1", key)
        assert hit == {"run_key": "gemini-live-x", "cached": True}
        assert service._existing_run_for("p1", "different") is None


class TestRevisionRestore:
    def test_restore_installs_archived_cut_as_new_revision(self, tmp_path) -> None:
        from pathlib import Path

        from video_app.config import Settings
        from video_app.projects import ProjectService

        def full_plan(revision, intent):
            event = {
                "event_id": "v01", "asset_id": None,
                "source_start_seconds": None, "source_end_seconds": None,
                "timeline_start_seconds": 0.0, "duration_seconds": 2.0,
                "playback_rate": 1.0, "intent": intent, "observed_content": None,
                "confidence": 1.0, "reframe": None, "transition_out": None,
                "text": None, "volume_db": None,
            }
            return {
                "schema_version": "edit-plan.v1",
                "generated_at": "2026-09-01T00:00:00Z",
                "benchmark_id": "t", "concept_id": "c", "revision": revision,
                "project": {"width": 320, "height": 240, "fps": 30,
                            "duration_seconds": 2.0,
                            "background_color": "black"},
                "tracks": [
                    {"track_id": "v1", "kind": "video", "events": [dict(event)]},
                    {"track_id": "a1", "kind": "audio", "events": [dict(event)]},
                    {"track_id": "t1", "kind": "title", "events": []},
                ],
            }

        root = tmp_path / "runtime" / "p1"
        (root / "plan" / "revisions").mkdir(parents=True)
        old = full_plan(1, "old")
        current = full_plan(2, "new")
        (root / "plan" / "edit-plan.json").write_text(json.dumps(current))
        (root / "plan" / "revisions" / "edit-plan.rev001.json").write_text(
            json.dumps(old))
        (root / "project.json").write_text(json.dumps({
            "schema_version": "video-app-project.v1", "project_id": "p1",
            "name": "t", "created_at": "x", "updated_at": "x",
            "source_directory": "footage", "prompt": "", "status": "plan_ready",
            "footage_summary": "", "analysis": {}, "inventory": {"assets": []},
            "concepts": [], "selected_concept_id": None, "plan": current,
            "outputs": {},
        }))
        service = ProjectService(Settings(
            root=Path(__file__).resolve().parents[2],
            runtime=tmp_path / "runtime",
        ))
        result = service.plan_restore_revision("p1", 1)
        assert result == {"revision": 3, "restored_from": 1}
        plan = json.loads((root / "plan" / "edit-plan.json").read_text())
        assert plan["tracks"][0]["events"][0]["intent"] == "old"
        assert plan["revision"] == 3
        # the replaced cut was archived, nothing lost
        archived = json.loads(
            (root / "plan" / "revisions" / "edit-plan.rev002.json").read_text())
        assert archived["tracks"][0]["events"][0]["intent"] == "new"

    def test_restore_refuses_current_and_missing(self, tmp_path) -> None:
        import pytest
        from pathlib import Path

        from video_app.config import Settings
        from video_app.projects import ProjectError, ProjectService

        root = tmp_path / "runtime" / "p1"
        (root / "plan").mkdir(parents=True)
        current = {"revision": 2}
        (root / "plan" / "edit-plan.json").write_text(json.dumps(current))
        (root / "project.json").write_text(json.dumps({"project_id": "p1"}))
        service = ProjectService(Settings(
            root=Path(__file__).resolve().parents[2],
            runtime=tmp_path / "runtime",
        ))
        with pytest.raises(ProjectError, match="No archived"):
            service.plan_restore_revision("p1", 7)
