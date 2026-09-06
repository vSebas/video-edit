from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

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


def test_analyze_voiceover_classifies_beats_and_excludes_own_audio(tmp_path, monkeypatch):
    """A0 preflight: transcribes the placed VO, segments beats, classifies each
    against approved footage evidence — with the VO's OWN asset excluded from the
    candidate pool — and persists a raw/provisional voiceover-analysis.v1."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-va"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    (root / "footage" / pid / "vo.m4a").write_bytes(b"fake")

    plan = {
        "schema_version": "edit-plan.v1", "revision": 3, "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"kind": "video", "events": [
                {"event_id": "v01", "asset_id": "clip_cafe",
                 "timeline_start_seconds": 0.0, "duration_seconds": 6.0,
                 "source_start_seconds": 0.0, "source_end_seconds": 6.0,
                 "observed_content": "Coffee poured at a cafe counter."}]},
            {"kind": "audio", "role": "voiceover", "events": [
                {"event_id": "vo-01", "asset_id": "vo_note",
                 "timeline_start_seconds": 0.0, "duration_seconds": 4.0,
                 "source_start_seconds": 0.0, "source_end_seconds": 4.0}]},
        ],
    }
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "VA",
        "plan": plan,
        "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "abc",
             "source_path": f"footage/{pid}/vo.m4a", "duration_seconds": 4.0},
            {"asset_id": "clip_cafe", "media_type": "video",
             "source_path": f"footage/{pid}/cafe.mp4", "duration_seconds": 6.0}]},
    })

    segs = [{"words": [
        {"word": "Tomé", "start_seconds": 0.0, "end_seconds": 0.4},
        {"word": "café.", "start_seconds": 0.4, "end_seconds": 0.9},
        {"word": "Después", "start_seconds": 2.5, "end_seconds": 3.0},
        {"word": "nadé.", "start_seconds": 3.0, "end_seconds": 3.6},
    ]}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "small", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    captured = {}

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[1]["content"]
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "current_match",
                 "evidence_ids": ["ev1"], "rationale": "café shot is shown"},
                {"beat_id": "b002", "class": "gap",
                 "evidence_ids": ["c999"], "rationale": "no swimming footage"}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)

    svc = ProjectService(Settings(root=root, runtime=runtime))
    # Pool includes the VO's OWN audio asset AND a non-visual audio memo — both
    # must be excluded (footage-only, self-exclusion).
    monkeypatch.setattr(svc, "approved_evidence", lambda pid_: [
        {"evidence_id": "ev1", "asset_id": "clip_cafe", "caption": "coffee at a cafe",
         "start_seconds": 0.0, "end_seconds": 5.0, "evidence_type": "visual"},
        {"evidence_id": "evX", "asset_id": "vo_note", "caption": "I went swimming",
         "start_seconds": 0.0, "end_seconds": 4.0, "evidence_type": "speech"},
    ])

    report = svc.analyze_voiceover(pid)

    assert report["schema_version"] == "voiceover-analysis.v1"
    assert report["status"] == "ok"
    assert report["timebase"] == "raw" and report["actionable"] is False
    assert report["basis"]["plan_revision"] == 3
    assert report["coverage_complete"] is True
    assert [b["beat_id"] for b in report["beats"]] == ["b001", "b002"]
    b1, b2 = report["beats"]
    # current_match is COHERENT: ev1 is the approved id on the visible café scene.
    assert b1["text"] == "Tomé café." and b1["class"] == "current_match"
    assert b1["candidates"][0]["evidence_id"] == "ev1"
    assert b1["candidates"][0]["visible"] is True
    assert b1["timeline_start_seconds"] == 0.0
    assert b1["word_ids"]                      # canonical lineage carried
    # gap survives only because the pool is COMPLETE and the invented id dropped.
    assert b2["class"] == "gap" and b2["candidates"] == []
    # the VO's own transcript is nowhere in the model input; footage caption is.
    assert "I went swimming" not in captured["user"]
    assert "I went swimming" not in captured["system"]
    assert "coffee at a cafe" in captured["user"]
    # persisted keyed by event, reloadable, and stamped fresh.
    loaded = svc.load_voiceover_analysis(pid, "vo-01")
    assert loaded["event_id"] == "vo-01" and loaded["stale"] is False


def test_voiceover_cleanup_previews_and_applies_vo_lane_cut(tmp_path, monkeypatch):
    """Phase B: cleanup previews conservative filler/dead-air ranges from the
    fresh A0 transcript, then applies them as one confirm-gated VO-lane cut —
    splitting the voiceover event and marking the A0 report stale (picture
    untouched)."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-clean"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    (root / "footage" / pid / "vo.m4a").write_bytes(b"fake")

    def _ev(event_id, asset_id, src_start, tl_start, dur, intent="scene",
            observed=None):
        return {
            "event_id": event_id, "asset_id": asset_id,
            "source_start_seconds": src_start,
            "source_end_seconds": round(src_start + dur, 6),
            "timeline_start_seconds": tl_start, "duration_seconds": dur,
            "playback_rate": 1.0, "intent": intent,
            "observed_content": observed, "confidence": 0.9,
            "reframe": None, "transition_out": None, "text": None,
            "volume_db": None,
        }

    plan = {
        "schema_version": "edit-plan.v1", "revision": 3,
        "generated_at": "2026-09-06T00:00:00Z",
        "benchmark_id": "t", "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [
                _ev("v01", "clip_cafe", 0.0, 0.0, 6.0,
                    observed="Coffee poured at a cafe counter.")]},
            {"track_id": "a1", "kind": "audio", "events": [
                _ev("a01", "clip_cafe", 0.0, 0.0, 6.0)]},
            {"track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
                _ev("vo-01", "vo_note", 0.0, 2.0, 5.0, intent="voiceover")]},
        ],
    }
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "VA",
        "plan": plan,
        "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "abc",
             "source_path": f"footage/{pid}/vo.m4a", "duration_seconds": 5.0},
            {"asset_id": "clip_cafe", "media_type": "video",
             "source_path": f"footage/{pid}/cafe.mp4", "duration_seconds": 6.0}]},
    })
    # The propose/apply flow reads and rewrites the durable plan file.
    (pdir / "plan").mkdir()
    write_json(pdir / "plan" / "edit-plan.json", plan)

    # "Eh" is a pure filler; the 2.3s gap before "nadé" is dead air.
    segs = [{"words": [
        {"word": "Eh", "start_seconds": 0.0, "end_seconds": 0.2},
        {"word": "tomé", "start_seconds": 0.3, "end_seconds": 0.7},
        {"word": "café.", "start_seconds": 0.7, "end_seconds": 1.1},
        {"word": "nadé.", "start_seconds": 3.4, "end_seconds": 3.9},
    ]}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "small", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "nonvisual",
                 "evidence_ids": [], "rationale": "narration"}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)

    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda pid_: [])

    svc.analyze_voiceover(pid)

    preview = svc.voiceover_cleanup_candidates(pid, "vo-01")
    cands = preview["candidates"]
    reasons = " ".join(c["reason"] for c in cands)
    assert "«eh»" in reasons                       # pure filler flagged
    assert any(c["kind"] == "dead_air" for c in cands)   # long silence flagged
    assert all(c["id"] for c in cands)             # stable ids for by-id selection
    base_rev = preview["plan_revision"]

    # A stale preview revision is refused (the plan moved underneath).
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_cleanup_apply(
            pid, "vo-01", [cands[0]["id"]], base_revision=base_rev + 5)
    # An unrecognised candidate id fails the WHOLE call closed (no subset apply).
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_cleanup_apply(
            pid, "vo-01", [cands[0]["id"], "vc-bogus"], base_revision=base_rev)

    ids = [c["id"] for c in cands]
    svc.voiceover_cleanup_apply(pid, "vo-01", ids, base_revision=base_rev)

    project = svc.get_project(pid)
    vo_track = next(t for t in project["plan"]["tracks"]
                    if t.get("role") == "voiceover")
    # the single VO event is now split into compacted kept segments...
    assert len(vo_track["events"]) >= 2
    # ...and every kept segment keeps the picture-independent VO role/asset.
    assert all(e["asset_id"] == "vo_note" for e in vo_track["events"])
    # the picture track is untouched.
    vid = next(t for t in project["plan"]["tracks"] if t["kind"] == "video")
    assert [e["event_id"] for e in vid["events"]] == ["v01"]
    # the A0 report is now stale (plan revision bumped) — a re-analysis is forced.
    reloaded = svc.load_voiceover_analysis(pid, "vo-01")
    assert reloaded["stale"] is True

    # The cleaned PLAN is the raw->edited map: kept segments carry original-asset
    # source coords in source order, so the removed span is recoverable as the
    # gap between consecutive segments (no drift-prone sidecar).
    segs = sorted(vo_track["events"], key=lambda e: e["source_start_seconds"])
    for lo, hi in zip(segs, segs[1:]):
        assert hi["source_start_seconds"] >= lo["source_end_seconds"]  # a real gap

    # empty selection is refused (never a silent no-op that looks applied).
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_cleanup_apply(pid, "vo-01", [], base_revision=base_rev)


