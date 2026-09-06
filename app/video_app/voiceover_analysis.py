"""Voiceover-driven editing — the DETERMINISTIC backbone (`voiceover-analysis.v1`).

The tool treats a recorded voiceover as an editing SIGNAL that CHECKS the cut: it
segments the narration into beats and, per beat, asks whether the footage under it
(and in the pool) supports what is being said. Design (owner + Codex, 2026-09-06):

- The voiceover proves the creator's INTENT and exact words, NOT that the footage
  depicts them. Coverage is checked against approved footage evidence; footage is
  never invented to match narration; an unsupported beat is a flagged GAP.
- Three questions stay SEPARATE per beat: does matching footage exist / is it
  currently visible / does it substantiate the narration. Semantic support is a
  MODEL suggestion over an ENUMERATED approved-evidence set, and the class is only
  trusted when it is COHERENT with a real approved evidence id (current_match
  needs a currently-VISIBLE id; available_elsewhere needs a non-visible one).
- Timebase-gated pipeline: A0 raw *provisional* preflight -> B filler cleanup (or
  explicit skip) freezes the timebase -> A1 canonical report + actionable remedies
  -> C atomic voiceover-led retime.
- Every stage binds to immutable basis ids (source-word ids, VO content+ASR hash,
  the exact enumerated evidence records, plan revision) so a re-record, a caption
  edit, an approval change, or a plan change deterministically invalidates it.

This module owns the pure/deterministic parts and is unit-tested; the model call
and the plan/OpenTake mutations live in the service layer.
"""

from __future__ import annotations

import hashlib
import re

SCHEMA_VERSION = "voiceover-analysis.v1"
# Independent of the schema version — bump when the CLASSIFIER prompt changes so
# stale detection fires even if the schema is unchanged.
PROMPT_VERSION = "vo-classify-prompt-v1"

# A beat boundary falls at a real pause between words or at sentence-ending
# punctuation. 0.6s is a natural clause gap in speech.
DEFAULT_PAUSE_SECONDS = 0.6
# Dead-air preview is conservative — only clearly unnatural silence, well above a
# clause gap. Real filler-word detection is phase B (the reviewed cleanup policy);
# A0 must not present ordinary conjunctions/pronouns as cleanup material.
DEADAIR_SECONDS = 1.2
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»)]*\s*$")

BEAT_CLASSES = (
    "current_match",        # a currently-VISIBLE approved observation depicts it
    "available_elsewhere",  # an approved observation depicts it but isn't shown here
    "ambiguous",            # several plausible / conflicting matches
    "gap",                  # nothing in the (complete) pool depicts it
    "nonvisual",            # connective/emotional narration needing no literal shot
    "unknown",              # pool was truncated — cannot assert a gap (A0 honesty)
)
REMEDIES = ("none", "extend", "pull", "record")


def assign_word_ids(words: list[dict]) -> list[dict]:
    """Stamp each RAW recording word with an immutable id (its position in the
    full source transcript, which never renumbers — filler cleanup removes from
    the TIMELINE, not from this source list). Beats and matches reference these
    ids, so they survive cleanup and can be revalidated later."""
    out = []
    for i, word in enumerate(words):
        try:
            start = float(word["start_seconds"])
            end = float(word["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "id": f"w{i:04d}",
            "word": str(word.get("word", "")),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
        })
    return out


