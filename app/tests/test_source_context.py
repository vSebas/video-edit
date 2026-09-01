from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_app.context import (
    analyze_context,
    anchor_events,
    merge_window_contexts,
    prepare_source_parts,
)
from video_app.planning import evidence_pack, source_context_section
from video_app.telemetry import aggregate_call_telemetry


def context_payload(events, relationships=None) -> dict:
    return {
        "summary": "A source-level summary.",
        "language": "en",
        "events": events,
        "relationships": relationships or [],
        "people": ["speaker"],
        "topics": ["testing"],
    }


def test_prepare_source_parts_prefers_whole_source_when_it_fits() -> None:
    calls = []

    def encoder(path, start, end, scale, crf):
        calls.append((start, end, scale, crf))
        return b"x" * 1000

    parts = prepare_source_parts(
        Path("unused.mp4"), 120.0, encoder=encoder, inline_limit=20_000
    )

    assert [(item["start_seconds"], item["end_seconds"]) for item in parts] == [
        (0.0, 120.0)
    ]
    assert calls == [(0.0, 120.0, 480, 30)]


def test_prepare_source_parts_uses_fewest_overlapping_windows_when_whole_is_too_large() -> None:
    def encoder(path, start, end, scale, crf):
        size = 4000 if end - start > 180 else 1000
        return b"x" * size

    parts = prepare_source_parts(
        Path("unused.mp4"), 300.0, encoder=encoder, inline_limit=20_000
    )

    assert [(item["start_seconds"], item["end_seconds"]) for item in parts] == [
        (0.0, 180.0),
        (170.0, 300.0),
    ]


def test_merge_offsets_timestamps_and_deduplicates_overlap_by_iou() -> None:
    first = context_payload(
        [
            {
                "start_seconds": 20.0,
                "end_seconds": 30.0,
                "label": "setup",
                "description": "A setup happens.",
            },
            {
                "start_seconds": 168.0,
                "end_seconds": 178.0,
                "label": "question",
                "description": "A question is asked.",
            },
        ]
    )
    second = context_payload(
        [
            {
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "label": "same question",
                "description": "The overlapping question is repeated.",
            },
            {
                "start_seconds": 20.0,
                "end_seconds": 30.0,
                "label": "answer",
                "description": "The answer follows.",
            },
        ],
        [
            {
                "kind": "question_answer",
                "from_event": "same question",
                "to_event": "answer",
                "description": "The second event answers the first.",
            }
        ],
    )

    merged = merge_window_contexts(
        "clip", 300.0, [
            {"start_seconds": 0.0, "context": first},
            {"start_seconds": 170.0, "context": second},
        ]
    )

    assert len(merged["events"]) == 3
    assert merged["events"][2]["start_seconds"] == 190.0
    relationship = merged["relationships"][0]
    assert relationship["from_event"] == merged["events"][1]["event_id"]
    assert relationship["to_event"] == merged["events"][2]["event_id"]