def test_voiceover_remedies_pull_from_pool_and_flag_record(tmp_path, monkeypatch):
    """Phase A1: for a beat whose relevant footage is available_elsewhere, PULL a
    grounded B-roll cutaway from the pool over the beat window (no ripple); for a
    gap beat, flag RECORD (no auto op). Apply is revision-bound and by beat_id."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-a1"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    (root / "footage" / pid / "vo.m4a").write_bytes(b"fake")

    def _ev(event_id, asset_id, src_start, tl_start, dur, intent="scene",
            observed=None):
        return {
            "event_id": event_id, "asset_id": asset_id,
            "source_start_seconds": src_start,
            "source_end_seconds": round(src_start + dur, 6),
            "timeline_start_seconds": tl_start, "duration_seconds": dur,
            "playback_rate": 1.0, "intent": intent,
            "observed_content": observed, "confidence": 0.9,
            "reframe": None, "transition_out": None, "text": None,
            "volume_db": None,
        }

    plan = {
        "schema_version": "edit-plan.v1", "revision": 4,
        "generated_at": "2026-09-06T00:00:00Z",
        "benchmark_id": "t", "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [
                _ev("v01", "clip_intro", 0.0, 0.0, 6.0,
                    observed="Packing a backpack indoors.")]},
            {"track_id": "a1", "kind": "audio", "events": [
                _ev("a01", "clip_intro", 0.0, 0.0, 6.0)]},
            {"track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
                _ev("vo-01", "vo_note", 0.0, 0.0, 2.5, intent="voiceover")]},
        ],
    }
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "A1",
        "plan": plan,
        "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "abc",
             "source_path": f"footage/{pid}/vo.m4a", "duration_seconds": 4.0},
            {"asset_id": "clip_intro", "media_type": "video",
             "source_path": f"footage/{pid}/intro.mp4", "duration_seconds": 6.0},
            {"asset_id": "clip_lake", "media_type": "video",
             "source_path": f"footage/{pid}/lake.mp4", "duration_seconds": 6.0}]},
    })
    (pdir / "plan").mkdir()
    write_json(pdir / "plan" / "edit-plan.json", plan)

    # "Fui al lago." (beat 1, sentence-end split) then "Comí algo." (beat 2). The
    # inter-word gaps stay under the dead-air threshold so the timebase is
    # already clean (no pending cleanup blocking A1).
    segs = [{"words": [
        {"word": "Fui", "start_seconds": 0.0, "end_seconds": 0.4},
        {"word": "al", "start_seconds": 0.4, "end_seconds": 0.6},
        {"word": "lago.", "start_seconds": 0.6, "end_seconds": 1.1},
        {"word": "Comí", "start_seconds": 1.5, "end_seconds": 1.9},
        {"word": "algo.", "start_seconds": 1.9, "end_seconds": 2.4},
    ]}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "small", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            # beat 1: the lake footage exists in the pool but is NOT on screen
            # (available_elsewhere); beat 2: nothing relevant (gap).
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "available_elsewhere",
                 "evidence_ids": ["ev_lake"], "rationale": "lake b-roll unused"},
                {"beat_id": "b002", "class": "gap",
                 "evidence_ids": [], "rationale": "no eating footage"}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)

    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda pid_: [
        {"evidence_id": "ev_intro", "asset_id": "clip_intro",
         "caption": "packing a bag", "start_seconds": 0.0, "end_seconds": 6.0,
         "evidence_type": "visual"},
        {"evidence_id": "ev_lake", "asset_id": "clip_lake",
         "caption": "a calm lake", "start_seconds": 0.0, "end_seconds": 5.0,
         "evidence_type": "visual"},
    ])

    svc.analyze_voiceover(pid)
    out = svc.voiceover_remedies(pid, "vo-01")
    base_rev = out["plan_revision"]
    by_beat = {r["beat_id"]: r for r in out["remedies"]}
    pull = by_beat["b001"]
    assert pull["tier"] == "pull" and pull["asset_id"] == "clip_lake"
    assert pull["op"]["op"] == "add_broll" and pull["remedy_id"]
    assert by_beat["b002"]["tier"] == "record" and by_beat["b002"]["op"] is None
    assert by_beat["b002"]["remedy_id"] is None

    # a stale preview revision is refused
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_remedy_apply(pid, "vo-01", pull["remedy_id"],
                                   base_revision=base_rev + 9)
    # an unknown / content-changed remedy id is refused (fail closed)
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_remedy_apply(pid, "vo-01", "vr-bogus", base_revision=base_rev)

    # the pull remedy overlays the lake footage as a NON-rippling B-roll cutaway
    svc.voiceover_remedy_apply(pid, "vo-01", pull["remedy_id"], base_revision=base_rev)
    project = svc.get_project(pid)
    broll = [e for t in project["plan"]["tracks"]
             if t.get("kind") == "video" and t.get("role") == "broll"
             for e in t.get("events", [])]
    assert any(e["asset_id"] == "clip_lake" for e in broll)
    # the primary picture and voiceover are untouched (no ripple)
    vid = next(t for t in project["plan"]["tracks"]
               if t.get("kind") == "video" and t.get("role") in (None, "", "primary"))
    assert [e["event_id"] for e in vid["events"]] == ["v01"]


def _retime_project(tmp_path, pid, vo_duration, clip_source_available,
                    monkeypatch):
    """A minimal mirrored plan: one 6s primary clip + a voiceover of the given
    length, plus a FRESH clean A0 report (Phase C now runs only on a frozen,
    reviewed timebase)."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / pid
    runtime = root / "runtime"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage").mkdir(parents=True, exist_ok=True)
    (root / "footage" / "vo.m4a").write_bytes(b"fake")

    def _ev(event_id, asset_id, dur, intent="scene"):
        return {
            "event_id": event_id, "asset_id": asset_id,
            "source_start_seconds": 0.0, "source_end_seconds": dur,
            "timeline_start_seconds": 0.0, "duration_seconds": dur,
            "playback_rate": 1.0, "intent": intent, "observed_content": None,
            "confidence": 0.9, "reframe": None, "transition_out": None,
            "text": None, "volume_db": None,
        }

    plan = {
        "schema_version": "edit-plan.v1", "revision": 2,
        "generated_at": "2026-09-06T00:00:00Z",
        "benchmark_id": "t", "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [_ev("v01", "clip", 6.0)]},
            {"track_id": "a1", "kind": "audio", "events": [_ev("a01", "clip", 6.0)]},
            {"track_id": "vo1", "kind": "audio", "role": "voiceover",
             "events": [_ev("vo-01", "vo_note", vo_duration, intent="voiceover")]},
        ],
    }
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "C",
        "plan": plan,
        "inventory": {"assets": [
            {"asset_id": "clip", "media_type": "video",
             "source_path": "footage/clip.mp4",
             "duration_seconds": clip_source_available},
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "vosha",
             "source_path": "footage/vo.m4a", "duration_seconds": vo_duration}]},
    })
    (pdir / "plan").mkdir()
    write_json(pdir / "plan" / "edit-plan.json", plan)
    svc = ProjectService(Settings(root=root, runtime=runtime))

    # A fresh, clean report: speech fills the whole VO window (no long leading or
    # trailing silence, gaps under the dead-air threshold), so no pending cleanup.
    ws = []
    t = 0.0
    while t < vo_duration - 0.35:
        ws.append({"word": "palabra", "start_seconds": round(t, 3),
                   "end_seconds": round(min(t + 0.4, vo_duration), 3)})
        t += 0.7
    segs = [{"words": ws}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "s", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    class _FC:
        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "nonvisual", "evidence_ids": []}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", _FC)
    monkeypatch.setattr(svc, "approved_evidence", lambda pid_: [])
    svc.analyze_voiceover(pid, "vo-01")
    return svc


