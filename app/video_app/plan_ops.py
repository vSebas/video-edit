"""Atomic natural-language plan edits (P4).

One instruction → ONE operation from a closed set → deterministic
application to the canonical plan as a new revision. The LLM only ever
chooses an operation and its arguments; every mutation is computed and
bounds-checked here, so a hallucinated argument fails closed instead of
corrupting the timeline.

Ops: delete_event, trim_event, set_volume, jl_cut, set_title. The model may
also answer reject (with a reason shown to the user) when the instruction
does not map cleanly onto exactly one op.
"""

from __future__ import annotations

import json
from copy import deepcopy

from .providers import parse_json_content

MIN_EVENT_SECONDS = 0.2


class PlanOpError(RuntimeError):
    pass


def _primary_tracks(plan: dict) -> tuple[dict, dict]:
    video = next(
        t for t in plan["tracks"]
        if t["kind"] == "video" and t.get("role") in (None, "primary")
    )
    # Role-based: voiceover and music tracks also have kind 'audio', so pick
    # the primary explicitly rather than "the first audio track".
    audio = next(
        t for t in plan["tracks"]
        if t["kind"] == "audio" and t.get("role") in (None, "primary")
    )
    return video, audio


def _broll_events(plan: dict) -> list[dict]:
    videos = [t for t in plan["tracks"] if t["kind"] == "video"]
    if len(videos) == 2 and videos[1].get("role") == "broll":
        return videos[1]["events"]
    return []


def _title_events(plan: dict) -> list[dict]:
    title = next((t for t in plan["tracks"] if t["kind"] == "title"), None)
    return title["events"] if title else []


def _mirrored(video: list[dict], audio: list[dict]) -> bool:
    return len(video) == len(audio) and all(
        v["asset_id"] == a["asset_id"]
        and v["source_start_seconds"] == a["source_start_seconds"]
        and v["timeline_start_seconds"] == a["timeline_start_seconds"]
        and v["duration_seconds"] == a["duration_seconds"]
        for v, a in zip(video, audio)
    )


def _require_mirrored(plan: dict) -> tuple[list[dict], list[dict]]:
    video, audio = _primary_tracks(plan)
    if not _mirrored(video["events"], audio["events"]):
        raise PlanOpError(
            "This plan already has J/L cuts; structural edits need the "
            "mirrored revision (revert or re-place first)"
        )
    return video["events"], audio["events"]


def _index_of(events: list[dict], event_id: str) -> int:
    for index, event in enumerate(events):
        if event["event_id"] == event_id:
            return index
    raise PlanOpError(f"No event {event_id!r} in the plan")


def _r(value: float) -> float:
    return round(value, 6)


def _grid(value: float, fps: float) -> float:
    """Quantize seconds to the plan's frame grid — atomic edits must never
    introduce fractional-frame boundaries (cross-review 8)."""
    return round(round(value * fps) / fps, 6)


def _op_number(op: dict, key: str) -> float:
    try:
        value = float(op[key])
    except (TypeError, ValueError) as exc:
        raise PlanOpError(f"{key} must be a number") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise PlanOpError(f"{key} must be finite")
    return value


def _caption_events(plan: dict) -> list[dict]:
    cap = next((t for t in plan["tracks"] if t.get("kind") == "caption"), None)
    return cap["events"] if cap else []


def _music_events(plan: dict) -> list[dict]:
    mus = next(
        (t for t in plan["tracks"]
         if t.get("kind") == "audio" and t.get("role") == "music"),
        None,
    )
    return mus["events"] if mus else []


def _ripple(plan: dict, from_seconds: float, delta: float) -> None:
    """Shift everything at/after a timeline position; overlays, titles and
    captions ride along so they stay glued to their content."""
    video, audio = _primary_tracks(plan)
    voiceover = _voiceover_track(plan)
    for events in (
        video["events"], audio["events"], _broll_events(plan),
        voiceover["events"] if voiceover else [],
    ):
        for event in events:
            if event["timeline_start_seconds"] >= from_seconds - 1e-6:
                event["timeline_start_seconds"] = _r(
                    event["timeline_start_seconds"] + delta
                )
    # Titles and captions are timeline-anchored cues, not clips: a caption left
    # in place while its scene moves would end up over the wrong footage.
    for event in _title_events(plan) + _caption_events(plan):
        if event["timeline_start_seconds"] >= from_seconds - 1e-6:
            event["timeline_start_seconds"] = _r(
                max(0.0, event["timeline_start_seconds"] + delta)
            )
    plan["project"]["duration_seconds"] = _r(
        plan["project"]["duration_seconds"] + delta
    )
    # The music bed spans the whole cut (it starts at 0, so the shift above
    # never touches it); re-fit it to the new duration. A looping bed keeps its
    # source range (the asset segment it loops) and only its timeline span
    # changes; a one-shot bed plays min(itself, the cut).
    new_duration = plan["project"]["duration_seconds"]
    for event in _music_events(plan):
        if event["timeline_start_seconds"] > 1e-6:
            continue
        music = event.get("music") or {}
        bed = music.get("bed") or {}
        if music.get("mode") != "bed" or bed.get("loop", True):
            event["duration_seconds"] = _r(new_duration)
        else:
            span = _r(min(event["duration_seconds"], new_duration))
            event["duration_seconds"] = span
            if event.get("source_end_seconds") is not None:
                event["source_end_seconds"] = span