def test_analyze_context_with_stub_client_windows_anchors_and_reports_telemetry(
    tmp_path: Path,
) -> None:
    class Config:
        provider = "gemini"
        model = "stub-gemini"

    class StubClient:
        config = Config()

        def __init__(self):
            self.calls = []
            self.responses = [
                context_payload(
                    [
                        {
                            "start_seconds": 168.0,
                            "end_seconds": 178.0,
                            "label": "question",
                            "description": "A question is asked.",
                        }
                    ]
                ),
                context_payload(
                    [
                        {
                            "start_seconds": 0.0,
                            "end_seconds": 10.0,
                            "label": "same question",
                            "description": "The same question overlaps.",
                        },
                        {
                            "start_seconds": 20.0,
                            "end_seconds": 30.0,
                            "label": "answer",
                            "description": "The answer follows.",
                        },
                    ],
                    [
                        {
                            "kind": "question_answer",
                            "from_event": "same question",
                            "to_event": "answer",
                            "description": "The later event answers the question.",
                        }
                    ],
                ),
            ]

        def chat(self, messages, **options):
            self.calls.append((messages, options))
            payload = self.responses.pop(0)
            return {
                "content": json.dumps(payload),
                "model": self.config.model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "telemetry": {
                    "request_bytes": 100,
                    "wall_seconds": 0.5,
                    "retries": 0,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            }

    media = tmp_path / "clip.mp4"
    media.touch()
    client = StubClient()

    def encoder(path, start, end, scale, crf):
        return b"x" * (4000 if end - start > 180 else 1000)

    normalized, raw, telemetry = analyze_context(
        client,
        [
            {
                "asset_id": "clip",
                "filename": "clip.mp4",
                "source_path": "clip.mp4",
                "media_type": "video",
                "duration_seconds": 300.0,
            }
        ],
        tmp_path,
        "project",
        "run",
        [
            {
                "asset_id": "clip",
                "evidence_id": "fine-1",
                "start_seconds": 190.0,
                "end_seconds": 195.0,
                "normalization_status": "accepted",
            }
        ],
        encoder=encoder,
        inline_limit=20_000,
    )

    assert len(client.calls) == 2
    assert len(raw) == 2
    assert len(normalized["assets"][0]["events"]) == 2
    assert normalized["assets"][0]["events"][1]["start_seconds"] == 190.0
    assert normalized["assets"][0]["events"][1]["evidence_ids"] == ["fine-1"]
    assert telemetry["calls"] == 2
    assert telemetry["unique_source_seconds"] == 300.0


def test_anchor_events_links_all_overlapping_fine_observations() -> None:
    events = [
        {
            "event_id": "event_1",
            "start_seconds": 5.0,
            "end_seconds": 10.0,
            "label": "action",
            "description": "An action.",
            "evidence_ids": [],
        }
    ]
    observations = [
        {
            "asset_id": "clip",
            "evidence_id": "visual-1",
            "start_seconds": 4.0,
            "end_seconds": 6.0,
            "normalization_status": "accepted",
        },
        {
            "asset_id": "clip",
            "evidence_id": "speech-1",
            "start_seconds": 9.5,
            "end_seconds": 11.0,
            "normalization_status": "accepted",
        },
        {
            "asset_id": "other",
            "evidence_id": "wrong-asset",
            "start_seconds": 5.0,
            "end_seconds": 10.0,
            "normalization_status": "accepted",
        },
    ]

    anchored = anchor_events("clip", events, observations)

    assert anchored[0]["evidence_ids"] == ["visual-1", "speech-1"]


def test_source_context_injection_is_prepended_and_capped() -> None:
    events = []
    for index in range(100):
        events.append(
            {
                "event_id": f"event_{index}",
                "start_seconds": float(index),
                "end_seconds": float(index + 1),
                "label": f"event {index}",
                "description": "long context description " * 5,
                "evidence_ids": [f"evidence_{index}"],
            }
        )
    source_context = {
        "assets": [
            {
                "asset_id": "clip",
                "summary": "summary",
                "events": events,
                "relationships": [],
            }
        ]
    }
    section = source_context_section(source_context)
    project = {
        "inventory": {
            "assets": [
                {
                    "asset_id": "clip",
                    "filename": "clip.mp4",
                    "media_type": "video",
                    "duration_seconds": 100.0,
                    "video": {"width": 1080, "height": 1920},
                }
            ]
        }
    }
    evidence = [
        {
            "asset_id": "clip",
            "start_seconds": 0.0,
            "end_seconds": 1.0,
            "evidence_type": "visual",
            "confidence": 0.9,
            "caption": "Real observation.",
        }
    ]

    pack = evidence_pack(project, evidence, source_context)

    assert len(section) <= 2500
    assert section.endswith("source context truncated")
    assert pack.startswith("## Source context (derived, non-citable)")
    assert pack.index("## Source context") < pack.index("## Assets")
    assert "Real observation." in pack
    assert "## Source context" not in evidence_pack(project, evidence)


def test_telemetry_aggregation_sums_costs_and_deoverlaps_coverage() -> None:
    records = [
        {
            "asset_id": "a",
            "source_start_seconds": 0,
            "source_end_seconds": 10,
            "telemetry": {
                "request_bytes": 100,
                "wall_seconds": 1.2,
                "retries": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
        {
            "asset_id": "a",
            "source_start_seconds": 8,
            "source_end_seconds": 20,
            "telemetry": {
                "request_bytes": 200,
                "wall_seconds": 2.0,
                "retries": 0,
                "prompt_tokens": 20,
                "completion_tokens": 7,
            },
        },
        {
            "asset_id": "b",
            "source_start_seconds": 0,
            "source_end_seconds": 4,
            "telemetry": {
                "request_bytes": 50,
                "wall_seconds": 0.8,
                "retries": 0,
                "prompt_tokens": 4,
                "completion_tokens": 2,
            },
        },
    ]

    telemetry = aggregate_call_telemetry(records)

    assert telemetry == {
        "calls": 3,
        "retries": 1,
        "uploaded_bytes": 350,
        "prompt_tokens": 34,
        "completion_tokens": 14,
        "wall_seconds": 4.0,
        "unique_source_seconds": 24.0,
    }


def test_source_context_never_enters_approved_or_pending_evidence(tmp_path: Path) -> None:
    from video_app.config import Settings
    from video_app.projects import ProjectError, ProjectService

    runtime = tmp_path / "runtime"
    project_id = "project"
    project_root = runtime / project_id
    project_root.mkdir(parents=True)
    (project_root / "project.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "name": "Project",
                "status": "semantic_ready",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-01T00:00:00Z",
                "inventory": {"assets": []},
                "concepts": [],
                "plan": None,
                "analysis": {},
            }
        ),
        encoding="utf-8",
    )

    visual_dir = project_root / "analysis" / "runs" / "gemini-live-visual"
    visual_dir.mkdir(parents=True)
    (visual_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_key": "gemini-live-visual",
                "imported_at": "2026-08-01T00:00:00Z",
                "provider": {"adapter": "owned-live-visual"},
            }
        ),
        encoding="utf-8",
    )
    observation_base = {
        "clip_id": "shot",
        "media_id": "clip",
        "asset_id": "clip",
        "filename": "clip.mp4",
        "raw_start_seconds": 0.0,
        "raw_end_seconds": 1.0,
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "source": "model",
        "normalization_status": "accepted",
        "review_status": "pending",
        "adjustments": [],
        "rejection_reasons": [],
        "risk_flags": [],
        "model_confidence": 0.9,
        "evidence_type": "visual",
    }
    (visual_dir / "normalized.json").write_text(
        json.dumps(
            {
                "schema_version": "semantic-evidence.v1",
                "summary": {},
                "observations": [
                    {**observation_base, "evidence_id": "approved", "caption": "real one"},
                    {**observation_base, "evidence_id": "pending", "caption": "real two"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (visual_dir / "reviews.json").write_text(
        json.dumps(
            {
                "decisions": {
                    "approved": {
                        "action": "approve",
                        "caption": "real one",
                        "note": None,
                        "reviewed_at": "2026-08-01T00:00:00Z",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    context_dir = project_root / "analysis" / "runs" / "ctx-live-derived"
    context_dir.mkdir(parents=True)
    (context_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_key": "ctx-live-derived",
                "imported_at": "2026-08-02T00:00:00Z",
                "provider": {"adapter": "owned-source-context"},
            }
        ),
        encoding="utf-8",
    )
    (context_dir / "normalized.json").write_text(
        json.dumps(
            {
                "schema_version": "source-context.v1",
                "assets": [
                    {
                        "asset_id": "clip",
                        "events": [
                            {
                                "event_id": "derived",
                                "start_seconds": 0,
                                "end_seconds": 1,
                                "label": "must never be evidence",
                                "description": "derived",
                                "evidence_ids": ["approved"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ProjectService(Settings(root=tmp_path, runtime=runtime))

    assert [item["caption"] for item in service.approved_evidence(project_id)] == [
        "real one"
    ]
    assert [item["caption"] for item in service.pending_evidence(project_id)] == [
        "real two"
    ]
    with pytest.raises(ProjectError, match="cannot be reviewed"):
        service.review_semantic_evidence(
            project_id, "ctx-live-derived", "derived", "approve"
        )
