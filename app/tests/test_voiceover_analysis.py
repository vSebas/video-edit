"""Deterministic backbone of voiceover-analysis.v1: word-id stamping, beat
segmentation, timeline mapping (fail-closed on rate), scene overlap, dead-air
preview, and the invalidation fingerprints."""

import pytest

from video_app.voiceover_analysis import (
    assign_word_ids,
    beat_timeline_window,
    beat_visible_source_spans,
    detect_deadair,
    evidence_fingerprint,
    scenes_under,
    segment_beats,
    voiceover_fingerprint,
)


def _w(word, s, e):
    return {"word": word, "start_seconds": s, "end_seconds": e}


def test_word_ids_are_stable_positions():
    words = assign_word_ids([_w("a", 0.0, 0.2), {"bad": 1}, _w("b", 0.3, 0.5)])
    # malformed word dropped, but ids reflect ORIGINAL position (immutable — a
    # gap is fine; ids only need to be unique and stable across re-analysis).
    assert [w["id"] for w in words] == ["w0000", "w0002"]
    assert [w["word"] for w in words] == ["a", "b"]


def test_segment_beats_splits_on_pause_and_sentence_end_and_carries_word_ids():
    words = assign_word_ids([
        _w("Hoy", 0.0, 0.3), _w("fui", 0.3, 0.6), _w("a", 0.6, 0.7),
        _w("Stanford.", 0.7, 1.2),          # sentence end -> boundary
        _w("Luego", 3.0, 3.4),              # 1.8s pause -> boundary before this
        _w("tomé", 3.4, 3.7), _w("café", 3.7, 4.1),
    ])
    beats = segment_beats(words, pause_seconds=0.6)
    assert len(beats) == 2
    assert beats[0]["text"] == "Hoy fui a Stanford."
    assert beats[0]["word_ids"] == ["w0000", "w0001", "w0002", "w0003"]
    assert beats[0]["source_start_seconds"] == 0.0
    assert beats[0]["source_end_seconds"] == 1.2
    assert beats[1]["text"] == "Luego tomé café"
    assert beats[1]["word_ids"] == ["w0004", "w0005", "w0006"]
    assert [b["beat_id"] for b in beats] == ["b001", "b002"]


def test_beat_timeline_window_maps_and_fails_closed_on_rate():
    beat = {"source_start_seconds": 2.0, "source_end_seconds": 5.0}
    assert beat_timeline_window(beat, 0.0, 10.0) == (12.0, 15.0)
    assert beat_timeline_window(beat, 1.0, 4.0) == (5.0, 8.0)
    with pytest.raises(ValueError):
        beat_timeline_window(beat, 0.0, 10.0, playback_rate=1.5)


def test_scenes_under_returns_only_overlapping_events():
    events = [
        {"event_id": "v1", "timeline_start_seconds": 0.0, "duration_seconds": 4.0},
        {"event_id": "v2", "timeline_start_seconds": 4.0, "duration_seconds": 4.0},
        {"event_id": "v3", "timeline_start_seconds": 8.0, "duration_seconds": 4.0},
    ]
    assert [e["event_id"] for e in scenes_under((3.0, 5.0), events)] == ["v1", "v2"]
    assert [e["event_id"] for e in scenes_under((8.5, 9.0), events)] == ["v3"]


def test_detect_deadair_only_flags_long_silence_with_neighbour_ids():
    words = assign_word_ids([
        _w("uno", 0.0, 0.4), _w("dos", 0.9, 1.2),   # 0.5s gap — NOT dead air
        _w("tres", 3.0, 3.4),                        # 1.8s gap — dead air
    ])
    found = detect_deadair(words, threshold=1.2)
    assert len(found) == 1
    assert found[0]["kind"] == "pause"
    assert found[0]["after_word_id"] == "w0001" and found[0]["before_word_id"] == "w0002"


def test_fingerprints_are_stable_and_sensitive():
    recs = [{"evidence_id": "e1", "asset_id": "a", "start_seconds": 0.0,
             "end_seconds": 5.0, "evidence_type": "visual", "caption": "coffee"}]
    fp = evidence_fingerprint(recs)
    assert fp == evidence_fingerprint(list(recs))
    # editing the caption (semantic input) changes the fingerprint
    recs2 = [{**recs[0], "caption": "tea"}]
    assert evidence_fingerprint(recs2) != fp
    # ...and so does the range
    recs3 = [{**recs[0], "end_seconds": 6.0}]
    assert evidence_fingerprint(recs3) != fp

    wa = [{"id": "w0", "word": "hola", "start_seconds": 0.0, "end_seconds": 0.4}]
    wb = [{"id": "w0", "word": "hola", "start_seconds": 0.0, "end_seconds": 0.9}]  # timing
    m, v = "whisper:large-v3:cuda", "local-asr-v1"
    base = voiceover_fingerprint("sha", 0.0, 6.0, m, v, wa)
    assert base == voiceover_fingerprint("sha", 0.0, 6.0, m, v, list(wa))
    assert base != voiceover_fingerprint("sha", 0.0, 6.0, "whisper:small:cpu", v, wa)  # model
    assert base != voiceover_fingerprint("sha", 0.0, 6.0, m, "v2", wa)                 # asr version
    assert base != voiceover_fingerprint("sha", 0.0, 6.0, m, v, wb)                    # word timing
    assert base != voiceover_fingerprint("sha2", 0.0, 6.0, m, v, wa)                   # content


def test_beat_visible_spans_handle_partial_broll_occlusion():
    # window 0-2s; primary clip (source 0-2) with B-roll covering timeline 0-1.
    primary = [{"asset_id": "A", "timeline_start_seconds": 0.0,
                "duration_seconds": 2.0, "source_start_seconds": 0.0}]
    broll = [{"asset_id": "B", "timeline_start_seconds": 0.0,
              "duration_seconds": 1.0, "source_start_seconds": 5.0}]
    spans = beat_visible_source_spans((0.0, 2.0), primary, broll)
    # B-roll visible over source 5-6; primary visible ONLY over source 1-2 (0-1
    # is occluded).
    assert ("B", 5.0, 6.0) in spans
    assert ("A", 1.0, 2.0) in spans
    assert ("A", 0.0, 1.0) not in spans   # occluded slice not visible