def test_voiceover_retime_trims_picture_to_voiceover(tmp_path, monkeypatch):
    """Phase C: a picture longer than the (post-cleanup) voiceover is trimmed at
    the tail to end with it — frame-exact, and the project duration follows."""
    from video_app import projects as projects_mod

    svc = _retime_project(tmp_path, "vlog-ctrim", vo_duration=4.0,
                          clip_source_available=10.0, monkeypatch=monkeypatch)
    preview = svc.voiceover_retime_preview("vlog-ctrim")
    c = preview["candidate"]
    assert c["action"] == "trim" and c["feasible"] is True
    assert c["target_end_seconds"] == 4.0 and c["delta_seconds"] == -2.0

    # a moved plan is refused
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_retime_apply("vlog-ctrim", base_revision=999)

    svc.voiceover_retime_apply("vlog-ctrim", base_revision=preview["plan_revision"])
    plan = svc.get_project("vlog-ctrim")["plan"]
    vid = next(t for t in plan["tracks"]
               if t["kind"] == "video" and t.get("role") in (None, "", "primary"))
    assert vid["events"][0]["duration_seconds"] == 4.0        # tail trimmed
    assert vid["events"][0]["source_end_seconds"] == 4.0
    assert plan["project"]["duration_seconds"] == 4.0         # canvas follows


