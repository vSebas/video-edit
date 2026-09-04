"""Platform-grounded music discovery: intent, candidate normalization, ranking,
the model fallback provider, and the official Instagram Audio API adapter."""

import json

import pytest

from video_app.music import (
    MusicCandidate,
    MusicIntent,
    ModelMusicProvider,
    build_intent,
    discover,
    rank_candidates,
)
from video_app.music_providers import InstagramMusicProvider


def test_build_intent_grounds_tempo_and_energy_in_measured_style() -> None:
    concept = {"concept_id": "c1", "title": "Robot day",
               "editorial": {"tone": ["energetic", "playful"]}}
    style = {"targets": {"bpm_estimate": 120, "cuts_per_minute": 45}}
    intent = build_intent(concept, style, 62.0, platform_targets=["instagram"])
    assert intent.bpm_preferred == 120
    assert intent.bpm_range == (108, 132)
    assert intent.energy == "high"          # tone + high cpm
    assert "energetic" in intent.mood
    assert intent.platform_targets == ["instagram"]


def test_build_intent_survives_missing_style_and_concept() -> None:
    intent = build_intent(None, None, 30.0)
    assert intent.bpm_preferred is None
    assert intent.energy is None
    assert intent.mood == []


def test_candidate_to_recommended_late_bound_only_with_platform_id() -> None:
    platform = MusicCandidate(
        provider="instagram", platform="instagram", platform_audio_id="A1",
        title="Sunset", artist="Petit Biscuit", bpm=95, energy="medium")
    rec = platform.to_recommended()
    assert rec["late_bound"] is True
    assert rec["platform_audio_id"] == "A1"
    assert rec["name"] == "Sunset — Petit Biscuit"

    model = MusicCandidate(provider="model", title="Some Song", artist="X", bpm=100)
    rec2 = model.to_recommended()
    assert "late_bound" not in rec2 or rec2.get("late_bound") is False


def test_rank_prefers_bpm_energy_and_drops_unusable() -> None:
    intent = MusicIntent(bpm_preferred=120, energy="high")
    good = MusicCandidate(provider="p", title="good", bpm=118, energy="high",
                          account_usable=True, trend_state="rising")
    off = MusicCandidate(provider="p", title="off", bpm=70, energy="low",
                         account_usable=True)
    unusable = MusicCandidate(provider="p", title="nope", bpm=120, energy="high",
                              account_usable=False)
    ranked = rank_candidates(intent, [off, unusable, good])
    assert [c.title for c in ranked] == ["good", "off"]  # unusable dropped


def test_model_provider_parses_candidates() -> None:
    payload = {"candidates": [
        {"title": "Track A", "artist": "AA", "vibe": "chill", "bpm": 95, "energy": "medium"},
        {"title": "Track B", "artist": "BB", "vibe": "up", "bpm": 128, "energy": "high"},
    ]}
    provider = ModelMusicProvider(lambda messages: json.dumps(payload))
    intent = MusicIntent(title="t", duration_seconds=30, energy="medium")
    out = provider.search_audio(intent)
    assert [c.title for c in out] == ["Track A", "Track B"]
    assert out[0].provenance == "model_knowledge"
    assert out[1].bpm == 128


def test_model_provider_tolerates_fenced_json() -> None:
    fenced = "```json\n{\"candidates\":[{\"title\":\"X\",\"artist\":\"Y\"}]}\n```"
    provider = ModelMusicProvider(lambda messages: fenced)
    out = provider.search_audio(MusicIntent())
    assert out[0].title == "X"


def test_discover_falls_back_to_model_when_platform_unavailable() -> None:
    payload = {"candidates": [{"title": "Fallback", "artist": "Z", "bpm": 100}]}
    ig = InstagramMusicProvider(env={})          # no token -> unavailable
    model = ModelMusicProvider(lambda messages: json.dumps(payload))
    candidates, source = discover(MusicIntent(energy="medium"), [ig, model])
    assert source == "model"
    assert candidates[0].title == "Fallback"


def test_instagram_provider_disabled_without_token() -> None:
    assert InstagramMusicProvider(env={}).available() is False
    assert InstagramMusicProvider(
        env={"INSTAGRAM_ACCESS_TOKEN": "tok"}).available() is True


def test_instagram_provider_normalizes_official_fields() -> None:
    ig = InstagramMusicProvider(env={"INSTAGRAM_ACCESS_TOKEN": "tok"})
    cand = ig._normalize(
        {"id": "889", "title": "Boomin", "artist_name": "Metro", "duration_ms": 30000},
        trending=True)
    assert cand.platform_audio_id == "889"
    assert cand.artist == "Metro"
    assert cand.duration_seconds == 30.0
    assert cand.account_usable is True
    assert cand.trend_state == "rising"
    assert cand.provenance == "instagram_audio_api"