def _check_overlays_fit(plan: dict) -> None:
    video, _ = _primary_tracks(plan)
    primary_end = max(
        (e["timeline_start_seconds"] + e["duration_seconds"]
         for e in video["events"]), default=0.0,
    )
    for event in _broll_events(plan):
        if (event["timeline_start_seconds"] + event["duration_seconds"]
                > primary_end + 0.05):
            raise PlanOpError(
                f"This edit would push B-roll {event['event_id']} past the "
                "end of the video — remove or move that overlay first"
            )
    voiceover = _voiceover_track(plan)
    for event in voiceover["events"] if voiceover else []:
        if (event["timeline_start_seconds"] + event["duration_seconds"]
                > primary_end + 0.05):
            raise PlanOpError(
                f"This edit would push voiceover {event['event_id']} past "
                "the end of the video — remove it first"
            )
    for label, events in (
        ("B-roll", _broll_events(plan)),
        ("voiceover", voiceover["events"] if voiceover else []),
    ):
        ordered = sorted(events, key=lambda e: e["timeline_start_seconds"])
        for previous, current in zip(ordered, ordered[1:]):
            if (current["timeline_start_seconds"]
                    < previous["timeline_start_seconds"]
                    + previous["duration_seconds"] - 1e-6):
                raise PlanOpError(
                    f"This edit would overlap {label} events "
                    f"{previous['event_id']} and {current['event_id']} — "
                    "remove or move one first"
                )


def _drop_captions_between(plan: dict, lo: float, hi: float) -> None:
    """Remove caption cues whose span falls in [lo, hi) — the timeline region
    whose footage a delete/trim removed. A cue left behind there would end up
    over the wrong scene once the ripple closes the gap. Uses pre-ripple
    coordinates, so call it BEFORE rippling."""
    cap = next((t for t in plan["tracks"] if t.get("kind") == "caption"), None)
    if cap is None:
        return
    kept = []
    for e in cap["events"]:
        mid = e["timeline_start_seconds"] + e["duration_seconds"] / 2
        if lo - 1e-6 <= mid < hi - 1e-6:
            continue
        kept.append(e)
    cap["events"] = kept


def _apply_delete(plan: dict, op: dict, assets: dict) -> str:
    video_events, audio_events = _require_mirrored(plan)
    index = _index_of(video_events, op["event_id"])
    removed = video_events.pop(index)
    audio_events.pop(index)
    start = removed["timeline_start_seconds"]
    length = removed["duration_seconds"]
    # The deleted scene's captions go with it — before the ripple slides the
    # rest forward over the gap.
    _drop_captions_between(plan, start, start + length)
    _ripple(plan, start + length, -length)
    _check_overlays_fit(plan)
    return (
        f"Eliminada la escena {removed['event_id']} "
        f"({length:.1f}s); todo lo posterior se adelanta"
    )


def _apply_trim(plan: dict, op: dict, assets: dict) -> str:
    video_events, audio_events = _require_mirrored(plan)
    index = _index_of(video_events, op["event_id"])
    video = video_events[index]
    audio = audio_events[index]
    fps = plan["project"]["fps"]
    seconds = _grid(_op_number(op, "seconds"), fps)
    if not 0 < seconds <= 60:
        raise PlanOpError("Trim seconds must be within (0, 60]")
    edge, direction = op["edge"], op["direction"]
    delta = seconds if direction == "extend" else -seconds
    new_duration = video["duration_seconds"] + delta
    if new_duration < MIN_EVENT_SECONDS:
        raise PlanOpError(
            f"{video['event_id']} would shrink below {MIN_EVENT_SECONDS}s"
        )
    old_start = video["timeline_start_seconds"]
    old_end = old_start + video["duration_seconds"]
    new_end = old_start + new_duration
    for event in (video, audio):
        if edge == "start":
            new_source_start = event["source_start_seconds"] - delta
            if new_source_start < 0:
                raise PlanOpError(
                    f"{video['event_id']} has no source material before "
                    f"{event['source_start_seconds']:.2f}s to extend into"
                )
            event["source_start_seconds"] = _r(new_source_start)
        else:
            new_source_end = event["source_end_seconds"] + delta
            asset = assets.get(event["asset_id"]) or {}
            available = float(asset.get("duration_seconds") or 0.0)
            if available and new_source_end > available + 0.05:
                raise PlanOpError(
                    f"{video['event_id']} has no source material after "
                    f"{event['source_end_seconds']:.2f}s to extend into"
                )
            event["source_end_seconds"] = _r(new_source_end)
        event["duration_seconds"] = _r(new_duration)
    # Captions that no longer cover the same footage must go (a caption cannot
    # be reliably remapped without ASR). Start-edge: the clip's whole content
    # shifts, so drop all of this clip's captions. End-shorten: drop only the
    # removed tail [new_end, old_end). End-extend adds fresh footage (no
    # captions yet) and the following scene's captions merely ripple forward —
    # dropping anything there would wrongly delete the next scene's captions.
    if edge == "start":
        _drop_captions_between(plan, old_start, old_end)
    elif direction == "shorten":
        _drop_captions_between(plan, new_end, old_end)
    _ripple(
        plan,
        video["timeline_start_seconds"] + video["duration_seconds"] - delta + 1e-6,
        delta,
    )
    _check_overlays_fit(plan)
    action = "alargada" if direction == "extend" else "recortada"
    lado = "al inicio" if edge == "start" else "al final"
    return (
        f"Escena {video['event_id']} {action} {seconds:.1f}s {lado}; "
        f"ahora dura {new_duration:.1f}s"
    )


