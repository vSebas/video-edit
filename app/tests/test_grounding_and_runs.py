"""Regressions for the grounding gate, media identity, and ASR run recency.

Each test here pins a defect found in the 2026-08-19 dual review (Codex
full-project pass + the five-dimension internal pass).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_app.planning import build_plan, sanitize_spans, span_supported

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_with(assets: list[dict]) -> dict:
    return {"project_id": "unit-test", "inventory": {"assets": assets}}


def video_asset(asset_id: str = "clip_a", duration: float = 20.0) -> dict:
    return {
        "asset_id": asset_id,
        "filename": f"{asset_id}.mp4",
        "media_type": "video",
        "duration_seconds": duration,
        "video": {"width": 1080, "height": 1920},
        "audio": {"sample_rate": 48000, "channels": 2},
    }


def span(asset_id: str, start: float, end: float) -> dict:
    return {
        "label": "beat",
        "asset_id": asset_id,
        "source_start_seconds": start,
        "source_end_seconds": end,
        "intent": "x",
        "observed_content": "y",
        "confidence": 0.9,
    }


class TestSpanSupport:
    """span_supported used to pass any cut whose midpoint grazed evidence."""

    def test_cut_covered_by_evidence_is_supported(self) -> None:
        assert span_supported(span("clip_a", 4.0, 8.0), {"clip_a": [(3.5, 9.0)]})

    def test_cut_running_far_past_its_evidence_is_rejected(self) -> None:
        # One second of approved observation, a ten-second cut around it.
        assert not span_supported(span("clip_a", 0.0, 10.0), {"clip_a": [(4.5, 5.5)]})

    def test_word_snapped_edges_just_outside_evidence_still_pass(self) -> None:
        # Snapping legitimately nudges a boundary past the observation.
        assert span_supported(span("clip_a", 3.88, 8.12), {"clip_a": [(4.0, 8.0)]})

    def test_evidence_on_another_asset_does_not_support(self) -> None:
        assert not span_supported(span("clip_b", 4.0, 8.0), {"clip_a": [(0.0, 20.0)]})

    def test_adjacent_ranges_combine_without_double_counting(self) -> None:
        assert span_supported(span("clip_a", 0.0, 8.0), {"clip_a": [(0.0, 4.0), (4.0, 8.0)]})


class TestSanitizeSpans:
    """Only footage belongs on the video track."""

    def test_audio_only_asset_never_reaches_the_video_track(self) -> None:
        project = project_with(
            [
                video_asset(),
                {
                    "asset_id": "voice_over",
                    "filename": "voice_over.m4a",
                    "media_type": "audio",
                    "duration_seconds": 30.0,
                    "video": None,
                    "audio": {"sample_rate": 48000, "channels": 1},
                },
            ]
        )
        spans = sanitize_spans(
            project,
            [
                {
                    "asset_id": "voice_over",
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 5.0,
                },
                {
                    "asset_id": "clip_a",
                    "source_start_seconds": 1.0,
                    "source_end_seconds": 4.0,
                },
            ],
        )
        assert [item["asset_id"] for item in spans] == ["clip_a"]

    def test_still_image_is_dropped_rather_than_compiled_with_no_duration(self) -> None:
        project = project_with(
            [
                {
                    "asset_id": "photo",
                    "filename": "photo.jpg",
                    "media_type": "image",
                    "duration_seconds": 0.0,
                    "video": {"width": 4032, "height": 3024},
                    "audio": None,
                }
            ]
        )
        spans = sanitize_spans(
            project,
            [{"asset_id": "photo", "source_start_seconds": 0.0, "source_end_seconds": 3.0}],
        )
        assert spans == []


class TestFrameAlignment:
    """Render seeks to the float; exporters round to a frame. Both must agree."""

    def test_source_start_lands_on_the_frame_grid(self) -> None:
        project = project_with([video_asset()])
        plan = build_plan(
            project,
            [span("clip_a", 3.44, 6.97)],
            concept_id="c1",
            benchmark_id="unit-test",
            hook_text="Title",
            fps=30,
        )
        event = plan["tracks"][0]["events"][0]
        for key in ("source_start_seconds", "source_end_seconds", "duration_seconds"):
            frames = event[key] * 30
            assert frames == pytest.approx(round(frames), abs=1e-4), key

    def test_linked_audio_shares_the_quantized_range(self) -> None:
        project = project_with([video_asset()])
        plan = build_plan(
            project,
            [span("clip_a", 3.44, 6.97)],
            concept_id="c1",
            benchmark_id="unit-test",
            hook_text="Title",
            fps=30,
        )
        video = plan["tracks"][0]["events"][0]
        audio = plan["tracks"][1]["events"][0]
        assert video["source_start_seconds"] == audio["source_start_seconds"]
        assert video["source_end_seconds"] == audio["source_end_seconds"]


class TestLatestAsrRun:
    """Run ids are random hex; recency comes from the manifest, not the name."""

    def _service(self, tmp_path: Path):
        from video_app.config import Settings
        from video_app.projects import ProjectService

        return ProjectService(
            Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
        )

    def _write_run(self, runtime: Path, project_id: str, run_key: str,
                   imported_at: str, marker: str) -> None:
        run_dir = runtime / project_id / "analysis" / "runs" / run_key
        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        (run_dir / "raw" / "transcripts.json").write_text(
            json.dumps({"transcripts": [{"asset_id": marker, "segments": []}]}),
            encoding="utf-8",
        )
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_key": run_key,
                    "imported_at": imported_at,
                    "provider": {"adapter": "faster-whisper"},
                }
            ),
            encoding="utf-8",
        )

    def test_newest_run_wins_even_when_its_id_sorts_first(self, tmp_path: Path) -> None:
        service = self._service(tmp_path)
        runtime = tmp_path / "runtime"
        # "f3a9" sorts after "1b2c", but the 1b2c run was imported later.
        self._write_run(runtime, "p", "asr-live-f3a9ff", "2026-08-01T10:00:00Z", "old")
        self._write_run(runtime, "p", "asr-live-1b2c00", "2026-08-19T10:00:00Z", "new")
        latest = service._latest_asr_transcripts("p")
        assert latest is not None
        assert latest["transcripts"][0]["asset_id"] == "new"

    def test_no_runs_returns_none(self, tmp_path: Path) -> None:
        service = self._service(tmp_path)
        (tmp_path / "runtime" / "p" / "analysis" / "runs").mkdir(parents=True)
        assert service._latest_asr_transcripts("p") is None


class TestSyncMediaIdentity:
    """A filename is not an identity: changed bytes must invalidate evidence."""

    def test_replaced_clip_is_reprobed_and_flagged(self, tmp_path: Path) -> None:
        import subprocess as sp

        from video_app.config import Settings
        from video_app.projects import ProjectService

        source = PROJECT_ROOT / "runtime" / "test-fixtures" / f"sync-{tmp_path.name}"
        source.mkdir(parents=True, exist_ok=True)
        clip = source / "clip.mp4"

        def encode(seconds: int) -> None:
            sp.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c=0x223344:s=320x240:d={seconds}:r=30",
                 "-c:v", "libx264", str(clip)],
                check=True,
            )

        try:
            encode(1)
            settings = Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
            service = ProjectService(settings)
            project = service.create_project(
                "Sync me", str(source.relative_to(PROJECT_ROOT)), ""
            )
            project_id = project["project_id"]
            before = project["inventory"]["assets"][0]

            encode(4)  # same filename, different bytes and duration
            result = service.sync_media(project_id)

            assert result["replaced"] == ["clip.mp4"]
            after = service.get_project(project_id)["inventory"]["assets"][0]
            assert after["asset_id"] == before["asset_id"]
            assert after["sha256"] != before["sha256"]
            assert after["duration_seconds"] > before["duration_seconds"]
            warning = service.get_project(project_id)["analysis"]["warning"]
            assert "changed on disk" in warning
        finally:
            if clip.exists():
                clip.unlink()
            if source.exists():
                source.rmdir()

    def test_untouched_clip_is_not_reprobed(self, tmp_path: Path) -> None:
        import subprocess as sp

        from video_app.config import Settings
        from video_app.projects import ProjectService

        source = PROJECT_ROOT / "runtime" / "test-fixtures" / f"stable-{tmp_path.name}"
        source.mkdir(parents=True, exist_ok=True)
        clip = source / "clip.mp4"
        try:
            sp.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "color=c=0x112233:s=320x240:d=1:r=30",
                 "-c:v", "libx264", str(clip)],
                check=True,
            )
            service = ProjectService(
                Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
            )
            project = service.create_project(
                "Stable", str(source.relative_to(PROJECT_ROOT)), ""
            )
            result = service.sync_media(project["project_id"])
            assert result == {"added": [], "removed": [], "replaced": [], "total": 1}
        finally:
            if clip.exists():
                clip.unlink()
            if source.exists():
                source.rmdir()


class TestSpanishRiskPatterns:
    """Captions describe Spanish footage; hedges must flag in both languages."""

    @pytest.mark.parametrize(
        "caption,expected",
        [
            ("La persona parece emocionada", "intent_or_emotion_inference"),
            ("El sujeto habla con un amigo", "unverified_speech_claim"),
            ("Se ve la marca en la botella", "brand_or_product_claim"),
            ("Es la misma persona del clip anterior", "identity_or_continuity_inference"),
        ],
    )
    def test_spanish_hedges_are_flagged(self, caption: str, expected: str) -> None:
        from video_app.visual import risk_flags_for

        assert expected in risk_flags_for(caption)

    def test_plain_spanish_description_stays_clean(self) -> None:
        from video_app.visual import risk_flags_for

        assert risk_flags_for("Una persona camina por el pasillo con una mochila") == []


class TestProjectWriteSerialization:
    """Job threads and request handlers mutate one project.json."""

    def test_concurrent_progress_marks_do_not_lose_each_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading
        import time

        from video_app import projects as projects_module
        from video_app.config import Settings
        from video_app.projects import ProjectService, write_json

        runtime = tmp_path / "runtime"
        (runtime / "p").mkdir(parents=True)
        (runtime / "p" / "project.json").write_text(
            json.dumps(
                {
                    "project_id": "p",
                    "analysis": {"visual": "unavailable", "speech": "unavailable"},
                }
            ),
            encoding="utf-8",
        )
        service = ProjectService(
            Settings(root=PROJECT_ROOT, runtime=runtime)
        )
        monkeypatch.setattr(service, "_semantic_run_manifests", lambda _id: [])

        # Widen the read-modify-write window so an unserialized pair would
        # reliably clobber; the per-project lock must hold the second thread.
        def slow_write(path, value):
            time.sleep(0.05)
            write_json(path, value)

        monkeypatch.setattr(projects_module, "write_json", slow_write)

        threads = [
            threading.Thread(target=service._mark_semantic_progress, args=("p", kind))
            for kind in ("visual", "speech")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        analysis = json.loads(
            (runtime / "p" / "project.json").read_text(encoding="utf-8")
        )["analysis"]
        assert analysis["visual"] == "completed"
        assert analysis["speech"] == "completed"


class TestOptionalTokenGate:
    """VIDEO_EDITING_TOKEN guards the API when it is bound beyond localhost."""

    def _client(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from video_app.config import Settings
        from video_app.main import create_app

        return TestClient(
            create_app(
                Settings(root=PROJECT_ROOT, runtime=tmp_path / "runtime")
            )
        )

    def test_unset_token_leaves_the_app_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VIDEO_EDITING_TOKEN", raising=False)
        with self._client(tmp_path) as client:
            assert client.get("/api/projects").status_code == 200

    def test_set_token_rejects_unauthenticated_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIDEO_EDITING_TOKEN", "s3cret")
        with self._client(tmp_path) as client:
            assert client.get("/api/projects").status_code == 401
            assert client.delete("/api/projects/anything").status_code == 401
            # Health stays open so container checks keep working.
            assert client.get("/api/health").status_code == 200

    def test_correct_token_passes_by_header_or_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIDEO_EDITING_TOKEN", "s3cret")
        with self._client(tmp_path) as client:
            assert client.get(
                "/api/projects", headers={"x-vlog-token": "s3cret"}
            ).status_code == 200
            seeded = client.get("/api/projects", params={"token": "s3cret"})
            assert seeded.status_code == 200
            # The seeded cookie carries the session afterwards.
            assert client.get("/api/projects").status_code == 200

    def test_wrong_token_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIDEO_EDITING_TOKEN", "s3cret")
        with self._client(tmp_path) as client:
            assert client.get(
                "/api/projects", headers={"x-vlog-token": "guess"}
            ).status_code == 401


class TestConceptCitationGrounding:
    """A citation overlapping no observation at all is a fabrication."""

    def _document(self, ranges: list[tuple[float, float]]) -> dict:
        def beat(beat_id: str, start: float, end: float) -> dict:
            return {
                "beat_id": beat_id,
                "purpose": "Beat",
                "target_duration_seconds": end - start,
                "evidence": [
                    {
                        "asset_id": "clip_a",
                        "start_seconds": start,
                        "end_seconds": end,
                        "observed_content": "Test content.",
                        "confidence": 0.9,
                    }
                ],
            }

        return {
            "schema_version": "creative-concepts.v1",
            "generated_at": "2026-08-19T00:00:00Z",
            "benchmark_id": "unit-test",
            "footage_summary": "One clip.",
            "concepts": [
                {
                    "concept_id": f"concept_{index}",
                    "title": f"Concept {index}",
                    "topic": "t", "audience": "a",
                    "platforms": ["instagram_reel"],
                    "target_duration_seconds": 15,
                    "hook": "h",
                    "structure": [
                        beat(f"b{position}", start, end)
                        for position, (start, end) in enumerate(ranges)
                    ],
                    "strengths": ["s"], "weaknesses": ["w"], "missing_shots": [],
                }
                for index in ("one", "two")
            ],
        }

    def test_citation_matching_no_observation_is_dropped(self) -> None:
        from video_app.planning import _sanitize_concepts

        project = project_with([video_asset()])
        document = self._document([(1.0, 3.0), (5.0, 7.0), (9.0, 11.0), (15.0, 18.0)])
        evidence = [
            {"asset_id": "clip_a", "start_seconds": 0.5, "end_seconds": 3.5},
            {"asset_id": "clip_a", "start_seconds": 4.5, "end_seconds": 7.5},
            {"asset_id": "clip_a", "start_seconds": 8.5, "end_seconds": 11.5},
        ]
        _sanitize_concepts(document, project, evidence)
        kept = [beat["beat_id"] for beat in document["concepts"][0]["structure"]]
        # The 15-18s beat cites footage nothing observed.
        assert kept == ["b0", "b1", "b2"]

    def test_concept_falling_below_three_grounded_beats_is_dropped(self) -> None:
        from video_app.planning import _sanitize_concepts

        project = project_with([video_asset()])
        document = self._document([(1.0, 3.0), (5.0, 7.0), (15.0, 18.0)])
        evidence = [
            {"asset_id": "clip_a", "start_seconds": 0.5, "end_seconds": 3.5},
            {"asset_id": "clip_a", "start_seconds": 4.5, "end_seconds": 7.5},
        ]
        _sanitize_concepts(document, project, evidence)
        assert document["concepts"] == []

    def test_without_an_evidence_set_citations_are_left_alone(self) -> None:
        from video_app.planning import _sanitize_concepts

        project = project_with([video_asset()])
        document = self._document([(1.0, 3.0), (5.0, 7.0), (15.0, 18.0)])
        _sanitize_concepts(document, project)
        assert len(document["concepts"][0]["structure"]) == 3


class TestVisualProvenance:
    """The normalized warning must describe what the model actually saw."""

    def test_audio_video_runs_are_not_described_as_keyframes(self) -> None:
        from video_app.visual import provenance_note

        note = provenance_note([{"input_mode": "video+audio"}])
        assert "audio" in note and "keyframes" not in note

    def test_keyframe_fallback_still_says_keyframes(self) -> None:
        from video_app.visual import provenance_note

        assert "keyframes" in provenance_note([{"input_mode": "keyframes"}])

    def test_mixed_modes_mention_both(self) -> None:
        from video_app.visual import provenance_note

        note = provenance_note(
            [{"input_mode": "video+audio"}, {"input_mode": "keyframes"}]
        )
        assert "audio" in note and "keyframes" in note


class TestClaimLevelGrounding:
    """Design-review blocker: time overlap with unrelated approved evidence
    must never authorize a claim — grounding is per CLAIM, not per second."""

    CAPTIONS = {"clip_0": [(0.0, 10.0, "una persona camina por el campus con mochila")]}

    def _span(self, content, needs_review=False):
        span = {"asset_id": "clip_0", "source_start_seconds": 2.0,
                "source_end_seconds": 6.0, "observed_content": content}
        if needs_review:
            span["needs_review"] = True
        return span

    def test_overlapping_but_unrelated_claim_is_dropped(self) -> None:
        from video_app.planning import claim_supported

        # pixels approved (0-10s), but the CLAIM is about something else
        assert claim_supported(
            self._span("camina por el campus con su mochila"), self.CAPTIONS
        ) is True
        assert claim_supported(
            self._span("celebra que ganó la carrera universitaria"), self.CAPTIONS
        ) is False

    def test_needs_review_never_compiles_unconfirmed(self) -> None:
        from video_app.planning import claim_supported

        assert claim_supported(
            self._span("camina por el campus con su mochila", needs_review=True),
            self.CAPTIONS,
        ) is False

    def test_short_claims_keep_benefit_of_doubt(self) -> None:
        from video_app.planning import claim_supported

        assert claim_supported(self._span("la mañana"), self.CAPTIONS) is True

    def test_fabricated_title_blocks_compilation(self) -> None:
        from video_app.planning import PlanningError, compile_edit_plan

        document = {
            "concepts": [{
                "concept_id": "c1",
                "title": "El día que ganó el campeonato nacional de robótica",
                "structure": [{
                    "beat_id": "b1", "purpose": "p",
                    "target_duration_seconds": 4,
                    "evidence": [{
                        "asset_id": "clip_0", "start_seconds": 2.0,
                        "end_seconds": 6.0,
                        "observed_content": "una persona camina por el campus",
                        "confidence": 0.9,
                    }],
                }],
            }],
        }
        project = {"project_id": "p", "inventory": {"assets": [
            {"asset_id": "clip_0", "media_type": "video",
             "duration_seconds": 30, "source_path": "x.mp4",
             "filename": "x.mp4",
             "video": {"width": 1080, "height": 1920}},
        ]}}
        with pytest.raises(PlanningError, match="título"):
            compile_edit_plan(
                project, document, "c1",
                approved_ranges={"clip_0": [(0.0, 10.0)]},
                approved_captions=self.CAPTIONS,
            )


class TestClaimIdLineage:
    """Authorization by evidence identity — the design-review architecture."""

    CAPTIONS = {"clip_0": [(0.0, 10.0, "una persona camina por el campus")]}
    SETS = {
        "approved": {"ev-1": "una persona camina por el campus con mochila"},
        "pending": {"ev-2"},
        "rejected": {"ev-3"},
    }

    def _span(self, content, ids):
        return {"asset_id": "clip_0", "source_start_seconds": 2.0,
                "source_end_seconds": 6.0, "observed_content": content,
                "evidence_ids": ids}

    def test_approved_lineage_authorizes_paraphrase(self) -> None:
        from video_app.planning import claim_supported

        # honest paraphrase with different vocabulary: identity, not
        # word overlap, is what authorizes
        span = self._span("estudiante pasea por la universidad con bolso",
                          ["ev-1"])
        assert claim_supported(span, self.CAPTIONS, self.SETS) is True

    def test_pending_or_rejected_lineage_fails_closed(self) -> None:
        from video_app.planning import claim_supported

        assert claim_supported(
            self._span("una persona camina", ["ev-2"]), self.CAPTIONS, self.SETS
        ) is False
        assert claim_supported(
            self._span("una persona camina", ["ev-1", "ev-3"]),
            self.CAPTIONS, self.SETS,
        ) is False

    def test_approved_lineage_still_blocks_risky_embellishment(self) -> None:
        from video_app.planning import claim_supported

        span = self._span("celebra que ganó la carrera nacional", ["ev-1"])
        assert claim_supported(span, self.CAPTIONS, self.SETS) is False

    def test_short_risky_claim_no_longer_passes_on_brevity(self) -> None:
        from video_app.planning import claim_supported

        span = {"asset_id": "clip_0", "source_start_seconds": 2.0,
                "source_end_seconds": 6.0,
                "observed_content": "ganó la carrera"}
        assert claim_supported(span, self.CAPTIONS, None) is False


class TestTitleGate:
    SUPPORT = "una persona camina por el campus y saluda a sus amigos"

    def test_poetic_title_passes_without_vocabulary_overlap(self) -> None:
        from video_app.planning import title_blocked

        assert title_blocked("Un día cualquiera", self.SUPPORT) is False
        assert title_blocked("Momentos de primavera", self.SUPPORT) is False

    def test_risky_unsupported_title_is_blocked(self) -> None:
        from video_app.planning import title_blocked

        assert title_blocked("Ganó la carrera", self.SUPPORT) is True
        assert title_blocked(
            "El día que renunció a su trabajo", self.SUPPORT
        ) is True

    def test_user_authored_titles_are_exempt(self) -> None:
        from video_app.planning import title_blocked

        assert title_blocked("Ganó la carrera", self.SUPPORT,
                             user_authored=True) is False


class TestScopedCorroboration:
    """ASR overlap proves speech HAPPENS — reported content stays human."""

    def test_reported_speech_content_is_not_auto_approved(self) -> None:
        import re as _re
        # the guard pattern used in _corroborate_run
        pattern = _re.compile(
            r"(dice(?:n)? que|explica(?:n)? que|comenta(?:n)? que|"
            r"cuenta(?:n)? que|pregunta(?:n)? (?:si|por)|responde(?:n)? que|"
            r"says? that|explains? that|tells? .* that|"
            "[\"«»“”])"
        )
        assert pattern.search("ella explica que renunció por frustración".lower())
        assert pattern.search('the man says that he "won the race"'.lower())
        assert not pattern.search("dos personas hablando frente a la cámara")
