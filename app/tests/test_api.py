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

    # An explicit human "use as-is" freeze unlocks A1/C on this exact recording.
    svc.voiceover_freeze(pid, "vo-01")
    svc.voiceover_remedies(pid, "vo-01")          # no longer raises
    svc.voiceover_retime_preview(pid)             # no longer raises


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


def _contract_retime_project(tmp_path, pid, env_end, monkeypatch):
    """A CONTRACT plan (lineage_contract) with a 6s clip + 10s voiceover, and E1
    approved over clip [0, env_end] — for exercising the retime coverage gate."""
    import video_app.speech as speech_mod
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / pid
    runtime = root / "runtime"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    (root / "footage").mkdir()
    (root / "footage" / "vo.m4a").write_bytes(b"x")

    def _ev(eid, aid, s0, tl, d, intent="scene", eids=None):
        e = {"event_id": eid, "asset_id": aid, "source_start_seconds": s0,
             "source_end_seconds": round(s0 + d, 6), "timeline_start_seconds": tl,
             "duration_seconds": d, "playback_rate": 1.0, "intent": intent,
             "observed_content": None, "confidence": 0.9, "reframe": None,
             "transition_out": None, "text": None, "volume_db": None}
        if eids is not None:
            e["evidence_ids"] = eids
        return e

    plan = {"schema_version": "edit-plan.v1", "revision": 2,
            "generated_at": "2026-09-06T00:00:00Z", "benchmark_id": "t",
            "concept_id": "c1", "lineage_contract": True,
            "project": {"width": 1080, "height": 1920, "fps": 30,
                        "duration_seconds": 12.0, "background_color": "black"},
            "tracks": [
                {"track_id": "v1", "kind": "video",
                 "events": [_ev("v01", "clip", 0.0, 0.0, 6.0, eids=["E1"])]},
                {"track_id": "a1", "kind": "audio",
                 "events": [_ev("a01", "clip", 0.0, 0.0, 6.0)]},
                {"track_id": "vo1", "kind": "audio", "role": "voiceover",
                 "events": [_ev("vo-01", "vo_note", 0.0, 0.0, 10.0, "voiceover")]}]}
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "X",
        "plan": plan, "inventory": {"assets": [
            {"asset_id": "vo_note", "media_type": "audio", "sha256": "s",
             "source_path": "footage/vo.m4a", "duration_seconds": 10.0},
            {"asset_id": "clip", "media_type": "video",
             "source_path": "footage/c.mp4", "duration_seconds": 12.0}]}})
    (pdir / "plan").mkdir()
    write_json(pdir / "plan" / "edit-plan.json", plan)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    ws = [{"word": "w", "start_seconds": round(t * 0.7, 3),
           "end_seconds": round(t * 0.7 + 0.4, 3)} for t in range(14)]
    monkeypatch.setattr(speech_mod, "_load_model", lambda size: (object(), "s", "cpu"))
    monkeypatch.setattr(speech_mod, "transcribe_asset", lambda m, p: ([{"words": ws}], {}))

    class _FC:
        def __init__(self, c):
            pass

        def chat(self, m, **k):
            return {"content": json.dumps({"beats": [
                {"beat_id": "b001", "class": "nonvisual", "evidence_ids": []}]})}

    monkeypatch.setattr(projects_mod, "resolve_provider", lambda *a, **k: None)
    monkeypatch.setattr(projects_mod, "ChatClient", _FC)
    monkeypatch.setattr(svc, "approved_evidence", lambda p: [])
    monkeypatch.setattr(svc, "_evidence_review_sets", lambda p: {
        "approved": {"E1": "cap"}, "rejected": set(), "pending": set(),
        "envelopes": {"E1": ("clip", 0.0, env_end)}})
    svc.analyze_voiceover(pid, "vo-01")
    return svc