def test_voiceover_retime_extends_only_into_available_footage(tmp_path, monkeypatch):
    """Phase C: a picture shorter than the voiceover extends the tail into real
    source; with no source to grow into it is reported infeasible, never invented."""
    # voiceover 8s, picture 6s, clip has 10s of source -> feasible extend
    svc = _retime_project(tmp_path, "vlog-cext", vo_duration=8.0,
                          clip_source_available=10.0, monkeypatch=monkeypatch)
    c = svc.voiceover_retime_preview("vlog-cext")["candidate"]
    assert c["action"] == "extend" and c["feasible"] is True and c["delta_seconds"] == 2.0

    # voiceover 8s, picture 6s, but the clip only HAS 6s of source -> infeasible
    svc2 = _retime_project(tmp_path, "vlog-cext2", vo_duration=8.0,
                           clip_source_available=6.0, monkeypatch=monkeypatch)
    c2 = svc2.voiceover_retime_preview("vlog-cext2")["candidate"]
    assert c2["action"] == "extend" and c2["feasible"] is False
    assert "metraje" in c2["reason"]


def test_voiceover_remedies_refuse_until_cleanup_is_done(tmp_path, monkeypatch):
    """A1 must refuse while the voiceover still has filler/dead-air (an unfrozen
    timebase) — the beat windows would shift under cleanup."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"; runtime = root / "runtime"; pid = "vlog-pend"
    (runtime / pid).mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    (root / "footage" / pid / "vo.m4a").write_bytes(b"x")

    def _ev(i, a, s0, tl, d, intent="scene"):
        return {"event_id": i, "asset_id": a, "source_start_seconds": s0,
                "source_end_seconds": round(s0 + d, 6), "timeline_start_seconds": tl,
                "duration_seconds": d, "playback_rate": 1.0, "intent": intent,
                "observed_content": None, "confidence": 0.9, "reframe": None,
                "transition_out": None, "text": None, "volume_db": None}

    plan = {"schema_version": "edit-plan.v1", "revision": 2,
            "generated_at": "2026-09-06T00:00:00Z", "benchmark_id": "t",
            "concept_id": "c1", "project": {"width": 1080, "height": 1920, "fps": 30,
            "duration_seconds": 12.0, "background_color": "black"}, "tracks": [
                {"track_id": "v1", "kind": "video", "events": [_ev("v01", "clip", 0.0, 0.0, 6.0)]},
                {"track_id": "a1", "kind": "audio", "events": [_ev("a01", "clip", 0.0, 0.0, 6.0)]},
                {"track_id": "vo1", "kind": "audio", "role": "voiceover",
                 "events": [_ev("vo-01", "vo_note", 0.0, 0.0, 4.0, "voiceover")]}]}
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "P",
        "plan": plan, "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "s",
             "source_path": f"footage/{pid}/vo.m4a", "duration_seconds": 4.0},
            {"asset_id": "clip", "media_type": "video",
             "source_path": f"footage/{pid}/c.mp4", "duration_seconds": 6.0}]}})
    (runtime / pid / "plan").mkdir()
    write_json(runtime / pid / "plan" / "edit-plan.json", plan)

    # a pure filler ("eh") -> pending_cleanup true
    segs = [{"words": [
        {"word": "eh", "start_seconds": 0.0, "end_seconds": 0.2},
        {"word": "fui.", "start_seconds": 0.3, "end_seconds": 0.8}]}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "s", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    class FC:
        def __init__(self, c):
            pass

        def chat(self, m, **k):
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "gap", "evidence_ids": []}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FC)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda p: [])

    rep = svc.analyze_voiceover(pid, "vo-01")
    assert rep["pending_cleanup"] is True
    with pytest.raises(projects_mod.ProjectError, match="[Ll]impia"):
        svc.voiceover_remedies(pid, "vo-01")
    # C is gated on the same frozen timebase
    with pytest.raises(projects_mod.ProjectError, match="[Ll]impia"):
        svc.voiceover_retime_preview(pid)


def test_analyze_voiceover_covers_all_segments_of_a_split_group(tmp_path, monkeypatch):
    """After Phase B splits a voiceover, A0 must analyze the WHOLE logical group
    (every segment of the recording), not just the anchor — or beats in later
    segments silently vanish and A1 can never cover them."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-grp"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    (root / "footage" / pid / "vo.m4a").write_bytes(b"fake")

    def _ev(event_id, asset_id, s0, tl, dur, intent="scene"):
        return {"event_id": event_id, "asset_id": asset_id,
                "source_start_seconds": s0, "source_end_seconds": round(s0 + dur, 6),
                "timeline_start_seconds": tl, "duration_seconds": dur,
                "playback_rate": 1.0, "intent": intent, "observed_content": None,
                "confidence": 0.9, "reframe": None, "transition_out": None,
                "text": None, "volume_db": None}

    # A voiceover split into two segments: source 0-2 @ tl 0, source 4-8 @ tl 2
    # (cleanup removed source 2-4 and compacted).
    plan = {
        "schema_version": "edit-plan.v1", "revision": 5,
        "generated_at": "2026-09-06T00:00:00Z",
        "benchmark_id": "t", "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": [_ev("v01", "clip", 0.0, 0.0, 6.0)]},
            {"track_id": "a1", "kind": "audio", "events": [_ev("a01", "clip", 0.0, 0.0, 6.0)]},
            {"track_id": "vo1", "kind": "audio", "role": "voiceover", "events": [
                _ev("vo-01", "vo_note", 0.0, 0.0, 2.0, intent="voiceover"),
                _ev("vo-02", "vo_note", 4.0, 2.0, 4.0, intent="voiceover")]},
        ],
    }
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "G",
        "plan": plan,
        "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "abc",
             "source_path": f"footage/{pid}/vo.m4a", "duration_seconds": 8.0},
            {"asset_id": "clip", "media_type": "video",
             "source_path": f"footage/{pid}/clip.mp4", "duration_seconds": 6.0}]},
    })

    # words across the WHOLE recording; the 2-4s ones fall in the removed gap and
    # belong to no segment.
    segs = [{"words": [
        {"word": "Uno", "start_seconds": 0.0, "end_seconds": 0.4},
        {"word": "dos.", "start_seconds": 0.4, "end_seconds": 1.0},
        {"word": "eh", "start_seconds": 2.5, "end_seconds": 2.9},      # removed gap
        {"word": "Cuatro", "start_seconds": 4.0, "end_seconds": 4.5},
        {"word": "cinco.", "start_seconds": 4.5, "end_seconds": 5.2},
    ]}]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "small", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: (segs, {}))

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "nonvisual", "evidence_ids": []},
                {"beat_id": "b002", "class": "nonvisual", "evidence_ids": []}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda pid_: [])

    report = svc.analyze_voiceover(pid, "vo-01")
    texts = [b["text"] for b in report["beats"]]
    assert "Uno dos." in texts and "Cuatro cinco." in texts   # BOTH segments
    # the second segment's beat is mapped onto the timeline via its OWN offset
    second = next(b for b in report["beats"] if b["text"] == "Cuatro cinco.")
    assert abs(second["timeline_start_seconds"] - 2.0) < 0.05
    # the removed-gap word never leaks into a beat
    assert "eh" not in " ".join(texts)


