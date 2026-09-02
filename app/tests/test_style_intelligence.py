"""Reference Style Intelligence — first slice, tested on synthetic media.

The deterministic extractor runs against a generated reference whose shot
structure and silence layout are known exactly; semantic extraction is
stubbed (it is one VLM call); aggregation, matching, and guidance are pure.
"""

import json
import subprocess
from pathlib import Path

import pytest

from video_app.style_intelligence import (
    StyleError,
    aggregate_template,
    build_observation,
    deterministic_observation,
    match_concept,
    semantic_observation,
    style_guidance,
)


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """12s reference: 4 visually hard-cut shots (3s each) with speech-like
    tone on the first half and silence on the second."""
    root = tmp_path_factory.mktemp("ref")
    path = root / "reference.mp4"
    colors = ["red", "blue", "green", "yellow"]
    inputs = []
    for color in colors:
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30:d=3"]
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
            "-f", "lavfi", "-i",
            "sine=frequency=300:sample_rate=48000:duration=6",
            "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=mono:d=6",
            "-filter_complex",
            "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v];"
            "[4:a][5:a]concat=n=2:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
    )
    return path


class TestDeterministicExtraction:
    def test_shots_pacing_and_speech_ratio(self, reference) -> None:
        deterministic, source = deterministic_observation(reference)
        assert source["duration_seconds"] == pytest.approx(12.0, abs=0.3)
        assert source["width"] == 320
        # 4 hard cuts between saturated colors must be found
        assert deterministic["shot_count"] == 4
        assert deterministic["median_shot_seconds"] == pytest.approx(3.0, abs=0.4)
        assert deterministic["cuts_per_minute"] == pytest.approx(15.0, abs=3.0)
        # tone for 6s of 12 -> about half speech
        assert 0.3 <= deterministic["speech_ratio"] <= 0.7

    def test_missing_file_fails_closed(self, tmp_path) -> None:
        with pytest.raises(StyleError):
            deterministic_observation(tmp_path / "nope.mp4")


class FakeVlm:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages, **kwargs):
        return {"content": json.dumps(self.payload)}


SEMANTIC = {
    "hook_type": "unexpected_result",
    "narrative_shape": ["hook", "setup", "failure", "retry", "payoff"],
    "tone": ["energetic", "personal"],
    "payoff_position": "late",
    "broll_ratio_estimate": 0.4,
    "caption_style": "minimal",
    "uses_voiceover": False,
    "notes": "quick cuts around the payoff",
    "confidence": 0.8,
}


class TestSemanticAndAggregation:
    def test_semantic_fields_are_whitelisted(self, reference) -> None:
        payload = dict(SEMANTIC)
        payload["hook_type"] = "not_a_real_hook"
        payload["narrative_shape"] = ["hook", "invented_label", "payoff"]
        semantic = semantic_observation(FakeVlm(payload), reference, 12.0)
        assert semantic["hook_type"] is None
        assert semantic["narrative_shape"] == ["hook", "payoff"]
        assert semantic["broll_ratio_estimate"] == 0.4

    def test_string_tone_is_not_split_into_characters(self, reference) -> None:
        # live 2026-09-02: the VLM returned tone as a bare string and the
        # template stored ['i','n','f','o']
        payload = dict(SEMANTIC)
        payload["tone"] = "informative, personal"
        payload["narrative_shape"] = "hook, payoff"
        semantic = semantic_observation(FakeVlm(payload), reference, 12.0)
        assert semantic["tone"] == ["informative", "personal"]
        assert semantic["narrative_shape"] == ["hook", "payoff"]

    def test_single_reference_template(self, reference) -> None:
        deterministic, source = deterministic_observation(reference)
        semantic = semantic_observation(FakeVlm(SEMANTIC), reference, 12.0)
        observation = build_observation(deterministic, source, semantic)
        template = aggregate_template("problema-y-final", [observation])
        grammar = template["grammar"]
        assert grammar["narrative_shape"] == SEMANTIC["narrative_shape"]
        assert grammar["median_shot_seconds"] == pytest.approx(3.0, abs=0.4)
        assert grammar["payoff_position"] == "late"
        assert template["requirements"]["needs_payoff"] is True
        assert template["requirements"]["needs_broll"] is True
        # a single reference caps confidence — one video is a hint
        assert template["confidence"] <= 0.55