def test_contract_retime_requires_full_coverage_of_new_span(tmp_path, monkeypatch):
    """Codex counterexample: evidence over only [0,6] must NOT authorize extending
    a [0,6] clip to [0,10] (60% overall coverage would let 4 unobserved seconds
    render). Full coverage allows and applies the extension."""
    # A) uncovered [6,10] -> infeasible, refused as unverified footage
    svcA = _contract_retime_project(tmp_path, "ctrA", env_end=6.0,
                                    monkeypatch=monkeypatch)
    cA = svcA.voiceover_retime_preview("ctrA")["candidate"]
    assert cA["action"] == "extend" and cA["feasible"] is False
    assert "evidencia" in cA["reason"]

    # B) fully covered -> feasible with E1 stamped, and applies end-to-end
    svcB = _contract_retime_project(tmp_path, "ctrB", env_end=10.5,
                                    monkeypatch=monkeypatch)
    cB = svcB.voiceover_retime_preview("ctrB")["candidate"]
    assert cB["feasible"] is True
    assert "E1" in (cB["op"].get("evidence_ids") or [])
    svcB.voiceover_retime_apply("ctrB", base_revision=2)
    plan = svcB.get_project("ctrB")["plan"]
    vid = next(t for t in plan["tracks"]
               if t["kind"] == "video" and t.get("role") in (None, "", "primary"))
    assert vid["events"][0]["source_end_seconds"] == 10.0
    assert plan["project"]["duration_seconds"] == 10.0