def test_voiceover_retime_refuses_when_broll_trails_past_the_voiceover(tmp_path, monkeypatch):
    """Phase C refuses when a B-roll cutaway trails past the voiceover end (it
    would float past the picture) — music/titles are reconciled, but a trailing
    scene/cutaway is a real conflict the single-clip resize cannot fix."""
    from video_app import projects as projects_mod

    svc = _retime_project(tmp_path, "vlog-ctrail", vo_duration=4.0,
                          clip_source_available=10.0, monkeypatch=monkeypatch)
    # a B-roll overlay that runs to 8s, well past the 4s voiceover
    pdir = svc.settings.runtime / "vlog-ctrail"
    for path in (pdir / "project.json", pdir / "plan" / "edit-plan.json"):
        doc = json.loads(path.read_text())
        plan = doc.get("plan", doc)
        plan["tracks"].append({
            "track_id": "b1", "kind": "video", "role": "broll", "events": [{
                "event_id": "bro-01", "asset_id": "clip",
                "source_start_seconds": 0.0, "source_end_seconds": 8.0,
                "timeline_start_seconds": 0.0, "duration_seconds": 8.0,
                "playback_rate": 1.0, "intent": "b-roll", "observed_content": None,
                "confidence": 1.0, "reframe": None, "transition_out": None,
                "text": None, "volume_db": None}]})
        path.write_text(json.dumps(doc))

    c = svc.voiceover_retime_preview("vlog-ctrail")["candidate"]
    assert c["action"] == "trim" and c["feasible"] is False   # refused, not floating
    with pytest.raises(projects_mod.ProjectError):
        svc.voiceover_retime_apply("vlog-ctrail", base_revision=2)


