#!/usr/bin/env python3
"""Render a machine-readable edit plan into a review MP4 with burned-in titles."""

import argparse
import json
import os
import subprocess
from pathlib import Path


# Bundled style fonts (app/fonts, OFL) — chosen via text_style.font on
# title events; "sans" falls through to the Liberation default below.
STYLE_FONTS = {
    "handwritten": Path(__file__).resolve().parent.parent / "fonts" / "Caveat.ttf",
    "clean": Path(__file__).resolve().parent.parent / "fonts" / "Inter.ttf",
    "display": Path(__file__).resolve().parent.parent / "fonts" / "Montserrat.ttf",
}

FONT_CANDIDATES = (
    Path("/usr/share/fonts/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
)


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def track(plan, kind: str):
    return next(item for item in plan["tracks"] if item["kind"] == kind)


def primary_audio_track(plan):
    # Role-based: voiceover and music are also kind 'audio', so the primary is
    # not simply "the first audio track" once tracks may be reordered.
    return next(
        item for item in plan["tracks"]
        if item["kind"] == "audio" and item.get("role") in (None, "primary")
    )


def duration_target(plan):
    return plan["project"]["duration_seconds"]


def broll_track(plan):
    matches = [item for item in plan["tracks"] if item["kind"] == "video"]
    return matches[1] if len(matches) == 2 and matches[1].get("role") == "broll" else None


def caption_events(plan):
    cap = next((t for t in plan["tracks"] if t.get("kind") == "caption"), None)
    return cap["events"] if cap else []


def music_bed_event(plan):
    # A renderable bed needs the TOP-LEVEL asset_id the renderer indexes with
    # (assets[event["asset_id"]]); a bed carrying only the nested bed.asset_id
    # would crash the render, so it is not treated as renderable here.
    for t in plan["tracks"]:
        if t.get("kind") == "audio" and t.get("role") == "music":
            for e in t.get("events", []):
                m = e.get("music") or {}
                if m.get("mode") == "bed" and e.get("asset_id"):
                    return e
    return None


def srt_timestamp(seconds: float) -> str:
    # Round to whole milliseconds FIRST, then decompose — otherwise 59.9996
    # formats as the invalid "00:00:59,1000" (the ms field must stay < 1000).
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_text(value: str) -> str:
    # A cue's text must not contain a blank line (it terminates the cue) or a
    # bare "-->" line (it looks like a timing line). Collapse newlines to
    # spaces so caption text can never inject extra/malformed cues.
    return " ".join(str(value or "").split())


def write_caption_srt(events, path) -> bool:
    lines = []
    n = 0
    for e in sorted(events, key=lambda x: x["timeline_start_seconds"]):
        text = _srt_text(e.get("text"))
        if not text:
            continue
        n += 1
        start = e["timeline_start_seconds"]
        end = start + e["duration_seconds"]
        lines.append(str(n))
        lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    if not n:
        return False
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


_ASS_FONTS = {
    # best-effort mapping onto fonts present in the render container
    "sans": "Liberation Sans",
    "clean": "Liberation Sans",
    "display": "Liberation Sans",
    "handwritten": "Liberation Serif",
}
_ASS_ALIGN = {"lower": 2, "center": 5, "top": 8}


def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(value: str) -> str:
    # Neutralize ASS markup in caption BODY text: '{...}' is an override block
    # and '\' starts commands (\N, \h). A user/model caption must never inject
    # styling outside the constrained caption_style vocabulary, so strip those
    # characters to harmless look-alikes after collapsing whitespace.
    return (
        _srt_text(value)
        .replace("\\", "＼")
        .replace("{", "(")
        .replace("}", ")")
    )


def has_caption_styles(events) -> bool:
    return any((e.get("caption_style") or {}) for e in events)


def write_caption_ass(events, path, width: int, height: int) -> bool:
    """Per-cue styled captions as ASS. Used only when an event carries a
    caption_style — the default (unstyled) path stays on SRT + force_style so
    its proven appearance is untouched. Style is applied as inline overrides
    so one default Style suffices."""
    default_size = max(16, round(height * 0.030))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginV\n"
        f"Style: Default,Liberation Sans,{default_size},&H00FFFFFF,&HCC000000,"
        "&H00000000,1,1,3,0,2,64\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, MarginV, Text\n"
    )
    lines = [header]
    n = 0
    for e in sorted(events, key=lambda x: x["timeline_start_seconds"]):
        text = _ass_text(e.get("text"))
        if not text:
            continue
        n += 1
        style = e.get("caption_style") or {}
        overrides = ""
        font = _ASS_FONTS.get(style.get("font"))
        if font:
            overrides += f"\\fn{font}"
        if style.get("size"):
            overrides += f"\\fs{int(style['size'])}"
        align = _ASS_ALIGN.get(style.get("position"))
        if align:
            overrides += f"\\an{align}"
        # a top caption clears the top edge; others sit above the bottom edge
        margin = 64
        body = f"{{{overrides}}}{text}" if overrides else text
        start = _ass_time(e["timeline_start_seconds"])
        end = _ass_time(e["timeline_start_seconds"] + e["duration_seconds"])
        lines.append(
            f"Dialogue: 0,{start},{end},Default,{margin},{body}"
        )
    if not n:
        return False
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def voiceover_events(plan):
    # Role-based: a music track may now sit among the audio tracks, so the
    # voiceover is no longer guaranteed to be the second audio track.
    for item in plan["tracks"]:
        if item["kind"] == "audio" and item.get("role") == "voiceover":
            return item["events"]
    return []