def test_visual_analysis_is_incremental_for_new_clips(tmp_path, monkeypatch):
    """A grown project analyzes ONLY the uncovered clips; prior observations and
    their review decisions (human ones included) carry into the new run with
    stable evidence ids — so the chat/planner see old + new footage together
    without paying to re-analyze everything (user ask 2026-09-07)."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-inc"
    pdir = runtime / pid
    pdir.mkdir(parents=True)

    def asset(aid, status="analyzed"):
        return {"asset_id": aid, "media_type": "video", "filename": f"{aid}.mp4",
                "source_path": f"footage/{pid}/{aid}.mp4", "sha256": aid,
                "duration_seconds": 5.0, "analysis_status": status}

    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "I", "status": "ready", "created_at": "x", "updated_at": "x",
        "analysis": {}, "plan": {},
        "inventory": {"assets": [asset("old1"), asset("old2"),
                                 asset("nuevo", status="technical_only")]}})

    # A prior visual run covering old1+old2, with a HUMAN review on one obs.
    prior = pdir / "analysis" / "runs" / "gemini-live-prior"
    (prior / "raw").mkdir(parents=True)
    prior_obs = [
        {"evidence_id": "ev-old1", "asset_id": "old1", "caption": "cafe",
         "normalization_status": "accepted", "review_status": "pending",
         "risk_flags": [], "model_confidence": 0.9, "evidence_type": "visual",
         "start_seconds": 0.0, "end_seconds": 5.0},
        {"evidence_id": "ev-old2", "asset_id": "old2", "caption": "park",
         "normalization_status": "accepted", "review_status": "pending",
         "risk_flags": [], "model_confidence": 0.9, "evidence_type": "visual",
         "start_seconds": 0.0, "end_seconds": 5.0},
    ]
    write_json(prior / "normalized.json", {
        "schema_version": "semantic-evidence.v1", "generated_at": "2026-09-01",
        "project_id": pid, "run_id": "prior",
        "provider": {"adapter": "owned-live-visual", "id": "gemini",
                     "model": "gemini-3.6-flash"},
        "review_status": "pending", "safe_for_edit_plan": False,
        "summary": {"project_asset_count": 2, "provider_media_count": 2,
                    "mapped_media_count": 2, "observation_count": 2,
                    "accepted_range_count": 2, "rejected_count": 0,
                    "clamped_count": 0, "risk_flagged_count": 0},
        "unmapped_media": [], "warnings": [], "observations": prior_obs})
    write_json(prior / "reviews.json", {
        "schema_version": "semantic-reviews.v1", "project_id": pid,
        "run_key": "gemini-live-prior", "updated_at": "2026-09-01",
        "decisions": {"ev-old1": {"event_id": "h1", "evidence_id": "ev-old1",
                                  "action": "approve", "caption": "cafe (humano)",
                                  "note": "human", "reviewed_at": "2026-09-01"}},
        "events": [{"event_id": "h1", "evidence_id": "ev-old1",
                    "action": "approve", "caption": "cafe (humano)",
                    "note": "human", "reviewed_at": "2026-09-01"}]})
    write_json(prior / "manifest.json", {
        "schema_version": "semantic-run-manifest.v1",
        "run_key": "gemini-live-prior", "run_id": "prior", "project_id": pid,
        "content_key": "prior-key",
        "provider": {"adapter": "owned-live-visual", "id": "gemini",
                     "model": "gemini-3.6-flash"},
        "review_status": "pending", "safe_for_edit_plan": False,
        "summary": {}, "warnings": [], "telemetry": {},
        "imported_at": "2026-09-01", "detail_url": "x"})

    analyzed_ids = []

    def fake_analyze(client, target, media_root, project_id_, run_id, progress=None):
        analyzed_ids.extend(a["asset_id"] for a in target)
        doc = {"schema_version": "semantic-evidence.v1",
               "generated_at": "2026-09-07", "project_id": pid, "run_id": run_id,
               "provider": {"adapter": "owned-live-visual", "id": "gemini",
                            "model": "gemini-3.6-flash"},
               "review_status": "pending", "safe_for_edit_plan": False,
               "summary": {"project_asset_count": len(target),
                           "provider_media_count": len(target),
                           "mapped_media_count": len(target),
                           "observation_count": 1, "accepted_range_count": 1,
                           "rejected_count": 0, "clamped_count": 0,
                           "risk_flagged_count": 0},
               "unmapped_media": [], "warnings": [],
               "observations": [
                   {"evidence_id": "ev-nuevo", "asset_id": "nuevo",
                    "caption": "rocket display", "normalization_status": "accepted",
                    "review_status": "pending", "risk_flags": [],
                    "model_confidence": 0.95, "evidence_type": "visual",
                    "start_seconds": 0.0, "end_seconds": 5.0}]}
        return doc, [], {}

    class _Cfg:
        model = "gemini-3.6-flash"

    class _Client:
        config = _Cfg()

    monkeypatch.setattr(projects_mod, "make_client", lambda p, m: _Client())
    monkeypatch.setattr(projects_mod, "analyze_assets", fake_analyze)
    monkeypatch.setattr(projects_mod, "validate_semantic_evidence",
                        lambda doc, schema: None)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "_corroborate_speech_claims", lambda p: None)
    monkeypatch.setattr(svc, "_mark_semantic_progress", lambda p, s: None)
    monkeypatch.setattr(svc, "_detect_rotations", lambda p, c: None)

    run = svc.analyze_visual(pid, "gemini", "gemini-3.6-flash")

    assert analyzed_ids == ["nuevo"]              # ONLY the new clip was analyzed
    obs = {o["evidence_id"]: o for o in run["observations"]}
    assert set(obs) == {"ev-old1", "ev-old2", "ev-nuevo"}   # merged, ids stable
    # the HUMAN decision on old1 survives (caption override applied at read)
    assert obs["ev-old1"]["review_status"] == "reviewed"
    assert obs["ev-old1"]["reviewed_caption"] == "cafe (humano)"
    # the new observation was auto-approved by policy
    assert obs["ev-nuevo"]["review_status"] == "reviewed"

    # The merged run records its coverage + sha snapshot, and the inventory's
    # analysis_status is finally MAINTAINED (it used to sit at technical_only
    # forever, making every clip look unanalyzed — user report 2026-09-07).
    manifests = [m for m in svc._current_run_manifests(pid)
                 if m["provider"]["adapter"] == "owned-live-visual"]
    (m2,) = manifests
    assert set(m2["analyzed_asset_ids"]) == {"old1", "old2", "nuevo"}
    assert m2["asset_shas"]["nuevo"] == "nuevo"
    statuses = {a["asset_id"]: a["analysis_status"]
                for a in svc.get_project(pid)["inventory"]["assets"]}
    assert statuses == {"old1": "analyzed", "old2": "analyzed",
                        "nuevo": "analyzed"}

    # A REPLACED file (same id, new sha) is re-analyzed on the next run even
    # though it is "covered" — detected via the stored sha snapshot.
    path = runtime / pid / "project.json"
    doc = json.loads(path.read_text())
    for a in doc["inventory"]["assets"]:
        if a["asset_id"] == "old2":
            a["sha256"] = "old2-changed"
    path.write_text(json.dumps(doc))
    analyzed_ids.clear()
    svc.analyze_visual(pid, "gemini", "gemini-3.6-flash")
    assert analyzed_ids == ["old2"]


def test_drive_inbox_reports_new_files_for_imported_folders(tmp_path, monkeypatch):
    """An imported folder that GREW on Drive (phone dropped new clips) must
    surface new_files/new_bytes — not be hidden forever behind imported=True."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "9-12-abril"
    (runtime / pid).mkdir(parents=True)
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "9-12 abril", "status": "plan_ready", "updated_at": "2026-09-06",
        "created_at": "2026-09-01", "plan": {}, "inventory": {"assets": []}})
    # locally present: old.mp4; remote also has nuevo.mp4 (uploaded later)
    local = root / "footage" / pid
    local.mkdir(parents=True)
    (local / "old.mp4").write_bytes(b"x" * 10)

    listing = json.dumps([
        {"Path": "9-12 abril/old.mp4", "Size": 10, "ModTime": "2026-09-01T00:00:00Z"},
        {"Path": "9-12 abril/nuevo.mp4", "Size": 500, "ModTime": "2026-09-01T00:00:00Z"},
    ])

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["rclone", "lsjson"]
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    monkeypatch.setattr(projects_mod.subprocess, "run", fake_run)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    folders = svc.drive_inbox()
    (folder,) = folders
    assert folder["imported"] is True
    assert folder["new_files"] == 1 and folder["new_bytes"] == 500