def test_place_voiceover_from_drive_validates_and_places(tmp_path, monkeypatch):
    """Loading a voice file from Drive copies exactly ONE listed audio file into
    the project and routes it through the normal placement — and refuses any path
    the listing didn't return (no traversal / arbitrary Drive paths)."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-drive"
    (runtime / pid).mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "D",
        "source_directory": f"footage/{pid}", "plan": {}, "inventory": {"assets": []},
    })
    svc = ProjectService(Settings(root=root, runtime=runtime))

    monkeypatch.setattr(svc, "drive_voice_files", lambda: [
        {"path": "abril/nota-voz.m4a", "name": "nota-voz.m4a",
         "bytes": 12345, "modified": "2026-09-06T00:00:00Z"}])

    placed = {}

    def fake_place(project_id, source_path, start, cap):
        placed.update(project_id=project_id, source_path=source_path,
                      start=start, cap=cap)
        return {"status": "plan_ready", "revision": 2}

    def fake_run(cmd, **kwargs):
        # rclone copyto <remote> <dest> — create the destination file
        if cmd[:2] == ["rclone", "copyto"]:
            Path(cmd[3]).write_bytes(b"voice")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(svc, "place_voiceover", fake_place)
    monkeypatch.setattr(projects_mod.subprocess, "run", fake_run)

    # a path the listing did not return is refused
    with pytest.raises(projects_mod.ProjectError):
        svc.place_voiceover_from_drive(pid, "../secret.m4a", 3.0, 1.0)

    svc.place_voiceover_from_drive(pid, "abril/nota-voz.m4a", 3.0, 1.5)
    assert placed["source_path"] == f"footage/{pid}/nota-voz.m4a"
    assert placed["start"] == 3.0 and placed["cap"] == 1.5
    assert (root / "footage" / pid / "nota-voz.m4a").is_file()


def _chat_fixture(tmp_path):
    """A project with an approved plan (primary video track + a B-roll track we
    assert is EXCLUDED from the grounded scene map) and a concept."""
    runtime = tmp_path / "runtime"
    pid = "vlog-vo"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    plan = {
        "schema_version": "edit-plan.v1", "revision": 2, "concept_id": "c1",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_seconds": 12.0, "background_color": "black"},
        "tracks": [
            {"kind": "video", "events": [
                {"event_id": "v01", "timeline_start_seconds": 0.0,
                 "duration_seconds": 6.0, "intent": "Start the day.",
                 "observed_content": "A man puts on a maroon cap and looks at the camera."},
                {"event_id": "v02", "timeline_start_seconds": 6.0,
                 "duration_seconds": 6.0, "intent": "Grab coffee.",
                 "observed_content": "Coffee being poured at a cafe counter."},
            ]},
            # B-roll is a video track WITH a role — it must not be picked as the
            # scene map (the role-based-selection lesson).
            {"kind": "video", "role": "broll", "events": [
                {"event_id": "bro-1", "timeline_start_seconds": 2.0,
                 "duration_seconds": 1.0, "observed_content": "BROLL_MARKER cutaway"}]},
        ],
    }
    concepts = [{
        "concept_id": "c1", "title": "Robot day",
        "editorial": {"tone": ["energetic"]}, "hook": "Opens on the cap.",
        "missing_shots": [{"purpose": "Voiceover narration to tie it together.",
                           "recording_instruction": "Record a 60s casual voiceover."}],
    }]
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "VO",
        "plan": plan, "concepts": concepts, "selected_concept_id": "c1",
    })
    return runtime, pid


def test_chat_reply_is_grounded_and_persists(tmp_path, monkeypatch):
    """A reply-kind turn: the model gets a grounded brief (scene map + concept +
    narration recommendation, with B-roll EXCLUDED), the reply persists, and a
    voiceover_draft rides along for the grabar-y-colocar shortcut."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    runtime, pid = _chat_fixture(tmp_path)
    captured: dict = {}
    canned = {"content": ""}

    class FakeClient:
        def __init__(self, config):  # noqa: D401 - stub
            pass

        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            return {"content": canned["content"]}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)
    svc = ProjectService(Settings(root=PROJECT_ROOT, runtime=runtime))

    canned["content"] = json.dumps({
        "kind": "reply", "text": "🎙️ [0:00–0:06] «Hoy arranca el día.»",
        "voiceover_draft": {"start_seconds": 0.0, "end_seconds": 6.0,
                            "text": "Hoy arranca el día."},
    })
    result = svc.chat_send(pid, "¿qué voz en off le pongo al inicio?")

    system = captured["messages"][0]["content"]
    assert captured["messages"][0]["role"] == "system"
    assert "MAPA DE ESCENAS" in system
    assert "maroon cap" in system            # grounded in real observed_content
    assert "Robot day" in system             # concept title
    assert "RECOMENDACIÓN DE NARRACIÓN" in system
    assert "BROLL_MARKER" not in system      # B-roll excluded from the scene map
    assert captured["messages"][-1] == {
        "role": "user", "content": "¿qué voz en off le pongo al inicio?"}

    assert result["message"]["content"].startswith("🎙️")
    assert result["message"]["voiceover_draft"]["end_seconds"] == 6.0
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]

    # Second turn carries the prior thread to the model as history.
    canned["content"] = json.dumps({"kind": "reply", "text": "vale, más corta"})
    svc.chat_send(pid, "hazla más corta")
    assert [m["role"] for m in captured["messages"]] == \
        ["system", "user", "assistant", "user"]

    reloaded = svc.load_chat(pid)
    assert len(reloaded) == 4
    assert svc.clear_chat(pid) == []
    assert svc.load_chat(pid) == []


