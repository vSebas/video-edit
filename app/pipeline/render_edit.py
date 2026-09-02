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


def duration_target(plan):
    return plan["project"]["duration_seconds"]


def broll_track(plan):
    matches = [item for item in plan["tracks"] if item["kind"] == "video"]
    return matches[1] if len(matches) == 2 and matches[1].get("role") == "broll" else None


def voiceover_events(plan):
    matches = [item for item in plan["tracks"] if item["kind"] == "audio"]
    if len(matches) == 2 and matches[1].get("role") == "voiceover":
        return matches[1]["events"]
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
    audio_events = track(plan, "audio")["events"]
    title_events = track(plan, "title")["events"]
    overlay = broll_track(plan)
    broll_events = overlay["events"] if overlay else []
    vo_events = voiceover_events(plan)

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

    for index, video in enumerate(video_events):
        start = video["source_start_seconds"]
        end = video["source_end_seconds"]
        rotation = (video.get("reframe") or {}).get("rotation_degrees", 0)
        filters.append(
            f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"{rotation_filter(rotation)}"
            f"{framing(video)}"
            f"setsar=1,fps={fps},format=yuv420p[v{index}]"
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
        text = ffmpeg_text(event["text"])
        filters.append(
            f"[{current_video}]drawtext=expansion=none:fontfile='{event_font}':text='{text}':"
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

    if args.captions:
        srt = str(args.captions.resolve())
        escaped = srt.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        style = (
            "FontName=Liberation Sans,FontSize=13,PrimaryColour=&HFFFFFF&,"
            "OutlineColour=&H99000000&,Outline=2,MarginV=42"
        )
        filters.append(
            f"[{current_video}]subtitles=filename='{escaped}':"
            f"force_style='{style}'[vsubs]"
        )
        current_video = "vsubs"

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