def test_import_drive_folder_updates_existing_project(tmp_path, monkeypatch):
    """Importing a folder whose project already exists must SYNC the new clips
    into it (rclone copy is incremental) instead of failing on 'already exists'."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "9-12-abril"
    (runtime / pid).mkdir(parents=True)
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "9-12 abril", "source_directory": f"footage/{pid}",
        "analysis": {}, "plan": {}, "inventory": {"assets": []}})
    (root / "footage" / pid).mkdir(parents=True)

    class FakeProc:
        returncode = 0

        def __init__(self, cmd, **kwargs):
            # the incremental copy "downloads" the new clip
            if cmd[:2] == ["rclone", "copy"]:
                (root / "footage" / pid / "nuevo.mp4").write_bytes(b"v" * 40)

        def communicate(self, timeout=None):
            return "", ""

        def kill(self):
            pass

    monkeypatch.setattr(projects_mod.subprocess, "Popen", FakeProc)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    # probe/thumbnail need ffprobe — stub them for the fake video file
    monkeypatch.setattr(svc, "_probe_asset", lambda aid, p: {
        "asset_id": aid, "filename": p.name, "media_type": "video",
        "source_path": str(p.relative_to(root)), "size_bytes": p.stat().st_size,
        "duration_seconds": 3.0})
    monkeypatch.setattr(svc, "_make_thumbnail", lambda *a: False)

    result = svc.import_drive_folder("9-12 abril")
    assert result["updated"] is True and result["project_id"] == pid
    assert result["added"] == ["nuevo.mp4"]
    names = [a["filename"] for a in
             svc.get_project(pid)["inventory"]["assets"]]
    assert "nuevo.mp4" in names


def test_drive_place_sequence_appends_after_the_lane(tmp_path, monkeypatch):
    """sequence=True makes the SERVER append after the voiceover lane's current
    end — clients send the draft start and stay dumb (the device-side cursors
    this replaces kept colliding on stale state, 2026-09-08)."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-seq"
    (runtime / pid).mkdir(parents=True)
    (root / "footage" / pid).mkdir(parents=True)
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "S", "source_directory": f"footage/{pid}",
        "plan": {"tracks": [{"kind": "audio", "role": "voiceover", "events": [
            # the phantom-float lane: end sums to 10.600000000000001
            {"event_id": "vo-01", "asset_id": "a", "timeline_start_seconds": 0.0,
             "duration_seconds": 5.933333},
            {"event_id": "vo-02", "asset_id": "b",
             "timeline_start_seconds": 5.933333, "duration_seconds": 4.666667}]}]},
        "inventory": {"assets": []}})
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "drive_voice_files", lambda: [
        {"path": "x/p3.m4a", "name": "p3.m4a", "bytes": 1, "modified": "z"}])

    captured = {}

    def fake_place(project_id, source_path, start, cap):
        captured["start"] = start
        return {"status": "plan_ready"}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["rclone", "copyto"]:
            Path(cmd[3]).write_bytes(b"v")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(svc, "place_voiceover", fake_place)
    monkeypatch.setattr(projects_mod.subprocess, "run", fake_run)

    # requested at 0.0, but the lane already ends at ~10.6 → server appends
    svc.place_voiceover_from_drive(pid, "x/p3.m4a", 0.0, sequence=True)
    assert captured["start"] >= 10.6
    # without sequence, the requested start is honored verbatim
    svc.place_voiceover_from_drive(pid, "x/p3.m4a", 0.0)
    assert captured["start"] == 0.0