def _concept(payoff=True, shape=None, evidence_count=8):
    beats = []
    for i in range(4):
        beats.append({
            "beat_id": f"b{i}", "purpose": "p", "target_duration_seconds": 5,
            "evidence": [
                {"asset_id": f"clip_{j % 3}", "start_seconds": j,
                 "end_seconds": j + 1, "observed_content": "x",
                 "confidence": 0.9}
                for j in range(i * (evidence_count // 4),
                               (i + 1) * (evidence_count // 4))
            ],
        })
    return {
        "concept_id": "c1", "title": "t", "target_duration_seconds": 60,
        "structure": beats,
        "editorial": {
            "archetype": "research_progress",
            "narrative_shape": shape or ["hook", "setup", "failure", "retry", "payoff"],
            "hook_type": "unexpected_result",
            "tone": ["energetic"],
            "dialogue_density": "medium",
            "payoff": {"present": payoff,
                       "approximate_story_position": "late" if payoff else "none"},
        },
    }


INVENTORY = {"assets": [
    {"asset_id": f"clip_{i}", "media_type": "video", "duration_seconds": 30}
    for i in range(5)
]}


def _template(**grammar_overrides):
    grammar = {
        "narrative_shape": ["hook", "setup", "failure", "retry", "payoff"],
        "hook_type": "unexpected_result", "tone": ["energetic"],
        "median_shot_seconds": 3.0, "cuts_per_minute": 12.0,
        "broll_ratio": 0.4, "payoff_position": "late",
        "jl_transitions": None, "uses_voiceover": False,
        "caption_style": "minimal",
    }
    grammar.update(grammar_overrides)
    return {
        "schema_version": "style-template.v1", "style_id": "style-test",
        "name": "test", "generated_at": "x", "source_observations": ["obs-1"],
        "confidence": 0.5, "grammar": grammar,
        "requirements": {"needs_payoff": True, "dialogue_density": "medium",
                         "min_distinct_shots": 4, "needs_broll": True},
    }


class TestMatching:
    def test_matching_concept_scores_high_with_reasons(self) -> None:
        match = match_concept(_template(), _concept(), INVENTORY)
        assert match["score"] >= 0.85
        assert match["components"]["payoff_fit"] == 1.0
        assert match["reasons"]

    def test_missing_payoff_is_called_out(self) -> None:
        match = match_concept(_template(), _concept(payoff=False), INVENTORY)
        assert match["components"]["payoff_fit"] == 0.2
        assert any("desenlace" in m for m in match["missing"])

    def test_pacing_infeasible_when_too_few_moments(self) -> None:
        match = match_concept(
            _template(cuts_per_minute=40.0), _concept(evidence_count=4), INVENTORY
        )
        assert match["components"]["pacing_feasibility"] < 0.6
        assert any("momentos" in m for m in match["missing"])

    def test_no_spare_footage_hurts_broll_styles(self) -> None:
        inventory = {"assets": [
            {"asset_id": f"clip_{i}", "media_type": "video",
             "duration_seconds": 30}
            for i in range(3)  # all used by the concept
        ]}
        match = match_concept(_template(), _concept(), inventory)
        assert match["components"]["broll_feasibility"] == 0.3
        assert any("B-roll" in m for m in match["missing"])


class TestGuidance:
    def test_guidance_carries_the_grammar_not_content(self) -> None:
        text = style_guidance(_template())
        assert "hook → setup → failure → retry → payoff" in text
        assert "3s" in text and "12" in text
        assert "40% of screen time" in text or "40%" in text
        assert "never" in text.lower()  # the grounding reminder
