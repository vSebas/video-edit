#!/usr/bin/env python3
"""Render a machine-readable edit plan into a review MP4 with burned-in titles."""

import argparse
import json
import os
import subprocess
from pathlib import Path


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

    if len(video_events) != len(audio_events):
        raise ValueError("Video and audio event counts must match for linked rendering")
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
    for event in video_events:
        source = (media_root / assets[event["asset_id"]]["source_path"]).resolve()
        command.extend(["-i", str(source)])

    filters = []
    concat_inputs = []
    for index, (video, audio) in enumerate(zip(video_events, audio_events, strict=True)):
        if (
            video["asset_id"] != audio["asset_id"]
            or video["source_start_seconds"] != audio["source_start_seconds"]
            or video["source_end_seconds"] != audio["source_end_seconds"]
        ):
            raise ValueError(f"Linked A/V mismatch at event index {index}")

        start = video["source_start_seconds"]
        end = video["source_end_seconds"]
        rotation = (video.get("reframe") or {}).get("rotation_degrees", 0)
        video_filter = (
            f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"{rotation_filter(rotation)}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={plan['project']['background_color']},"
            f"setsar=1,fps={fps},format=yuv420p[v{index}]"
        )
        volume = audio.get("volume_db") or 0
        if assets[audio["asset_id"]].get("audio"):
            audio_filter = (
                f"[{index}:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"volume={volume}dB,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        else:
            # Silent source keeps A/V event pairing intact for clips that
            # carry no audio stream.
            audio_filter = (
                f"anullsrc=r=48000:cl=stereo:d={end - start},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        filters.extend([video_filter, audio_filter])
        concat_inputs.append(f"[v{index}][a{index}]")

    filters.append(
        f"{''.join(concat_inputs)}concat=n={len(video_events)}:v=1:a=1[vcat][acat]"
    )

    current_video = "vcat"
    for index, event in enumerate(title_events):
        output_label = f"vtitle{index}"
        start = event["timeline_start_seconds"]
        end = start + event["duration_seconds"]
        hook = index == 0
        font_size = 56 if hook else 58
        y = 220 if hook else 190
        text = ffmpeg_text(event["text"])
        filters.append(
            f"[{current_video}]drawtext=fontfile='{font_path}':text='{text}':"
            f"fontsize={font_size}:fontcolor=white:borderw=3:bordercolor=black@0.85:"
            f"box=1:boxcolor=black@0.42:boxborderw=24:"
            f"x=(w-text_w)/2:y={y}:fix_bounds=true:"
            f"enable='between(t\\,{start}\\,{end})'[{output_label}]"
        )
        current_video = output_label

    filters.append(
        "[acat]loudnorm=I=-16:LRA=11:TP=-1.5,aresample=48000,"
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