def test_rename_project_changes_display_name_only(tmp_path):
    """Renaming changes the display name; the project_id (slug keying runtime,
    footage and OpenTake state) never moves. Blank/oversized names are refused."""
    from video_app import projects as projects_mod
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-rn"
    (runtime / pid).mkdir(parents=True)
    write_json(runtime / pid / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "Viejo", "plan": {}, "inventory": {"assets": []}})
    svc = ProjectService(Settings(root=root, runtime=runtime))

    out = svc.rename_project(pid, "  Semana en Stanford  ")
    assert out == {"project_id": pid, "name": "Semana en Stanford"}
    assert svc.get_project(pid)["name"] == "Semana en Stanford"
    assert svc.get_project(pid)["project_id"] == pid     # slug untouched

    with pytest.raises(projects_mod.ProjectError):
        svc.rename_project(pid, "   ")
    with pytest.raises(projects_mod.ProjectError):
        svc.rename_project(pid, "x" * 81)


def test_chat_context_lists_unused_footage(tmp_path, monkeypatch):
    """The chat brief must include approved footage NOT in the cut — otherwise
    the model truthfully denies footage that exists but wasn't picked for the
    proposal (user report 2026-09-07)."""
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-ctx"
    (runtime / pid).mkdir(parents=True)
    plan = {"concept_id": "c1",
            "project": {"duration_seconds": 6.0},
            "tracks": [{"kind": "video", "events": [
                {"event_id": "v01", "asset_id": "clip_cafe",
                 "timeline_start_seconds": 0.0, "duration_seconds": 6.0,
                 "source_start_seconds": 0.0, "source_end_seconds": 6.0,
                 "observed_content": "Coffee at a cafe."}]}]}
    project = {"schema_version": "video-app-project.v1", "project_id": pid,
               "name": "Ctx", "plan": plan, "concepts": [],
               "inventory": {"assets": []}}
    write_json(runtime / pid / "project.json", project)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda p: [
        # used: overlaps the placed clip_cafe range -> NOT listed
        {"evidence_id": "e1", "asset_id": "clip_cafe", "caption": "coffee pour",
         "start_seconds": 1.0, "end_seconds": 3.0, "evidence_type": "visual"},
        # unused: a lake clip that exists but is not in the cut -> LISTED
        {"evidence_id": "e2", "asset_id": "clip_lake", "caption": "a calm lake at sunset",
         "start_seconds": 0.0, "end_seconds": 5.0, "evidence_type": "visual"},
        # speech evidence is not part of the visual catalog
        {"evidence_id": "e3", "asset_id": "clip_lake", "caption": "someone says hi",
         "start_seconds": 0.0, "end_seconds": 5.0, "evidence_type": "speech"},
    ])
    ctx = svc._chat_context(project, plan)
    assert "MATERIAL DISPONIBLE SIN USAR" in ctx
    assert "a calm lake at sunset" in ctx
    assert "coffee pour" not in ctx          # already displayed by the cut
    assert "someone says hi" not in ctx      # speech is not footage