def test_chat_edit_routes_through_confirm_gate(tmp_path, monkeypatch):
    """An edit-kind turn routes through the reviewed propose path and lands as a
    confirm-gated proposal message; chat_apply flips it applied and appends a
    note — the chat never mutates the plan itself."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    runtime, pid = _chat_fixture(tmp_path)
    canned = {"content": ""}

    class FakeClient:
        def __init__(self, config):  # noqa: D401 - stub
            pass

        def chat(self, messages, **kwargs):
            return {"content": canned["content"]}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", FakeClient)
    svc = ProjectService(Settings(root=PROJECT_ROOT, runtime=runtime))

    # Isolate routing from the op machinery / schema validation.
    proposed_calls: list = []
    monkeypatch.setattr(svc, "plan_command_propose", lambda pid_, instr, **k: (
        proposed_calls.append(instr) or {
            "status": "proposed", "summary": "eliminar la escena de 0:06",
            "proposal_id": "p1", "revision_preview": 3}))

    canned["content"] = json.dumps({
        "kind": "edit", "instruction": "elimina la escena de 0:06",
        "text": "Elimino esa escena."})
    r = svc.chat_send(pid, "quita la del café")

    assert proposed_calls == ["elimina la escena de 0:06"]  # explicit instruction routed
    assert r["proposal"]["proposal_id"] == "p1"
    proposal_msg = r["messages"][-1]
    assert proposal_msg["kind"] == "proposal"
    assert proposal_msg["applied"] is False
    assert proposal_msg["proposal_id"] == "p1"

    # A rejected instruction becomes a plain explanatory reply, not a card.
    monkeypatch.setattr(svc, "plan_command_propose", lambda pid_, instr, **k: {
        "status": "rejected", "reason": "instrucción ambigua"})
    canned["content"] = json.dumps({"kind": "edit", "instruction": "haz magia"})
    r2 = svc.chat_send(pid, "haz magia")
    assert "proposal" not in r2
    assert "No pude hacer ese cambio" in r2["message"]["content"]

    # chat_apply delegates to the revision-guarded apply, then records it.
    monkeypatch.setattr(svc, "plan_command_apply", lambda pid_, pxid: {
        "revision": 3, "summary": "eliminar la escena de 0:06", "status": "plan_ready"})
    applied = svc.chat_apply(pid, "p1")
    assert applied["revision"] == 3
    assert applied["messages"][-1]["kind"] == "applied"
    prop = [m for m in applied["messages"] if m.get("kind") == "proposal"][0]
    assert prop["applied"] is True


def test_model_prefs_persist_and_resolve(tmp_path, monkeypatch):
    """available_models flags per-key availability; a stored pref is validated,
    persisted, and honored by the stage resolver (explicit arg still wins)."""
    import pytest

    from video_app.config import Settings
    from video_app.projects import ProjectError, ProjectService

    runtime, pid = _chat_fixture(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)   # no OpenAI key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)   # no Gemini key
    svc = ProjectService(Settings(root=PROJECT_ROOT, runtime=runtime))

    stages = svc.available_models()["stages"]
    concepts = stages["concepts"]
    assert concepts["default"] == {"provider": "qwen", "model": "deepseek-v4-pro"}
    qwen = next(o for o in concepts["options"]
                if o["provider"] == "qwen" and o["model"] == "deepseek-v4-pro")
    assert qwen["available"] is True
    openai_opt = next(o for o in concepts["options"] if o["provider"] == "openai")
    assert openai_opt["available"] is False   # key absent → shown unavailable
    gem = next(o for o in stages["visual"]["options"] if o["provider"] == "gemini")
    assert gem["hears_audio"] is True

    # Default resolution when nothing chosen.
    assert svc._resolve_stage_model(pid, "concepts", None, None) == \
        ("qwen", "deepseek-v4-pro")
    # A model without a provider is honored on the resolved provider (regression).
    assert svc._resolve_stage_model(pid, "concepts", None, "custom-x") == \
        ("qwen", "custom-x")
    # An off-menu model is refused (a stored pref can't point at the unknown).
    with pytest.raises(ProjectError):
        svc.set_model_pref(pid, "concepts", "qwen", "not-a-model")
    # A provider whose key is not configured is refused (not just UI-disabled).
    with pytest.raises(ProjectError):
        svc.set_model_pref(pid, "concepts", "openai", "gpt-5.6-sol")
    # A valid, key-backed choice persists and is honored.
    svc.set_model_pref(pid, "concepts", "qwen", "qwen3.7-plus")
    assert svc._resolve_stage_model(pid, "concepts", None, None) == \
        ("qwen", "qwen3.7-plus")
    # An explicit caller argument still wins over the stored pref.
    assert svc._resolve_stage_model(pid, "concepts", "openai", "m") == ("openai", "m")


def test_bundle_clip_fingerprint_detects_same_count_edits():
    """A structural fingerprint changes on a trim/reorder even when the clip
    COUNT is unchanged (which the count-only check missed) — Codex review."""
    from video_app.opentake_bridge import bundle_clip_fingerprint

    # Use OpenTake's REAL camelCase clip keys (clip.rs, rename_all=camelCase).
    def bundle(a_dur, b_dur, order=("a", "b")):
        clips = {"a": {"mediaRef": "a.mp4", "startFrame": 0, "durationFrames": a_dur},
                 "b": {"mediaRef": "b.mp4", "startFrame": a_dur, "durationFrames": b_dur}}
        return {"timeline": {"tracks": [
            {"type": "video", "clips": [clips[k] for k in order]}]}}

    base = bundle_clip_fingerprint(bundle(150, 150))
    assert bundle_clip_fingerprint(bundle(150, 150)) == base          # stable
    assert bundle_clip_fingerprint(bundle(90, 150)) != base           # trim (durationFrames)
    assert bundle_clip_fingerprint(bundle(150, 150, ("b", "a"))) != base  # reorder
    # A volume change (same counts/positions) must also flip the hash.
    vol = {"timeline": {"tracks": [{"type": "video", "clips": [
        {"mediaRef": "a.mp4", "startFrame": 0, "durationFrames": 150, "volume": 0.5}]}]}}
    assert bundle_clip_fingerprint(vol) != bundle_clip_fingerprint(
        {"timeline": {"tracks": [{"type": "video", "clips": [
            {"mediaRef": "a.mp4", "startFrame": 0, "durationFrames": 150}]}]}})


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