def segment_beats(
    words: list[dict], pause_seconds: float = DEFAULT_PAUSE_SECONDS
) -> list[dict]:
    """Group id-stamped words (VOICEOVER SOURCE time) into beats at pauses and
    sentence ends. Each beat carries the IMMUTABLE word ids it spans, so a match
    cached against those ids survives filler cleanup regrouping the beats.

    `words`: output of `assign_word_ids` — [{id, word, start_seconds, end_seconds}].
    Returns: [{beat_id, source_start_seconds, source_end_seconds, text, word_ids}].
    """
    beats: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        toks = [str(w.get("word", "")).strip() for w in current]
        text = re.sub(r"\s+([.,!?;:])", r"\1", " ".join(t for t in toks if t)).strip()
        beats.append({
            "beat_id": f"b{len(beats) + 1:03d}",
            "source_start_seconds": round(float(current[0]["start_seconds"]), 3),
            "source_end_seconds": round(float(current[-1]["end_seconds"]), 3),
            "text": text,
            "word_ids": [w["id"] for w in current],
        })

    prev_end: float | None = None
    for word in words:
        start = float(word["start_seconds"])
        if prev_end is not None and start - prev_end >= pause_seconds and current:
            flush()
            current = []
        current.append(word)
        prev_end = float(word["end_seconds"])
        if _SENTENCE_END.search(str(word.get("word", ""))):
            flush()
            current = []
            prev_end = None
    flush()
    return beats


def detect_deadair(words: list[dict], threshold: float = DEADAIR_SECONDS) -> list[dict]:
    """Conservative dead-air preview for A0 — only clearly unnatural silences,
    with immutable neighbour word ids. NOT filler-word detection (that is the
    reviewed phase-B policy); A0 never proposes cutting ordinary words."""
    out: list[dict] = []
    for a, b in zip(words, words[1:]):
        gap = float(b["start_seconds"]) - float(a["end_seconds"])
        if gap >= threshold:
            out.append({
                "kind": "pause",
                "start_seconds": round(float(a["end_seconds"]), 3),
                "end_seconds": round(float(b["start_seconds"]), 3),
                "after_word_id": a["id"],
                "before_word_id": b["id"],
            })
    return out


def voiceover_filler_candidates(words: list[dict]) -> list[dict]:
    """Filler + dead-air SOURCE ranges in the voiceover, using the SAME
    conservative policy as dialogue cleanup (`cleanup.PURE_FILLERS` /
    `GAP_FILLERS` + thresholds) — so ordinary conjunctions/pronouns are never
    flagged. Phase B (the reviewed VO-lane cut) consumes these; A0 only previews
    dead-air. Each candidate is a removable [source_start, source_end] with a
    reason and neighbour word ids for stable review."""
    from .cleanup import (
        DEAD_AIR_KEEP, DEAD_AIR_MIN, GAP_FILLERS, HESITATION_GAP, PURE_FILLERS,
        WORD_PAD,
    )

    def norm(w: dict) -> str:
        return re.sub(r"[^\wáéíóúñü]", "", str(w.get("word", "")).lower())

    out: list[dict] = []
    n = len(words)
    for i, word in enumerate(words):
        tok = norm(word)
        nxt = norm(words[i + 1]) if i + 1 < n else ""
        gap_after = (float(words[i + 1]["start_seconds"]) - float(word["end_seconds"])
                     if i + 1 < n else 0.0)
        reason = None
        span = (float(word["start_seconds"]), float(word["end_seconds"]))
        word_ids = [word["id"]]
        if tok in PURE_FILLERS:
            reason = f"muletilla «{tok}»"
        elif tok in GAP_FILLERS and gap_after >= HESITATION_GAP:
            reason = f"muletilla «{tok}» + pausa {gap_after:.2f}s"
        elif f"{tok} {nxt}" in GAP_FILLERS and i + 1 < n:
            next_gap = (float(words[i + 2]["start_seconds"]) - float(words[i + 1]["end_seconds"])
                        if i + 2 < n else 0.0)
            if next_gap >= HESITATION_GAP:
                reason = f"muletilla «{tok} {nxt}» + pausa {next_gap:.2f}s"
                span = (float(word["start_seconds"]), float(words[i + 1]["end_seconds"]))
                word_ids = [word["id"], words[i + 1]["id"]]
        if reason:
            out.append({
                "kind": "filler", "reason": reason,
                "source_start_seconds": round(max(0.0, span[0] - WORD_PAD), 3),
                "source_end_seconds": round(span[1] + WORD_PAD, 3),
                "word_ids": word_ids,
                "context": " ".join(norm(x) for x in words[max(0, i - 2):i + 3]),
            })
    # Dead air: silences longer than DEAD_AIR_MIN, keeping DEAD_AIR_KEEP of breath.
    for a, b in zip(words, words[1:]):
        gap = float(b["start_seconds"]) - float(a["end_seconds"])
        if gap >= DEAD_AIR_MIN:
            cut_start = float(a["end_seconds"]) + DEAD_AIR_KEEP / 2
            cut_end = float(b["start_seconds"]) - DEAD_AIR_KEEP / 2
            if cut_end > cut_start:
                out.append({
                    "kind": "dead_air", "reason": f"silencio {gap:.1f}s",
                    "source_start_seconds": round(cut_start, 3),
                    "source_end_seconds": round(cut_end, 3),
                    "word_ids": [a["id"], b["id"]], "context": "",
                })
    return sorted(out, key=lambda c: c["source_start_seconds"])