def _apply_volume(plan: dict, op: dict, assets: dict) -> str:
    _, audio_track = _primary_tracks(plan)
    events = audio_track["events"]
    event_id = op["event_id"]
    try:
        index = _index_of(events, event_id)
    except PlanOpError:
        # the model may name the video event of the pair
        video_track, _ = _primary_tracks(plan)
        index = _index_of(video_track["events"], event_id)
        if index >= len(events):
            raise PlanOpError(f"No audio partner for {event_id!r}")
    db = _op_number(op, "volume_db")
    if not -96 <= db <= 12:
        raise PlanOpError("volume_db must be within [-96, 12]")
    events[index]["volume_db"] = db
    label = "silenciado" if db <= -96 else f"a {db:g}dB"
    return f"Audio de {events[index]['event_id']} {label}"


def _apply_jl(plan: dict, op: dict, assets: dict) -> str:
    video_events, audio_events = _require_mirrored(plan)
    index = _index_of(video_events, op["event_id"])
    if index == 0:
        raise PlanOpError("The first scene has nothing before it to J/L into")
    lead = _grid(_op_number(op, "lead_seconds"), plan["project"]["fps"])
    if not 0.1 <= abs(lead) <= 5:
        raise PlanOpError("lead_seconds must be 0.1-5s (either sign)")
    previous = audio_events[index - 1]
    current = audio_events[index]
    if lead > 0:  # J-cut: this scene's audio starts early
        if previous["duration_seconds"] - lead < MIN_EVENT_SECONDS:
            raise PlanOpError("Not enough previous audio to lead into")
        if current["source_start_seconds"] - lead < 0:
            raise PlanOpError(
                f"{current['event_id']} has no source audio before "
                f"{current['source_start_seconds']:.2f}s"
            )
        previous["duration_seconds"] = _r(previous["duration_seconds"] - lead)
        previous["source_end_seconds"] = _r(previous["source_end_seconds"] - lead)
        current["timeline_start_seconds"] = _r(
            current["timeline_start_seconds"] - lead
        )
        current["source_start_seconds"] = _r(
            current["source_start_seconds"] - lead
        )
        current["duration_seconds"] = _r(current["duration_seconds"] + lead)
        kind = "J-cut"
    else:  # L-cut: the previous scene's audio continues under this picture
        tail = -lead
        if current["duration_seconds"] - tail < MIN_EVENT_SECONDS:
            raise PlanOpError("Not enough of this scene's audio to cut into")
        asset = assets.get(previous["asset_id"]) or {}
        available = float(asset.get("duration_seconds") or 0.0)
        if available and previous["source_end_seconds"] + tail > available + 0.05:
            raise PlanOpError(
                f"{previous['event_id']} has no source audio after "
                f"{previous['source_end_seconds']:.2f}s"
            )
        previous["duration_seconds"] = _r(previous["duration_seconds"] + tail)
        previous["source_end_seconds"] = _r(previous["source_end_seconds"] + tail)
        current["timeline_start_seconds"] = _r(
            current["timeline_start_seconds"] + tail
        )
        current["source_start_seconds"] = _r(
            current["source_start_seconds"] + tail
        )
        current["duration_seconds"] = _r(current["duration_seconds"] - tail)
        kind = "L-cut"
    return (
        f"{kind} de {abs(lead):.1f}s en la transición hacia "
        f"{video_events[index]['event_id']} — se aplica en el render y "
        "también al enviar a OpenTake"
    )


TITLE_FONTS = ("sans", "handwritten", "clean", "display")
TITLE_POSITIONS = ("top", "center", "lower")


def _apply_title(plan: dict, op: dict, assets: dict) -> str:
    events = _title_events(plan)
    index = _index_of(events, op["event_id"])
    text = str(op["text"]).strip()
    if not 1 <= len(text) <= 120:
        raise PlanOpError("Title text must be 1-120 characters")
    events[index]["text"] = text
    # a user-typed title is the USER's claim, not the model's — the
    # rendered-language gates exempt it, and provenance records why
    events[index]["user_authored"] = True
    notes = []
    style = dict(events[index].get("text_style") or {})
    if op.get("font") is not None:
        if op["font"] not in TITLE_FONTS:
            raise PlanOpError(
                f"font must be one of {', '.join(TITLE_FONTS)}"
            )
        style["font"] = op["font"]
        notes.append(f"fuente {op['font']}")
    if op.get("size") is not None:
        size = int(op["size"])
        if not 24 <= size <= 140:
            raise PlanOpError("size must be 24-140")
        style["size"] = size
        notes.append(f"tamaño {size}")
    if op.get("position") is not None:
        if op["position"] not in TITLE_POSITIONS:
            raise PlanOpError(
                f"position must be one of {', '.join(TITLE_POSITIONS)}"
            )
        style["position"] = op["position"]
        notes.append({"top": "arriba", "center": "al centro",
                      "lower": "abajo"}[op["position"]])
    if style:
        events[index]["text_style"] = style
    suffix = f" ({', '.join(notes)})" if notes else ""
    return f"Título {events[index]['event_id']} ahora dice: «{text}»{suffix}"