def test_chat_attaches_full_evidence_for_clips_named_in_the_message(tmp_path, monkeypatch):
    """When the creator names a clip (filename or asset id) in a chat message,
    its FULL evidence — approved and pending-tagged — is attached to the context
    uncapped, and the index lists every clip so descriptive references can be
    resolved (user ask 2026-09-08)."""
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-mention"
    (runtime / pid).mkdir(parents=True)
    project = {
        "schema_version": "video-app-project.v1", "project_id": pid,
        "name": "M", "plan": {"concept_id": "c1",
                              "project": {"duration_seconds": 6.0}, "tracks": []},
        "concepts": [],
        "inventory": {"assets": [
            {"asset_id": "20260409_170210", "filename": "20260409_170210.mp4",
             "media_type": "video", "duration_seconds": 5.0},
            {"asset_id": "clase_sueter", "filename": "clase_sueter.mp4",
             "media_type": "video", "duration_seconds": 8.0}]},
    }
    write_json(runtime / pid / "project.json", project)
    svc = ProjectService(Settings(root=root, runtime=runtime))
    monkeypatch.setattr(svc, "approved_evidence", lambda p: [
        {"evidence_id": "e1", "asset_id": "20260409_170210",
         "caption": "Jensen walks toward a dark SUV outside", "evidence_type": "visual",
         "start_seconds": 0.0, "end_seconds": 5.0},
        {"evidence_id": "e2", "asset_id": "clase_sueter",
         "caption": "instructor in a red sweater at a whiteboard",
         "evidence_type": "visual", "start_seconds": 0.0, "end_seconds": 8.0}])
    monkeypatch.setattr(svc, "pending_evidence", lambda p: [
        {"evidence_id": "p1", "asset_id": "20260409_170210",
         "caption": "crowd waits by the exit", "evidence_type": "visual",
         "start_seconds": 2.0, "end_seconds": 4.0}])

    # message names one clip by FILENAME — matched case-insensitively
    mentioned = svc._mentioned_assets(project, "usa el clip 20260409_170210.MP4 al final")
    assert [a["asset_id"] for a in mentioned] == ["20260409_170210"]
    section = svc._designated_clips_section(pid, mentioned)
    assert "CLIPS SEÑALADOS" in section
    assert "Jensen walks toward a dark SUV" in section        # approved, verbatim
    assert "VISUAL:" in section                               # rows are TYPED
    # pending lives in its own do-not-assert block, not mixed with approved
    assert "SIN VERIFICAR (NO las afirmes" in section and "crowd waits" in section
    assert "red sweater" not in section                        # only the named clip

    # the context's index lists EVERY clip (uncapped roster), framed as hints —
    # never proof of absence
    ctx = svc._chat_context(project, project["plan"])
    assert "ÍNDICE DE CLIPS" in ctx and "NUNCA prueba de ausencia" in ctx
    assert "clase_sueter.mp4" in ctx and "red sweater" in ctx
    assert "20260409_170210.mp4" in ctx

    # prose words never false-match short ids
    assert svc._mentioned_assets(project, "quiero un final épico") == []
    # EXACT token matching: a longer filename must not drag in a prefix sibling
    project["inventory"]["assets"].append(
        {"asset_id": "20260409_1702101", "filename": "20260409_1702101.mp4",
         "media_type": "video", "duration_seconds": 3.0})
    hits = svc._mentioned_assets(project, "mete 20260409_1702101.mp4 al final")
    assert [a["asset_id"] for a in hits] == ["20260409_1702101"]

    # speech rows are labeled as proving what was SAID, not shown
    monkeypatch.setattr(svc, "approved_evidence", lambda p: [
        {"evidence_id": "s1", "asset_id": "clase_sueter",
         "caption": "dice: hay un coche rojo", "evidence_type": "speech",
         "start_seconds": 0.0, "end_seconds": 2.0}])
    monkeypatch.setattr(svc, "pending_evidence", lambda p: [])
    sec2 = svc._designated_clips_section(
        pid, [project["inventory"]["assets"][1]])
    assert "SPEECH (prueba lo DICHO" in sec2

    # the section is bounded: naming many clips truncates with an honest note
    many = [dict(project["inventory"]["assets"][0], asset_id=f"a{i}",
                 filename=f"clip_file_{i}.mp4") for i in range(9)]
    sec3 = svc._designated_clips_section(pid, many)
    assert "muestro 4" in sec3