def compact_voiceover_segments(
    src_start: float, src_end: float, remove: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Given the VO's analysed [src_start, src_end] and the source ranges to
    REMOVE, return the KEPT source segments (in order). Overlapping/adjacent
    removals are merged; the picture is untouched, so the caller compacts these
    onto the voiceover lane back-to-back (phase B)."""
    merged: list[list[float]] = []
    for r0, r1 in sorted((max(src_start, a), min(src_end, b)) for a, b in remove):
        if r1 <= r0:
            continue
        if merged and r0 <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], r1)
        else:
            merged.append([r0, r1])
    kept: list[tuple[float, float]] = []
    cursor = src_start
    for r0, r1 in merged:
        if r0 > cursor + 1e-6:
            kept.append((round(cursor, 3), round(r0, 3)))
        cursor = max(cursor, r1)
    if src_end > cursor + 1e-6:
        kept.append((round(cursor, 3), round(src_end, 3)))
    return kept


def beat_timeline_window(
    beat: dict, event_source_start: float, event_timeline_start: float,
    playback_rate: float = 1.0,
) -> tuple[float, float]:
    """Map a beat's VOICEOVER-SOURCE range onto the TIMELINE. Voiceovers play at
    1x; the caller FAILS CLOSED for any other rate (the schema permits it), so
    this never emits confidently-wrong timecodes."""
    if abs(float(playback_rate) - 1.0) > 1e-6:
        raise ValueError("voiceover playback_rate must be 1.0 to map beats")
    offset = event_timeline_start - float(event_source_start or 0.0)
    return (
        round(beat["source_start_seconds"] + offset, 3),
        round(beat["source_end_seconds"] + offset, 3),
    )


def scenes_under(window: tuple[float, float], video_events: list[dict]) -> list[dict]:
    """Video events overlapping a timeline window."""
    start, end = window
    covering = []
    for event in video_events:
        ev_start = float(event.get("timeline_start_seconds", 0) or 0)
        ev_end = ev_start + float(event.get("duration_seconds", 0) or 0)
        if ev_start < end and ev_end > start:
            covering.append(event)
    return covering


def evidence_fingerprint(records: list[dict]) -> str:
    """Stable id for the EXACT enumerated evidence set actually shown to the
    model — id, asset, range, caption and modality — so an approval change OR a
    caption/range edit deterministically invalidates cached classifications."""
    payload = "|".join(sorted(
        f"{r.get('evidence_id')};{r.get('asset_id')};{r.get('start_seconds')};"
        f"{r.get('end_seconds')};{r.get('evidence_type')};{(r.get('caption') or '')[:200]}"
        for r in records
    ))
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _interval_subtract(
    base: tuple[float, float], holes: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """`base` minus the union of `holes` — the sub-intervals that remain."""
    result = [base]
    for h0, h1 in holes:
        nxt = []
        for lo, hi in result:
            if h1 <= lo or h0 >= hi:
                nxt.append((lo, hi))
                continue
            if lo < h0:
                nxt.append((lo, h0))
            if h1 < hi:
                nxt.append((h1, hi))
        result = nxt
    return result


def beat_visible_source_spans(
    window: tuple[float, float],
    primary_events: list[dict],
    broll_events: list[dict],
) -> list[tuple[str, float, float]]:
    """The (asset, source_lo, source_hi) spans ACTUALLY on screen during a beat
    window: B-roll where it overlaps (it sits on top), and the primary only in
    the sub-intervals B-roll does NOT cover — so PARTIAL occlusion is handled,
    not just full (Codex review 2026-09-06). Evidence is visible only if it
    overlaps one of these exact spans."""
    spans: list[tuple[str, float, float]] = []
    broll_windows: list[tuple[float, float]] = []
    for ev in broll_events:
        tl0 = max(window[0], float(ev.get("timeline_start_seconds", 0) or 0))
        tl1 = min(window[1], float(ev.get("timeline_start_seconds", 0) or 0)
                  + float(ev.get("duration_seconds", 0) or 0))
        if tl1 > tl0:
            broll_windows.append((tl0, tl1))
            sl = displayed_source_slice(ev, (tl0, tl1))
            if sl:
                spans.append((ev.get("asset_id"), sl[0], sl[1]))
    for ev in primary_events:
        tl0 = max(window[0], float(ev.get("timeline_start_seconds", 0) or 0))
        tl1 = min(window[1], float(ev.get("timeline_start_seconds", 0) or 0)
                  + float(ev.get("duration_seconds", 0) or 0))
        if tl1 <= tl0:
            continue
        for lo, hi in _interval_subtract((tl0, tl1), broll_windows):
            sl = displayed_source_slice(ev, (lo, hi))
            if sl:
                spans.append((ev.get("asset_id"), sl[0], sl[1]))
    return spans


def voiceover_fingerprint(
    asset_sha256: str, source_start: float, source_end: float,
    asr_model: str, asr_version: str, words: list[dict],
) -> str:
    """Immutable identity of the analysed voiceover material — content hash, the
    analysed source range, the ACTUAL ASR model, and a hash of the WORD RECORDS
    (id + token + timestamps, since the timestamps determine beats, mapping and
    dead-air — not just the text). A re-record, a source trim, a different
    Whisper model, or shifted word timings all invalidate it."""
    wtext = "|".join(
        f"{w.get('id')};{w.get('word')};{w.get('start_seconds')};{w.get('end_seconds')}"
        for w in words
    )
    wh = hashlib.sha1(wtext.encode()).hexdigest()[:16]
    key = (f"{asset_sha256}|{source_start:.3f}|{source_end:.3f}"
           f"|{asr_model}|{asr_version}|{wh}")
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def displayed_source_slice(
    event: dict, window: tuple[float, float]
) -> tuple[float, float] | None:
    """The SOURCE sub-range of a video event actually shown during a beat's
    timeline window (rate 1.0). Evidence must overlap THIS slice, not the whole
    clip source range — a beat over source 9-10 isn't supported by evidence at
    source 0-1 of the same clip (Codex review 2026-09-06)."""
    ev_start = float(event.get("timeline_start_seconds", 0) or 0)
    ev_end = ev_start + float(event.get("duration_seconds", 0) or 0)
    iw0 = max(window[0], ev_start)
    iw1 = min(window[1], ev_end)
    if iw1 <= iw0:
        return None
    src0 = float(event.get("source_start_seconds", 0) or 0)
    return (src0 + (iw0 - ev_start), src0 + (iw1 - ev_start))


def covers(events: list[dict], window: tuple[float, float]) -> bool:
    """Do these timeline events jointly cover the whole window (used for B-roll
    occlusion — a covered primary scene is not visible)?"""
    cursor = window[0]
    for ev in sorted(events, key=lambda e: float(e.get("timeline_start_seconds", 0) or 0)):
        s = float(ev.get("timeline_start_seconds", 0) or 0)
        e = s + float(ev.get("duration_seconds", 0) or 0)
        if s <= cursor + 1e-6:
            cursor = max(cursor, e)
        if cursor >= window[1] - 1e-6:
            return True
    return cursor >= window[1] - 1e-6
