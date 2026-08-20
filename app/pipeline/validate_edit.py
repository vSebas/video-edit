#!/usr/bin/env python3
"""Validate edit-plan semantics and, when provided, the rendered review MP4."""

import argparse
import datetime as dt
import json
import re
import subprocess
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import jsonschema


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
FRAME_TOLERANCE = 0.01  # fraction of a frame


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def track(plan, kind: str):
    return next(item for item in plan["tracks"] if item["kind"] == kind)


def merged_duration(intervals):
    total = 0.0
    merged = []
    for start, end in sorted(intervals):
        if not merged or start >= merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    for start, end in merged:
        total += end - start
    return total


def probe(path: Path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    return json.loads(subprocess.check_output(command, text=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument(
        "--media-root", type=Path, required=True,
        help="Directory that asset source_path values are relative to.",
    )
    parser.add_argument("--render", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    plan_path = args.plan.resolve()
    plan = load_json(plan_path)
    schema = load_json(SCHEMA_DIR / "edit-plan.schema.json")
    report_schema = load_json(SCHEMA_DIR / "validation-report.schema.json")
    inventory = load_json(args.inventory.resolve())
    media_root = args.media_root.resolve()
    assets = {asset["asset_id"]: asset for asset in inventory["assets"]}
    errors = []
    warnings = []
    checks = []

    def finding(target, code, message, event_id=None):
        target.append({"code": code, "message": message, "event_id": event_id})

    def check(check_id, status, message):
        checks.append({"check_id": check_id, "status": status, "message": message})

    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    schema_errors = sorted(validator.iter_errors(plan), key=lambda item: list(item.path))
    if schema_errors:
        for item in schema_errors:
            finding(errors, "SCHEMA", item.message)
        check("edit_plan_schema", "fail", f"{len(schema_errors)} schema error(s)")
    else:
        check("edit_plan_schema", "pass", "Edit plan conforms to edit-plan.v1")

    fps = plan["project"]["fps"]
    duration = plan["project"]["duration_seconds"]
    video_events = sorted(track(plan, "video")["events"], key=lambda event: event["timeline_start_seconds"])
    audio_events = sorted(track(plan, "audio")["events"], key=lambda event: event["timeline_start_seconds"])
    title_events = sorted(track(plan, "title")["events"], key=lambda event: event["timeline_start_seconds"])
    all_events = [event for item in plan["tracks"] for event in item["events"]]

    ranges = defaultdict(list)
    selected_seconds = 0.0
    range_error_count = 0
    frame_error_count = 0
    output_geometry_error_count = 0
    for event in video_events:
        asset = assets.get(event.get("asset_id"))
        if asset is None:
            finding(errors, "UNKNOWN_ASSET", "Asset is absent from inventory", event["event_id"])
            range_error_count += 1
            continue
        source_path = (media_root / asset["source_path"]).resolve()
        start = event["source_start_seconds"]
        end = event["source_end_seconds"]
        if not source_path.exists() or not 0 <= start < end <= asset["duration_seconds"] + 1e-6:
            finding(errors, "SOURCE_RANGE", "Source is missing or trim is out of bounds", event["event_id"])
            range_error_count += 1
        expected_duration = (end - start) / event.get("playback_rate", 1.0)
        if abs(expected_duration - event["duration_seconds"]) > 1e-6:
            finding(errors, "TRIM_DURATION", "Trim duration does not match timeline duration", event["event_id"])
            range_error_count += 1
        ranges[event["asset_id"]].append((start, end))
        selected_seconds += end - start
        for value in (start, end, event["timeline_start_seconds"], event["duration_seconds"]):
            # Plans store seconds rounded to six decimals, so an exactly
            # quantized 1/30 s reads as 0.033333 and lands 1e-5 off an
            # integer frame. Anything under a hundredth of a frame is that
            # rounding, not misalignment.
            if abs(value * fps - round(value * fps)) > FRAME_TOLERANCE:
                finding(errors, "FRAME_ALIGNMENT", f"{value}s is not aligned to {fps} fps", event["event_id"])
                frame_error_count += 1
                break
        reframe = event.get("reframe") or {}
        # "fit" scales any source into the project frame, so only modes that
        # pass the source through unscaled require matching dimensions.
        if reframe.get("mode") not in {"fit", "fill", "crop"}:
            rotation = int(round(reframe.get("rotation_degrees", 0))) % 180
            width = asset["video"]["width"]
            height = asset["video"]["height"]
            if rotation == 90:
                width, height = height, width
            if (width, height) != (plan["project"]["width"], plan["project"]["height"]):
                finding(errors, "GEOMETRY", "Reframed source does not resolve to project dimensions", event["event_id"])
                output_geometry_error_count += 1

    check("source_ranges", "pass" if not range_error_count else "fail", f"Checked {len(video_events)} source trims against the inventory")
    check("frame_alignment", "pass" if not frame_error_count else "fail", f"All trims and edit points are aligned to {fps} fps" if not frame_error_count else f"Found {frame_error_count} non-frame-aligned event(s)")
    check(
        "output_geometry",
        "pass" if not output_geometry_error_count else "fail",
        f"All video events resolve to {plan['project']['width']}x{plan['project']['height']}",
    )

    timeline_error_count = 0
    cursor = 0.0
    for event in video_events:
        if abs(event["timeline_start_seconds"] - cursor) > 1e-6:
            finding(errors, "TIMELINE_GAP_OR_OVERLAP", f"Expected event to start at {cursor:.3f}s", event["event_id"])
            timeline_error_count += 1
        cursor = event["timeline_start_seconds"] + event["duration_seconds"]
    if abs(cursor - duration) > 1e-6:
        finding(errors, "PROJECT_DURATION", f"Video timeline ends at {cursor:.3f}s instead of {duration:.3f}s")
        timeline_error_count += 1
    check("video_timeline", "pass" if not timeline_error_count else "fail", f"Video timeline is contiguous from 0 to {duration:.1f}s")

    av_error_count = 0
    if len(video_events) != len(audio_events):
        av_error_count += 1
    else:
        for video, audio in zip(video_events, audio_events, strict=True):
            keys = ("asset_id", "source_start_seconds", "source_end_seconds", "timeline_start_seconds", "duration_seconds")
            if any(video[key] != audio[key] for key in keys):
                av_error_count += 1
                finding(errors, "AV_SYNC", "Linked audio does not match video trim", audio["event_id"])
    if av_error_count and len(video_events) != len(audio_events):
        finding(errors, "AV_SYNC", "Video and audio event counts differ")
    check("linked_audio", "pass" if not av_error_count else "fail", "Audio trims are linked one-to-one with video trims")

    # Titles are an editorial choice — a single hook or a full band are both
    # valid. Only their placement is an invariant.
    title_error_count = 0
    for event in title_events:
        if not event.get("text"):
            title_error_count += 1
            finding(errors, "TITLE_TEXT", "Title beat has no text", event["event_id"])
        start = event["timeline_start_seconds"]
        if start < -1e-6 or start + event["duration_seconds"] > duration + 1e-6:
            title_error_count += 1
            finding(errors, "TITLE_BOUNDS", "Title beat falls outside the timeline", event["event_id"])
    check(
        "title_structure",
        "pass" if not title_error_count else "fail",
        f"{len(title_events)} title beat(s) carry text and sit inside the timeline",
    )

    unique_seconds = sum(merged_duration(asset_ranges) for asset_ranges in ranges.values())
    repeated_seconds = max(0.0, selected_seconds - unique_seconds)
    if repeated_seconds > 1e-6:
        # Reusing a moment is a valid cut, so this is reported, not failed.
        finding(warnings, "SOURCE_REPETITION", f"{repeated_seconds:.3f}s of source imagery is reused")
        check("source_repetition", "pass", f"{repeated_seconds:.3f}s of source imagery is reused")
    else:
        check("source_repetition", "pass", "No source frames are reused")

    if args.render:
        render_path = args.render.resolve()
        if not render_path.exists():
            finding(errors, "RENDER_MISSING", f"Rendered file does not exist: {render_path}")
            check("render_technical", "fail", "Rendered file is missing")
        else:
            data = probe(render_path)
            streams = data["streams"]
            video_stream = next((item for item in streams if item["codec_type"] == "video"), None)
            audio_stream = next((item for item in streams if item["codec_type"] == "audio"), None)
            actual_duration = float(data["format"]["duration"])
            technical_errors = []
            if video_stream is None or audio_stream is None:
                technical_errors.append("missing video or audio stream")
            else:
                rate = float(Fraction(video_stream["avg_frame_rate"]))
                if (video_stream["width"], video_stream["height"]) != (1080, 1920):
                    technical_errors.append("output is not 1080x1920")
                if abs(rate - fps) > 1e-6:
                    technical_errors.append(f"output frame rate is {rate}")
                if video_stream["codec_name"] != "h264" or audio_stream["codec_name"] != "aac":
                    technical_errors.append("output codecs are not H.264/AAC")
                if int(audio_stream["sample_rate"]) != 48000 or int(audio_stream["channels"]) != 2:
                    technical_errors.append("output audio is not 48 kHz stereo")
            if abs(actual_duration - duration) > (1 / fps + 0.01):
                technical_errors.append(f"output duration is {actual_duration:.3f}s")
            if technical_errors:
                for message in technical_errors:
                    finding(errors, "RENDER_TECHNICAL", message)
                check("render_technical", "fail", "; ".join(technical_errors))
            else:
                check("render_technical", "pass", f"H.264/AAC, 1080x1920, {fps:g} fps, {actual_duration:.3f}s")

            loudness = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(render_path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True,
                text=True,
                check=False,
            )
            match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", loudness.stderr)
            if match:
                maximum = float(match.group(1))
                status = "pass" if maximum <= -0.1 else "warning"
                check("audio_peak", status, f"Decoded maximum audio level is {maximum:.1f} dBFS")
                if status == "warning":
                    finding(warnings, "AUDIO_PEAK", "Audio peak is too close to digital full scale")
            else:
                check("audio_peak", "warning", "Could not parse decoded audio peak")
                finding(warnings, "AUDIO_PEAK_UNKNOWN", "Decoded audio peak could not be measured")
    else:
        check("render_technical", "not_applicable", "No render was supplied")
        check("audio_peak", "not_applicable", "No render was supplied")

    report = {
        "schema_version": "validation-report.v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "edit_plan_path": str(plan_path),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "metrics": {
            "planned_duration_seconds": duration,
            "unique_source_seconds": round(unique_seconds, 6),
            "repeated_source_seconds": round(repeated_seconds, 6),
            "event_count": len(all_events),
        },
    }
    jsonschema.Draft202012Validator(
        report_schema, format_checker=jsonschema.FormatChecker()
    ).validate(report)
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{'VALID' if report['valid'] else 'INVALID'}\t{report_path}")
    for item in checks:
        print(f"{item['status'].upper()}\t{item['check_id']}\t{item['message']}")
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