def _voiceover_track(plan: dict, create: bool = False) -> dict | None:
    # Role-based: a music track can also live among the audio tracks, so the
    # voiceover is not necessarily the second one.
    for track in plan["tracks"]:
        if track["kind"] == "audio" and track.get("role") == "voiceover":
            return track
    if create:
        track = {"track_id": "a2", "kind": "audio", "role": "voiceover",
                 "events": []}
        plan["tracks"].append(track)
        return track
    return None


def _apply_add_voiceover(plan: dict, op: dict, assets: dict) -> str:
    asset = assets.get(op["asset_id"])
    if asset is None:
        raise PlanOpError(f"No asset {op['asset_id']!r} in the inventory")
    if asset.get("media_type") != "audio":
        raise PlanOpError(
            f"{op['asset_id']} is not an audio asset — voiceovers come from "
            "recorded audio files in the footage folder"
        )
    start = _grid(_op_number(op, "timeline_start_seconds"), plan["project"]["fps"])
    total = plan["project"]["duration_seconds"]
    if not 0 <= start <= total - 0.5:
        raise PlanOpError(
            f"timeline_start_seconds must be within [0, {total - 0.5:.1f}]"
        )
    available = float(asset.get("duration_seconds") or 0.0)
    if available < 0.5:
        raise PlanOpError(f"{op['asset_id']} is shorter than 0.5s")
    duration = _grid(min(available, total - start), plan["project"]["fps"])
    track = _voiceover_track(plan, create=True)
    used = {e["event_id"] for e in track["events"]}
    number = 1
    while f"vo-{number:02d}" in used:
        number += 1
    event_id = f"vo-{number:02d}"
    for existing in track["events"]:
        a, b = existing["timeline_start_seconds"], (
            existing["timeline_start_seconds"] + existing["duration_seconds"]
        )
        if start < b and start + duration > a:
            raise PlanOpError(
                f"Overlaps voiceover {existing['event_id']} — remove it first"
            )
    track["events"].append({
        "event_id": event_id, "asset_id": op["asset_id"],
        "source_start_seconds": 0.0, "source_end_seconds": duration,
        "timeline_start_seconds": _r(start), "duration_seconds": duration,
        "playback_rate": 1.0, "intent": "voiceover",
        "observed_content": None, "confidence": 1.0, "reframe": None,
        "transition_out": None, "text": None, "volume_db": None,
    })
    return (
        f"Voz en off {event_id} ({op['asset_id']}) desde "
        f"{start:.1f}s, {duration:.1f}s; el audio original baja -9dB "
        "debajo en el render (OpenTake muestra el clip sin el ducking)"
    )


def _apply_remove_voiceover(plan: dict, op: dict, assets: dict) -> str:
    track = _voiceover_track(plan)
    if track is None:
        raise PlanOpError("This plan has no voiceover track")
    index = _index_of(track["events"], op["event_id"])
    removed = track["events"].pop(index)
    if not track["events"]:
        # an empty A2 would place fine but be rejected by sync later
        plan["tracks"].remove(track)
    return f"Voz en off {removed['event_id']} eliminada"


def _broll_track(plan: dict, create: bool = False) -> dict | None:
    videos = [t for t in plan["tracks"] if t["kind"] == "video"]
    if len(videos) == 2 and videos[1].get("role") == "broll":
        return videos[1]
    if create:
        track = {"track_id": "v2", "kind": "video", "role": "broll",
                 "events": []}
        plan["tracks"].append(track)
        return track
    return None


def _primary_end(plan: dict) -> float:
    video, _ = _primary_tracks(plan)
    return max(
        (e["timeline_start_seconds"] + e["duration_seconds"]
         for e in video["events"]), default=0.0,
    )


def _video_asset(assets: dict, asset_id: str) -> dict:
    asset = assets.get(asset_id)
    if asset is None:
        raise PlanOpError(f"No asset {asset_id!r} in the inventory")
    if asset.get("media_type") != "video":
        raise PlanOpError(f"{asset_id} is not a video asset")
    return asset


