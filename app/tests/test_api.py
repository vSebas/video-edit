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