def ffmpeg_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("\n", "\\n")
    )


def rotation_filter(degrees: float) -> str:
    normalized = int(round(degrees)) % 360
    if normalized == 0:
        return ""
    if normalized == 90:
        return "transpose=clock,"
    if normalized == 180:
        return "hflip,vflip,"
    if normalized == 270:
        return "transpose=cclock,"
    raise ValueError(f"Unsupported rotation: {degrees} degrees")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument(
        "--captions", type=Path, default=None,
        help="Optional SRT file to burn into the review render",
    )
    args = parser.parse_args()

    plan_path = args.plan.resolve()
    output_path = args.output.resolve()
    media_root = args.media_root.resolve()
    plan = load_json(plan_path)
    inventory = load_json(args.inventory)
    assets = {asset["asset_id"]: asset for asset in inventory["assets"]}
    video_events = track(plan, "video")["events"]
    audio_events = primary_audio_track(plan)["events"]
    title_events = track(plan, "title")["events"]
    overlay = broll_track(plan)
    broll_events = overlay["events"] if overlay else []
    vo_events = voiceover_events(plan)
    caption_track = caption_events(plan)
    music_event = music_bed_event(plan)

    # Video and audio are independent timelines (J/L cuts move the audio
    # boundary without moving the picture boundary). Overlaps are errors;
    # gaps are rendered as black/silence by segments() below. Tolerance is
    # half a frame so legitimate one-frame gaps survive at any fps.
    epsilon = 0.5 / plan["project"]["fps"]
    for kind, events in (("video", video_events), ("audio", audio_events)):
        cursor = 0.0
        for event in sorted(events, key=lambda e: e["timeline_start_seconds"]):
            if event["timeline_start_seconds"] < cursor - epsilon:
                raise ValueError(
                    f"{kind} track overlaps itself at "
                    f"{event['timeline_start_seconds']}s"
                )
            cursor = event["timeline_start_seconds"] + event["duration_seconds"]
        if cursor > duration_target(plan) + 0.05:
            raise ValueError(
                f"{kind} track covers {cursor}s, past the plan duration "
                f"{duration_target(plan)}s"
            )
    font_path = next((path for path in FONT_CANDIDATES if path.exists()), None)
    if font_path is None:
        raise FileNotFoundError(
            "Liberation Sans Bold was not found in any expected font directory"
        )

    width = plan["project"]["width"]
    height = plan["project"]["height"]
    fps = plan["project"]["fps"]
    duration = plan["project"]["duration_seconds"]

    command = ["ffmpeg", "-hide_banner", "-y"]
    for event in video_events + broll_events + audio_events + vo_events:
        source = (media_root / assets[event["asset_id"]]["source_path"]).resolve()
        command.extend(["-i", str(source)])
    audio_input_base = len(video_events) + len(broll_events)
    vo_input_base = audio_input_base + len(audio_events)
    music_input_index = None
    if music_event is not None:
        music_input_index = len(video_events) + len(broll_events) + len(audio_events) + len(vo_events)
        bed = (music_event.get("music") or {}).get("bed") or {}
        msrc = (media_root / assets[music_event["asset_id"]]["source_path"]).resolve()
        # loop the bed so a short track fills the whole cut
        if bed.get("loop", True):
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(msrc)])

    def gaps(events):
        """(position, length) of every hole a track leaves in [0, duration)."""
        holes = []
        cursor = 0.0
        for event in sorted(events, key=lambda e: e["timeline_start_seconds"]):
            if event["timeline_start_seconds"] > cursor + epsilon:
                holes.append((cursor, event["timeline_start_seconds"] - cursor))
            cursor = event["timeline_start_seconds"] + event["duration_seconds"]
        if duration > cursor + epsilon:
            holes.append((cursor, duration - cursor))
        return holes

    filters = []
    def framing(event) -> str:
        """fit letterboxes (default); fill scale-crops toward the reframe
        center so vertical outputs can use the full frame."""
        reframe = event.get("reframe") or {}
        if reframe.get("mode") == "fill":
            cx = min(1.0, max(0.0, reframe.get("center_x", 0.5)))
            cy = min(1.0, max(0.0, reframe.get("center_y", 0.5)))
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:(iw-{width})*{cx}:(ih-{height})*{cy},"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:"
            f"color={plan['project']['background_color']},"
        )

    # Per-cut dip-to-colour: a fade_black/fade_white transition_out colours ONE
    # seam. It is applied inside the two adjacent clips' own time (outgoing tail
    # fades to the colour, incoming head fades up), so it is geometry-preserving.
    # A dip only exists where the two clips are actually CONTIGUOUS and the
    # outgoing clip has a successor — a gap or the final clip has no seam.
    ordered_video = sorted(video_events, key=lambda e: e["timeline_start_seconds"])

    # Fail closed on any transition this build can't render — on EVERY clip,
    # including the last or only one — rather than silently dropping it.
    for ev in ordered_video:
        kind = (ev.get("transition_out") or {}).get("type")
        if kind not in (None, "cut", "fade_black", "fade_white"):
            raise ValueError(
                f"transition type {kind!r} on {ev['event_id']} is not "
                "renderable in this build"
            )

    def _rendered_len(ev):
        # the actual length of the trimmed segment the fade sits on — source
        # span, not the timeline duration (they differ under a playback_rate)
        return ev["source_end_seconds"] - ev["source_start_seconds"]

    def _seam_dip(prev_ev, this_ev):
        """(colour, half-duration) of the dip on the seam BEFORE this_ev, or
        (None, 0) if prev_ev has no dip or the two are not contiguous. Raises on
        an unsupported transition type so a persisted 'dissolve' fails loudly
        rather than rendering as a silent cut."""
        if prev_ev is None:
            return None, 0.0
        t = (prev_ev.get("transition_out") or {})
        kind = t.get("type")
        if kind in (None, "cut"):
            return None, 0.0
        if kind not in ("fade_black", "fade_white"):
            raise ValueError(
                f"transition type {kind!r} on {prev_ev['event_id']} is not "
                "renderable in this build"
            )
        prev_end = prev_ev["timeline_start_seconds"] + prev_ev["duration_seconds"]
        if abs(this_ev["timeline_start_seconds"] - prev_end) > epsilon:
            return None, 0.0  # a gap sits between them — not a seam
        colour = "black" if kind == "fade_black" else "white"
        # clamp by the RENDERED length of both segments so the fade can never
        # start before the clip or run past its decoded frames
        half = min(float(t.get("duration_seconds") or 0.0) / 2.0,
                   _rendered_len(prev_ev) / 2.0,
                   _rendered_len(this_ev) / 2.0)
        return (colour, half) if half > 0 else (None, 0.0)

    for index, video in enumerate(video_events):
        start = video["source_start_seconds"]
        end = video["source_end_seconds"]
        length = end - start
        rotation = (video.get("reframe") or {}).get("rotation_degrees", 0)
        pos = ordered_video.index(video)
        prev_ev = ordered_video[pos - 1] if pos > 0 else None
        next_ev = ordered_video[pos + 1] if pos + 1 < len(ordered_video) else None
        fade_filters = ""
        # fade UP from the colour of the dip on the seam BEFORE this clip
        in_colour, in_half = _seam_dip(prev_ev, video)
        if in_colour:
            fade_filters += f",fade=t=in:st=0:d={round(in_half, 3)}:color={in_colour}"
        # fade DOWN to the colour of this clip's own dip (only if it has a
        # contiguous successor — the final clip has no seam to colour)
        out_colour, out_half = _seam_dip(video, next_ev) if next_ev else (None, 0.0)
        if out_colour:
            fade_filters += (
                f",fade=t=out:st={round(length - out_half, 3)}:d={round(out_half, 3)}:"
                f"color={out_colour}"
            )
        filters.append(
            f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"{rotation_filter(rotation)}"
            f"{framing(video)}"
            f"setsar=1,fps={fps},format=yuv420p{fade_filters}[v{index}]"
        )
    for index, audio in enumerate(audio_events):
        start = audio["source_start_seconds"]
        end = audio["source_end_seconds"]
        volume = audio.get("volume_db") or 0
        if assets[audio["asset_id"]].get("audio"):
            length = end - start
            # 12ms edge fades suppress clicks at every concat joint.
            fades = (
                f",afade=t=in:st=0:d=0.012,"
                f"afade=t=out:st={round(length - 0.012, 6)}:d=0.012"
                if length > 0.1 else ""
            )
            filters.append(
                f"[{audio_input_base + index}:a:0]"
                f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"volume={volume}dB{fades},aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        else:
            # A silent stand-in keeps the audio timeline contiguous for
            # sources that carry no audio stream.
            filters.append(
                f"anullsrc=r=48000:cl=stereo:d={end - start},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
    video_parts = [
        (event["timeline_start_seconds"], f"[v{i}]")
        for i, event in enumerate(video_events)
    ]
    for j, (position, length) in enumerate(gaps(video_events)):
        filters.append(
            f"color=c=black:size={width}x{height}:rate={fps}:d={length},"
            f"format=yuv420p,setsar=1[vfill{j}]"
        )
        video_parts.append((position, f"[vfill{j}]"))
    audio_parts = [
        (event["timeline_start_seconds"], f"[a{i}]")
        for i, event in enumerate(audio_events)
    ]
    for j, (position, length) in enumerate(gaps(audio_events)):
        filters.append(
            f"anullsrc=r=48000:cl=stereo:d={length},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[afill{j}]"
        )
        audio_parts.append((position, f"[afill{j}]"))
    video_parts.sort()
    audio_parts.sort()
    filters.append(
        "".join(label for _, label in video_parts)
        + f"concat=n={len(video_parts)}:v=1:a=0[vcat]"
    )
    filters.append(
        "".join(label for _, label in audio_parts)
        + f"concat=n={len(audio_parts)}:v=0:a=1[acat]"
    )

    current_video = "vcat"
    # B-roll overlays sit above the primary picture while its audio continues;
    # each is offset to its timeline slot and only enabled inside it.
    for index, event in enumerate(broll_events):
        input_index = len(video_events) + index
        start = event["source_start_seconds"]
        end = event["source_end_seconds"]
        timeline_start = event["timeline_start_seconds"]
        timeline_end = timeline_start + event["duration_seconds"]
        rotation = (event.get("reframe") or {}).get("rotation_degrees", 0)
        output_label = f"vbroll{index}"
        filters.append(
            f"[{input_index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"{rotation_filter(rotation)}"
            f"{framing(event)}"
            f"setsar=1,fps={fps},format=yuv420p,"
            f"setpts=PTS+{timeline_start}/TB[b{index}]"
        )
        filters.append(
            f"[{current_video}][b{index}]overlay=eof_action=pass:"
            f"enable='between(t\\,{timeline_start}\\,{timeline_end})'[{output_label}]"
        )
        current_video = output_label
    title_text_dir = output_path.parent / f".titles.{output_path.stem}"
    title_text_dir.mkdir(parents=True, exist_ok=True)
    for index, event in enumerate(title_events):
        output_label = f"vtitle{index}"
        start = event["timeline_start_seconds"]
        end = start + event["duration_seconds"]
        hook = index == 0
        style = event.get("text_style") or {}
        styled_font = STYLE_FONTS.get(style.get("font"))
        event_font = (
            styled_font if styled_font is not None and styled_font.exists()
            else font_path
        )
        font_size = int(style.get("size") or (56 if hook else 58))
        position = style.get("position") or "top"
        y = {
            "top": 220 if hook else 190,
            "center": "(h-text_h)/2",
            "lower": "h-text_h-220",
        }.get(position, 220 if hook else 190)
        # arbitrary title text (apostrophes, colons, %) breaks inline
        # filter quoting no matter how it is escaped — a textfile whose
        # PATH we control sidesteps the whole quoting problem
        text_path = title_text_dir / f"title{index}.txt"
        text_path.write_text(event["text"], encoding="utf-8")
        filters.append(
            f"[{current_video}]drawtext=expansion=none:fontfile='{event_font}':textfile='{text_path}':"
            f"fontsize={font_size}:fontcolor=white:borderw=3:bordercolor=black@0.85:"
            f"box=1:boxcolor=black@0.42:boxborderw=24:"
            f"x=(w-text_w)/2:y={y}:fix_bounds=true:"
            f"enable='between(t\\,{start}\\,{end})'[{output_label}]"
        )
        current_video = output_label

    audio_out = "acat"
    if vo_events:
        # Duck the production audio -9dB inside every voiceover window,
        # then mix the delayed voiceover streams on top.
        windows = "+".join(
            f"between(t\\,{e['timeline_start_seconds']}\\,"
            f"{e['timeline_start_seconds'] + e['duration_seconds']})"
            for e in vo_events
        )
        filters.append(
            f"[acat]volume=-9dB:enable='{windows}'[aducked]"
        )
        vo_labels = []
        for index, event in enumerate(vo_events):
            start = event["source_start_seconds"] or 0
            end = event["source_end_seconds"] or (start + event["duration_seconds"])
            gain = event.get("volume_db") or 0
            delay_ms = int(round(event["timeline_start_seconds"] * 1000))
            length = end - start
            fades = (
                f",afade=t=in:st=0:d=0.012,"
                f"afade=t=out:st={round(length - 0.012, 6)}:d=0.012"
                if length > 0.1 else ""
            )
            filters.append(
                f"[{vo_input_base + index}:a:0]"
                f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"volume={gain}dB{fades},aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}[vo{index}]"
            )
            vo_labels.append(f"[vo{index}]")
        filters.append(
            f"[aducked]{''.join(vo_labels)}"
            f"amix=inputs={len(vo_events) + 1}:duration=first:normalize=0[amix]"
        )
        audio_out = "amix"

    # Background music bed: loop/trim to the cut, apply gain, DUCK inside
    # speech + voiceover windows so dialogue stays intelligible, then mix
    # under the production audio.
    if music_input_index is not None:
        bed = (music_event.get("music") or {}).get("bed") or {}
        gain = bed.get("gain_db", -14)
        duck = bed.get("duck_db", -12)
        # Duck only over ACTUAL speech, not the whole timeline. The primary
        # audio clips normally span the entire cut, so ducking on their
        # geometry would suppress the bed everywhere (bed + duck at all times).
        # Caption events are the ASR-derived spoken intervals; add voiceover.
        speech_windows = []
        for e in caption_track:
            speech_windows.append(
                (e["timeline_start_seconds"],
                 e["timeline_start_seconds"] + e["duration_seconds"])
            )
        for e in vo_events:
            speech_windows.append(
                (e["timeline_start_seconds"],
                 e["timeline_start_seconds"] + e["duration_seconds"])
            )
        # Fallback: with no captions and no voiceover we have no speech map, so
        # duck under the whole production audio rather than let music blast over
        # possible dialogue. (Conservative — captions, when present, are tighter.)
        if not speech_windows:
            for e in audio_events:
                speech_windows.append(
                    (e["timeline_start_seconds"],
                     e["timeline_start_seconds"] + e["duration_seconds"])
                )
        # Small pad so the duck opens just before a word and closes just after.
        pad = 0.15
        enable = "+".join(
            f"between(t\\,{round(max(0.0, s0 - pad), 3)}\\,{round(e0 + pad, 3)})"
            for s0, e0 in speech_windows
        ) or "0"
        # Short in/out fades so a bed that starts or ends on a non-zero sample
        # cannot click (the primary/voiceover clips already do this per-segment).
        # The fade-out sits at the bed's ACTUAL end — a non-looping bed shorter
        # than the cut ends before the plan does, so a fade scheduled at the plan
        # end would never fire.
        mus_end = round(duration, 3)
        bed_end = round(min(duration, float(music_event["duration_seconds"])), 3)
        fade_out_st = round(max(0.0, bed_end - 0.02), 3)
        filters.append(
            f"[{music_input_index}:a:0]"
            f"atrim=start=0:end={mus_end},asetpts=PTS-STARTPTS,"
            f"volume={gain}dB,"
            f"volume={duck}dB:enable='{enable}',"
            f"afade=t=in:st=0:d=0.02,"
            f"afade=t=out:st={fade_out_st}:d=0.02,"
            f"aresample=48000,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[mus]"
        )
        filters.append(
            f"[{audio_out}][mus]amix=inputs=2:duration=first:normalize=0[amus]"
        )
        audio_out = "amus"

    caption_file = None
    caption_is_ass = False
    if caption_track and has_caption_styles(caption_track):
        # per-cue styling requires ASS; the subtitles filter renders it natively
        caption_ass = output_path.with_name(f".{output_path.stem}.captions.ass")
        if write_caption_ass(caption_track, caption_ass, width, height):
            caption_file = caption_ass
            caption_is_ass = True
    if caption_file is None and caption_track:
        caption_srt = output_path.with_name(f".{output_path.stem}.captions.srt")
        if write_caption_srt(caption_track, caption_srt):
            caption_file = caption_srt
    elif caption_file is None and args.captions:
        caption_file = args.captions.resolve()
    if caption_file is not None:
        srt = str(caption_file)
        escaped = srt.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        if caption_is_ass:
            # ASS carries its own styling; no force_style override
            filters.append(
                f"[{current_video}]subtitles=filename='{escaped}'[vsubs]"
            )
        else:
            # bigger, high-contrast, safely above the bottom edge for Reels
            style = (
                "FontName=Liberation Sans,FontSize=15,Bold=1,PrimaryColour=&HFFFFFF&,"
                "OutlineColour=&HCC000000&,BorderStyle=1,Outline=3,Shadow=0,MarginV=64"
            )
            filters.append(
                f"[{current_video}]subtitles=filename='{escaped}':"
                f"force_style='{style}'[vsubs]"
            )
        current_video = "vsubs"

    # Opening/closing fades are VIDEO-ONLY and applied to the FINAL composited
    # picture: they sit at the very ends of the stream (where a plain fade=in /
    # fade=out is well defined) and cover B-roll/titles/captions. Video-only so
    # they never clip an opening hook or a closing sentence. (Internal dips can
    # NOT use composite fades — a chained fade=in blacks the whole stream before
    # its start — so they are applied per-clip above.)
    video_fades = []
    transitions = plan.get("transitions") or {}
    intro = min(float(transitions.get("intro_fade_seconds") or 0.0), duration / 2)
    outro = min(float(transitions.get("outro_fade_seconds") or 0.0), duration / 2)
    if intro > 0:
        video_fades.append(f"fade=t=in:st=0:d={round(intro, 3)}:color=black")
    if outro > 0:
        st = round(duration - outro, 3)
        video_fades.append(f"fade=t=out:st={st}:d={round(outro, 3)}:color=black")
    if video_fades:
        filters.append(f"[{current_video}]{','.join(video_fades)}[vfaded]")
        current_video = "vfaded"

    filters.append(
        f"[{audio_out}]loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[afinal]"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.rendering{output_path.suffix}")
    log_path = output_path.with_suffix(".ffmpeg.log")
    manifest_path = output_path.with_suffix(".render-command.json")

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{current_video}]",
            "-map",
            "[afinal]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-t",
            str(duration),
            str(temporary),
        ]
    )

    manifest = {
        "edit_plan": str(plan_path),
        "output": str(output_path),
        "command": command,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with log_path.open("w", encoding="utf-8") as log_handle:
        result = subprocess.run(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode:
        raise SystemExit(
            f"ffmpeg failed with exit code {result.returncode}; inspect {log_path}"
        )
    os.replace(temporary, output_path)
    print(output_path)


if __name__ == "__main__":
    main()