def _new_broll_event(plan, asset_id, source_start, timeline_start, duration):
    track = _broll_track(plan, create=True)
    used = {e["event_id"] for e in track["events"]}
    number = 1
    while f"bro-{number:02d}" in used:
        number += 1
    event = {
        "event_id": f"bro-{number:02d}", "asset_id": asset_id,
        "source_start_seconds": source_start,
        "source_end_seconds": round(source_start + duration, 6),
        "timeline_start_seconds": timeline_start,
        "duration_seconds": duration, "playback_rate": 1.0,
        "intent": "b-roll", "observed_content": None, "confidence": 0.5,
        "reframe": None, "transition_out": None, "text": None,
        "volume_db": None,
    }
    track["events"].append(event)
    track["events"].sort(key=lambda e: e["timeline_start_seconds"])
    return event


def _apply_add_broll(plan: dict, op: dict, assets: dict) -> str:
    fps = plan["project"]["fps"]
    asset = _video_asset(assets, op["asset_id"])
    start = _grid(_op_number(op, "timeline_start_seconds"), fps)
    end = _primary_end(plan)
    if not 0 <= start <= end - 0.5:
        raise PlanOpError(
            f"timeline_start_seconds must be within [0, {end - 0.5:.1f}]"
        )
    available = float(asset.get("duration_seconds") or 0.0)
    source_start = _grid(
        _op_number(op, "source_start_seconds")
        if op.get("source_start_seconds") is not None else 0.0, fps,
    )
    if source_start < 0 or source_start >= available:
        raise PlanOpError(
            f"source_start_seconds must be within [0, {available:.1f})"
        )
    duration = _grid(
        _op_number(op, "duration_seconds")
        if op.get("duration_seconds") is not None
        else min(4.0, available - source_start, end - start), fps,
    )
    if duration < 0.5:
        raise PlanOpError("B-roll must be at least 0.5s")
    if source_start + duration > available + 0.05:
        raise PlanOpError(
            f"{op['asset_id']} has only {available:.1f}s of source material"
        )
    event = _new_broll_event(plan, op["asset_id"], source_start, start, duration)
    _check_overlays_fit(plan)
    return (
        f"B-roll {event['event_id']} ({op['asset_id']}) sobre "
        f"{start:.1f}-{start + duration:.1f}s; el audio original continúa"
    )


def _apply_remove_broll(plan: dict, op: dict, assets: dict) -> str:
    track = _broll_track(plan)
    if track is None:
        raise PlanOpError("This plan has no B-roll track")
    index = _index_of(track["events"], op["event_id"])
    removed = track["events"].pop(index)
    if not track["events"]:
        plan["tracks"].remove(track)
    return f"B-roll {removed['event_id']} eliminado; vuelve a verse la escena"


def _apply_replace_broll(plan: dict, op: dict, assets: dict) -> str:
    fps = plan["project"]["fps"]
    track = _broll_track(plan)
    if track is None:
        raise PlanOpError("This plan has no B-roll track")
    event = track["events"][_index_of(track["events"], op["event_id"])]
    asset = _video_asset(assets, op["asset_id"])
    available = float(asset.get("duration_seconds") or 0.0)
    source_start = _grid(
        _op_number(op, "source_start_seconds")
        if op.get("source_start_seconds") is not None else 0.0, fps,
    )
    if source_start + event["duration_seconds"] > available + 0.05:
        raise PlanOpError(
            f"{op['asset_id']} has only {available:.1f}s of source material "
            f"for this {event['duration_seconds']:.1f}s slot"
        )
    event["asset_id"] = op["asset_id"]
    event["source_start_seconds"] = source_start
    event["source_end_seconds"] = _r(source_start + event["duration_seconds"])
    event["observed_content"] = None
    event["confidence"] = 0.5
    return (
        f"B-roll {event['event_id']} ahora muestra {op['asset_id']} "
        f"(mismo hueco de {event['duration_seconds']:.1f}s)"
    )


def _apply_move_broll(plan: dict, op: dict, assets: dict) -> str:
    fps = plan["project"]["fps"]
    track = _broll_track(plan)
    if track is None:
        raise PlanOpError("This plan has no B-roll track")
    event = track["events"][_index_of(track["events"], op["event_id"])]
    start = _grid(_op_number(op, "timeline_start_seconds"), fps)
    end = _primary_end(plan)
    if not 0 <= start <= end - event["duration_seconds"] + 0.05:
        raise PlanOpError(
            "timeline_start_seconds must keep the B-roll within the video "
            f"(0 to {end - event['duration_seconds']:.1f})"
        )
    event["timeline_start_seconds"] = start
    track["events"].sort(key=lambda e: e["timeline_start_seconds"])
    _check_overlays_fit(plan)
    return (
        f"B-roll {event['event_id']} movido a "
        f"{start:.1f}-{start + event['duration_seconds']:.1f}s"
    )


def _music_track(plan: dict, create: bool = False) -> dict | None:
    for track in plan["tracks"]:
        if track["kind"] == "audio" and track.get("role") == "music":
            return track
    if create:
        track = {"track_id": "mus1", "kind": "audio", "role": "music",
                 "events": []}
        plan["tracks"].append(track)
        return track
    return None


def _as_bool(value, default: bool) -> bool:
    # A JSON string must map to a bool by its SPELLING, not Python truthiness
    # (plain bool("false") is True). Only recognized spellings are accepted;
    # anything else raises rather than silently guessing.
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("true", "1", "yes"):
            return True
        if token in ("false", "0", "no"):
            return False
    raise PlanOpError(f"expected a boolean, got {value!r}")