def test_models_in_use_reports_used_and_next(tmp_path):
    """Ongoing projects can't retroactively change a completed stage's model, so
    the endpoint reports what each stage ACTUALLY used (recorded provenance) plus
    what the next run would use (pref/default) — including a divergence."""
    from video_app.config import Settings
    from video_app.projects import ProjectService

    root = tmp_path / "root"
    runtime = root / "runtime"
    pid = "vlog-models"
    pdir = runtime / pid
    pdir.mkdir(parents=True)
    write_json(pdir / "project.json", {
        "schema_version": "video-app-project.v1", "project_id": pid, "name": "M",
        # pref says qwen3.7-plus for the NEXT concepts run...
        "model_prefs": {"concepts": {"provider": "qwen", "model": "qwen3.7-plus"}},
        "plan": {}, "inventory": {"assets": []},
    })
    # ...but the stories that exist were generated by deepseek (provenance).
    (pdir / "analysis").mkdir()
    write_json(pdir / "analysis" / "concepts.json", {
        "provenance": {"adapter": "owned-planning", "provider": "qwen",
                       "model": "deepseek-v4-pro"}})
    # a completed visual run + the local ASR run
    run = pdir / "analysis" / "runs" / "gemini-live-abc"
    run.mkdir(parents=True)
    write_json(run / "manifest.json", {
        "provider": {"adapter": "owned-live-visual", "id": "gemini",
                     "model": "gemini-3.6-flash"},
        "imported_at": "2026-09-06T00:00:00Z"})
    asr = pdir / "analysis" / "runs" / "asr-live-def"
    asr.mkdir()
    write_json(asr / "manifest.json", {
        "provider": {"adapter": "local-asr", "id": "faster-whisper",
                     "model": "whisper-large-v3-cuda"},
        "imported_at": "2026-09-06T00:00:00Z"})

    svc = ProjectService(Settings(root=root, runtime=runtime))
    stages = svc.models_in_use(pid)["stages"]
    # concepts: used deepseek, next run would use the qwen3.7-plus pref
    assert stages["concepts"]["used"]["model"] == "deepseek-v4-pro"
    assert stages["concepts"]["next"]["model"] == "qwen3.7-plus"
    # visual: used == recorded manifest; next falls back to the stage default
    assert stages["visual"]["used"]["model"] == "gemini-3.6-flash"
    assert stages["visual"]["next"]["model"]
    # ASR rides along as informative (local, no picker)
    assert stages["asr"]["used"]["model"] == "whisper-large-v3-cuda"
    assert stages["asr"]["next"] is None


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

    # Structure: the SYSTEM message carries only the static policy; the project
    # data (scene map, catalogs — footage-derived text) travels as an explicitly
    # delimited UNTRUSTED user message so caption text can never act as a
    # directive (Codex review 2026-09-08).
    system = captured["messages"][0]["content"]
    assert captured["messages"][0]["role"] == "system"
    assert "no contiene instrucciones" not in system  # policy only, no data blob
    data = captured["messages"][1]["content"]
    assert captured["messages"][1]["role"] == "user"
    assert data.startswith("DATOS DEL PROYECTO") and "<<<DATOS" in data
    assert "MAPA DE ESCENAS" in data
    assert "maroon cap" in data              # grounded in real observed_content
    assert "Robot day" in data               # concept title
    assert "RECOMENDACIÓN DE NARRACIÓN" in data
    assert "BROLL_MARKER" not in data        # B-roll excluded from the scene map
    assert captured["messages"][-1] == {
        "role": "user", "content": "¿qué voz en off le pongo al inicio?"}

    assert result["message"]["content"].startswith("🎙️")
    assert result["message"]["voiceover_draft"]["end_seconds"] == 6.0
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]

    # Second turn carries the prior thread to the model as history.
    canned["content"] = json.dumps({"kind": "reply", "text": "vale, más corta"})
    svc.chat_send(pid, "hazla más corta")
    # system policy, DATOS wrapper, then the actual conversation turns
    assert [m["role"] for m in captured["messages"]] == \
        ["system", "user", "user", "assistant", "user"]
    assert captured["messages"][1]["content"].startswith("DATOS DEL PROYECTO")

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