def _finite(value, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise PlanOpError(f"{name} must be a number")
    if result != result or result in (float("inf"), float("-inf")):
        raise PlanOpError(f"{name} must be a finite number")
    return result


def _apply_set_music_bed(plan: dict, op: dict, assets: dict) -> str:
    asset_id = op["asset_id"]
    asset = assets.get(asset_id)
    if asset is None or asset.get("media_type") not in ("audio", "video"):
        raise PlanOpError(f"{asset_id} is not a usable music source")
    # A silent video (B-roll with no audio stream) has nothing to mix — the
    # renderer would reference a nonexistent :a:0 and fail.
    if not asset.get("audio"):
        raise PlanOpError(f"{asset.get('filename', asset_id)} has no audio to use as music")
    duration = float(plan["project"]["duration_seconds"])
    asset_duration = float(asset.get("duration_seconds") or 0.0)
    gain = _finite(op.get("gain_db"), -14.0, "gain_db")
    duck = _finite(op.get("duck_db"), -12.0, "duck_db")
    if not -40 <= gain <= 6 or not -40 <= duck <= 0:
        raise PlanOpError("gain_db must be -40..6 and duck_db -40..0")
    loop = _as_bool(op.get("loop"), default=True)
    # Looping fills the whole cut from a source of any length; a one-shot bed
    # occupies only min(asset, cut) so its source range stays inside the asset
    # and the plan validator accepts it.
    if loop:
        source_end = round(asset_duration, 3) if asset_duration else round(duration, 3)
        span = round(duration, 3)
    else:
        span = round(min(duration, asset_duration or duration), 3)
        source_end = span
    track = _music_track(plan, create=True)
    track["events"] = [{
        "event_id": "mus-01", "asset_id": asset_id,
        "source_start_seconds": 0.0, "source_end_seconds": source_end,
        "timeline_start_seconds": 0.0, "duration_seconds": span,
        "playback_rate": 1.0, "intent": "music", "observed_content": None,
        "confidence": 1.0, "text": None, "volume_db": gain,
        "music": {"mode": "bed", "recommended": None,
                  "bed": {"asset_id": asset_id, "gain_db": gain,
                          "duck_db": duck, "loop": loop}},
    }]
    return f"Música de fondo puesta ({asset['filename']}, {gain:g}dB, ducking {duck:g}dB)"


def _apply_remove_music(plan: dict, op: dict, assets: dict) -> str:
    plan["tracks"] = [
        t for t in plan["tracks"]
        if not (t["kind"] == "audio" and t.get("role") == "music")
    ]
    return "Música de fondo quitada"


def _apply_edit_caption(plan: dict, op: dict, assets: dict) -> str:
    cap = next((t for t in plan["tracks"] if t["kind"] == "caption"), None)
    if cap is None:
        raise PlanOpError("This cut has no captions")
    event = next(
        (e for e in cap["events"] if e["event_id"] == op["event_id"]), None
    )
    if event is None:
        raise PlanOpError(f"No caption {op['event_id']!r}")
    text = str(op["text"]).strip()
    if not 1 <= len(text) <= 200:
        raise PlanOpError("Caption text must be 1-200 characters")
    # Capture the ASR text this correction replaced (once) so a later revision
    # can prove the footage is unchanged before carrying the correction forward.
    if not event.get("asr_text"):
        event["asr_text"] = event.get("text")
    event["text"] = text
    # Provenance: this text was typed by the user through the direct caption
    # control, not authored by a model — it is trusted rendered language.
    event["user_authored"] = True
    return f"Subtítulo {event['event_id']} ahora dice: «{text}»"


def _apply_remove_caption(plan: dict, op: dict, assets: dict) -> str:
    cap = next((t for t in plan["tracks"] if t["kind"] == "caption"), None)
    if cap is None:
        raise PlanOpError("This cut has no captions")
    before = len(cap["events"])
    cap["events"] = [e for e in cap["events"] if e["event_id"] != op["event_id"]]
    if len(cap["events"]) == before:
        raise PlanOpError(f"No caption {op['event_id']!r}")
    return f"Subtítulo {op['event_id']} eliminado"


# Transitions the renderer honours WITHOUT changing timeline geometry: a hard
# cut, or a dip to black/white centred on the seam. A true crossfade
# ("dissolve") needs the clips to overlap on the timeline, which this build does
# not yet do, so it is refused rather than silently rendered as a cut.
_DIP_TRANSITIONS = {"cut", "fade_black", "fade_white"}


def _apply_set_transition(plan: dict, op: dict, assets: dict) -> str:
    video, _ = _primary_tracks(plan)
    event = next(
        (e for e in video["events"] if e["event_id"] == op["event_id"]), None
    )
    if event is None:
        raise PlanOpError(f"No scene {op['event_id']!r}")
    kind = op["type"]
    if not isinstance(kind, str):
        raise PlanOpError("transition type must be a string")
    if kind == "dissolve":
        raise PlanOpError(
            "el fundido encadenado (dissolve) aún no está disponible; usa "
            "corte, o fundido a negro/blanco"
        )
    if kind not in _DIP_TRANSITIONS:
        raise PlanOpError(f"transition type must be one of {_DIP_TRANSITIONS}")
    if kind == "cut":
        event["transition_out"] = {"type": "cut", "duration_seconds": 0.0}
        return f"Escena {event['event_id']}: corte seco"
    dur = _finite(op.get("duration_seconds"), 0.5, "duration_seconds")
    if not 0 < dur <= 3:
        raise PlanOpError("duration_seconds must be within (0, 3]")
    event["transition_out"] = {"type": kind, "duration_seconds": round(dur, 3)}
    colour = "negro" if kind == "fade_black" else "blanco"
    return f"Escena {event['event_id']}: fundido a {colour} ({dur:g}s)"


def _apply_set_fades(plan: dict, op: dict, assets: dict) -> str:
    if op.get("intro_seconds") is None and op.get("outro_seconds") is None:
        raise PlanOpError("set_fades needs intro_seconds and/or outro_seconds")
    current = plan.get("transitions") or {}
    # Update only the side(s) the caller provided — an instruction that sets the
    # intro must not silently wipe an existing outro.
    intro = _finite(op.get("intro_seconds"),
                    float(current.get("intro_fade_seconds") or 0.0), "intro_seconds")
    outro = _finite(op.get("outro_seconds"),
                    float(current.get("outro_fade_seconds") or 0.0), "outro_seconds")
    if not 0 <= intro <= 3 or not 0 <= outro <= 3:
        raise PlanOpError("intro_seconds and outro_seconds must be 0..3")
    plan["transitions"] = {
        "intro_fade_seconds": round(intro, 3),
        "outro_fade_seconds": round(outro, 3),
    }
    if intro == 0 and outro == 0:
        return "Fundidos de apertura y cierre quitados"
    return f"Fundidos: apertura {intro:g}s, cierre {outro:g}s"


_APPLIERS = {
    "delete_event": (_apply_delete, {"event_id"}),
    "trim_event": (_apply_trim, {"event_id", "edge", "direction", "seconds"}),
    "set_volume": (_apply_volume, {"event_id", "volume_db"}),
    "jl_cut": (_apply_jl, {"event_id", "lead_seconds"}),
    "set_title": (_apply_title, {"event_id", "text"}),
    "add_voiceover": (_apply_add_voiceover,
                      {"asset_id", "timeline_start_seconds"}),
    "remove_voiceover": (_apply_remove_voiceover, {"event_id"}),
    "add_broll": (_apply_add_broll, {"asset_id", "timeline_start_seconds"}),
    "remove_broll": (_apply_remove_broll, {"event_id"}),
    "replace_broll": (_apply_replace_broll, {"event_id", "asset_id"}),
    "move_broll": (_apply_move_broll, {"event_id", "timeline_start_seconds"}),
    "set_music_bed": (_apply_set_music_bed, {"asset_id"}),
    "remove_music": (_apply_remove_music, set()),
    "edit_caption": (_apply_edit_caption, {"event_id", "text"}),
    "remove_caption": (_apply_remove_caption, {"event_id"}),
    "set_transition": (_apply_set_transition, {"event_id", "type"}),
    "set_fades": (_apply_set_fades, set()),
}


def apply_op(plan: dict, op: dict, inventory: dict) -> tuple[dict, str]:
    """Apply one validated op to a COPY of the plan; returns (candidate,
    Spanish summary). The candidate's revision is bumped by one."""
    kind = op.get("op")
    if kind not in _APPLIERS:
        raise PlanOpError(f"Unknown operation {kind!r}")
    applier, required = _APPLIERS[kind]
    missing = required - {k for k, v in op.items() if v is not None}
    if missing:
        raise PlanOpError(f"{kind} is missing {', '.join(sorted(missing))}")
    if kind == "trim_event":
        if op["edge"] not in ("start", "end"):
            raise PlanOpError("edge must be 'start' or 'end'")
        if op["direction"] not in ("shorten", "extend"):
            raise PlanOpError("direction must be 'shorten' or 'extend'")
    assets = {a["asset_id"]: a for a in inventory.get("assets", [])}
    candidate = deepcopy(plan)
    summary = applier(candidate, op, assets)
    candidate["revision"] = int(plan.get("revision", 1)) + 1
    # derived style grammar must follow every mutation or it becomes a lie
    from .planning import refresh_style_application

    refresh_style_application(candidate)
    return candidate, summary


def _event_table(
    plan: dict,
    inventory: dict | None = None,
    asset_hints: dict | None = None,
) -> str:
    lines = []
    video, audio = _primary_tracks(plan)
    for v, a in zip(video["events"], audio["events"]):
        end = v["timeline_start_seconds"] + v["duration_seconds"]
        quote = (v.get("observed_content") or "").replace("\n", " ")[:90]
        volume = a.get("volume_db")
        vol = f" vol={volume:g}dB" if volume else ""
        lines.append(
            f"- {v['event_id']} [{v['timeline_start_seconds']:.1f}-{end:.1f}s]"
            f" intent={v['intent']}{vol} :: {quote}"
        )
    for t in _title_events(plan):
        lines.append(
            f"- {t['event_id']} (título, {t['timeline_start_seconds']:.1f}s)"
            f" :: {t.get('text')!r}"
        )
    for b in _broll_events(plan):
        end = b["timeline_start_seconds"] + b["duration_seconds"]
        lines.append(
            f"- {b['event_id']} (b-roll, {b['timeline_start_seconds']:.1f}-"
            f"{end:.1f}s)"
        )
    vo = _voiceover_track(plan)
    for v in (vo["events"] if vo else []):
        lines.append(
            f"- {v['event_id']} (voz en off, "
            f"{v['timeline_start_seconds']:.1f}s, {v['asset_id']})"
        )
    audio_assets = [
        a for a in (inventory or {}).get("assets", [])
        if a.get("media_type") == "audio"
    ]
    if audio_assets:
        lines.append("Available voiceover audio assets:")
        for a in audio_assets:
            lines.append(
                f"- {a['asset_id']} ({float(a.get('duration_seconds') or 0):.1f}s)"
                f" :: {a.get('filename', '')}"
            )
    video_assets = [
        a for a in (inventory or {}).get("assets", [])
        if a.get("media_type") == "video"
    ]
    if video_assets:
        lines.append("Available footage assets (for B-roll):")
        for a in video_assets:
            hint = (asset_hints or {}).get(a["asset_id"], "")
            hint = f" — {hint[:80]}" if hint else ""
            lines.append(
                f"- {a['asset_id']} ({float(a.get('duration_seconds') or 0):.1f}s)"
                f" :: {a.get('filename', '')}{hint}"
            )
    return "\n".join(lines)


_OPS_CONTRACT = """
Respond with EXACTLY one JSON object, nothing else. One of:
{"op":"delete_event","event_id":"..."}
{"op":"trim_event","event_id":"...","edge":"start"|"end","direction":"shorten"|"extend","seconds":<0-60>}
{"op":"set_volume","event_id":"...","volume_db":<-96..12, -96 mutes>}
{"op":"jl_cut","event_id":"...","lead_seconds":<0.1..5 J-cut (audio of this scene starts early) or -5..-0.1 L-cut>}
{"op":"set_title","event_id":"...","text":"...","font":"sans|handwritten|clean|display (optional)","size":"24-140 (optional)","position":"top|center|lower (optional)"}
{"op":"set_music_bed","asset_id":"<an audio asset>","gain_db":<-40..6, opt>,"duck_db":<-40..0, opt>,"loop":<bool, opt>}
{"op":"remove_music"}
{"op":"set_transition","event_id":"<a scene id>","type":"cut|fade_black|fade_white","duration_seconds":<0-3, for a fade>}
{"op":"set_fades","intro_seconds":<0-3>,"outro_seconds":<0-3>}
{"op":"add_voiceover","asset_id":"<an available voiceover audio asset>","timeline_start_seconds":<number>}
{"op":"remove_voiceover","event_id":"vo-.."}
{"op":"add_broll","asset_id":"<a footage asset>","timeline_start_seconds":<number>,"duration_seconds":<optional, default up to 4>,"source_start_seconds":<optional, default 0>}
{"op":"remove_broll","event_id":"bro-.."}
{"op":"replace_broll","event_id":"bro-..","asset_id":"<a footage asset>","source_start_seconds":<optional>}
{"op":"move_broll","event_id":"bro-..","timeline_start_seconds":<number>}
{"op":"reject","reason":"<short reason in the instruction's language>"}

Rules: pick exactly ONE operation. Use event ids from the timeline listing
verbatim. If the instruction is ambiguous, asks for several changes at once,
or asks for something outside these operations, use reject with a helpful
reason. Do not invent event ids.
""".strip()


def instruction_to_op(
    client, plan: dict, instruction: str, inventory: dict | None = None,
    asset_hints: dict | None = None,
) -> dict:
    """Map a natural-language instruction to one closed-set op via the LLM."""
    instruction = (instruction or "").strip()
    if not 2 <= len(instruction) <= 500:
        raise PlanOpError("Instruction must be 2-500 characters")
    messages = [
        {
            "role": "system",
            "content": (
                "You translate ONE editing instruction into ONE structured "
                "operation on a video edit plan.\n\nTimeline:\n"
                + _event_table(plan, inventory, asset_hints)
                + "\n\n" + _OPS_CONTRACT
            ),
        },
        {"role": "user", "content": instruction},
    ]
    response = client.chat(messages, json_object=True, temperature=0.0)
    op = parse_json_content(response["content"])
    if not isinstance(op, dict) or not isinstance(op.get("op"), str):
        raise PlanOpError(f"Model returned no operation: {json.dumps(op)[:200]}")
    # Caption text is a rendered claim: it may only be authored by the user
    # through the direct caption controls, never by the instruction model.
    # If the model emits one anyway, refuse rather than burn unverified text.
    if op.get("op") in ("edit_caption", "remove_caption"):
        return {
            "op": "reject",
            "reason": "los subtítulos se editan directamente en cada línea",
        }
    return op
