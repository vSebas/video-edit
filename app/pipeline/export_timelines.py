#!/usr/bin/env python3
"""Export the canonical edit plan to OTIO and DaVinci-compatible FCP7 XMEML."""

import datetime as dt
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import opentimelineio as otio


# Set from command-line arguments in main(); every path is per-project.
PLAN_PATH: Path
INVENTORY_PATH: Path
MEDIA_ROOT: Path
OUTPUT_DIR: Path
SEQUENCE_NAME = "Timeline"
SEQUENCE_ID = "timeline-sequence"
OTIO_PATH: Path
XMEML_PATH: Path
REPORT_PATH: Path


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def track(plan, kind: str):
    return next(item for item in plan["tracks"] if item["kind"] == kind)


def broll_events(plan):
    matches = [item for item in plan["tracks"] if item["kind"] == "video"]
    if len(matches) == 2 and matches[1].get("role") == "broll":
        return matches[1]["events"]
    return []


def rt(seconds: float, fps: float):
    return otio.opentime.RationalTime(round(seconds * fps), fps)


def time_range(start: float, duration: float, fps: float):
    return otio.opentime.TimeRange(rt(start, fps), rt(duration, fps))


def add(parent, tag, value=None, attributes=None):
    node = ET.SubElement(parent, tag, attributes or {})
    if value is not None:
        node.text = str(value)
    return node


def add_rate(parent, fps: int):
    rate = add(parent, "rate")
    add(rate, "timebase", fps)
    add(rate, "ntsc", "FALSE")
    return rate


def frame(value: float, fps: int) -> int:
    return round(value * fps)


def make_reference(asset, fps):
    source = (MEDIA_ROOT / asset["source_path"]).resolve()
    return otio.schema.ExternalReference(
        target_url=source.as_uri(),
        available_range=time_range(
            0,
            math.ceil(asset["duration_seconds"] * fps) / fps,
            fps,
        ),
        metadata={
            "asset_id": asset["asset_id"],
            "sha256": asset["sha256"],
        },
    )


def export_otio(plan, assets):
    fps = plan["project"]["fps"]
    timeline = otio.schema.Timeline(name=SEQUENCE_NAME)
    timeline.global_start_time = rt(0, fps)
    timeline.metadata["video_editing_poc"] = {
        "schema_version": plan["schema_version"],
        "concept_id": plan["concept_id"],
        "edit_plan": str(PLAN_PATH),
        "width": plan["project"]["width"],
        "height": plan["project"]["height"],
        "fps": fps,
    }

    for kind, otio_kind, name in (
        ("video", otio.schema.TrackKind.Video, "V1 — AI Selects"),
        ("audio", otio.schema.TrackKind.Audio, "A1 — Natural Production Audio"),
    ):
        output_track = otio.schema.Track(name=name, kind=otio_kind)
        for event in track(plan, kind)["events"]:
            asset = assets[event["asset_id"]]
            clip = otio.schema.Clip(
                name=f"{event['event_id']} — {asset['filename']}",
                media_reference=make_reference(asset, fps),
                source_range=time_range(
                    event["source_start_seconds"],
                    event["duration_seconds"],
                    fps,
                ),
            )
            clip.metadata["video_editing_poc"] = {
                "event_id": event["event_id"],
                "asset_id": event["asset_id"],
                "intent": event["intent"],
                "observed_content": event.get("observed_content"),
                "confidence": event["confidence"],
                "timeline_start_seconds": event["timeline_start_seconds"],
                "rotation_degrees": (event.get("reframe") or {}).get("rotation_degrees", 0),
                "volume_db": event.get("volume_db"),
            }
            output_track.append(clip)
        timeline.tracks.append(output_track)

    overlays = broll_events(plan)
    if overlays:
        output_track = otio.schema.Track(
            name="V2 — B-Roll", kind=otio.schema.TrackKind.Video
        )
        previous_end = 0.0
        for event in overlays:
            gap = event["timeline_start_seconds"] - previous_end
            if gap > 1e-9:
                output_track.append(
                    otio.schema.Gap(source_range=time_range(0, gap, fps))
                )
            asset = assets[event["asset_id"]]
            clip = otio.schema.Clip(
                name=f"{event['event_id']} — {asset['filename']}",
                media_reference=make_reference(asset, fps),
                source_range=time_range(
                    event["source_start_seconds"], event["duration_seconds"], fps
                ),
            )
            clip.metadata["video_editing_poc"] = {
                "event_id": event["event_id"],
                "asset_id": event["asset_id"],
                "role": "broll",
                "intent": event["intent"],
                "confidence": event["confidence"],
                "timeline_start_seconds": event["timeline_start_seconds"],
            }
            output_track.append(clip)
            previous_end = (
                event["timeline_start_seconds"] + event["duration_seconds"]
            )
        timeline.tracks.append(output_track)

    for event in track(plan, "title")["events"]:
        marker = otio.schema.Marker(
            name=event["text"],
            marked_range=time_range(
                event["timeline_start_seconds"], event["duration_seconds"], fps
            ),
            color=otio.schema.MarkerColor.GREEN,
            metadata={
                "video_editing_poc": {
                    "kind": "title",
                    "event_id": event["event_id"],
                    "intent": event["intent"],
                    "text": event["text"],
                }
            },
        )
        timeline.tracks.markers.append(marker)

    otio.adapters.write_to_file(timeline, str(OTIO_PATH))


def add_file_node(clipitem, asset, fps):
    source = (MEDIA_ROOT / asset["source_path"]).resolve()
    file_node = add(clipitem, "file", attributes={"id": f"file-{asset['asset_id']}"})
    add(file_node, "name", asset["filename"])
    add(file_node, "pathurl", source.as_uri())
    add(file_node, "duration", math.ceil(asset["duration_seconds"] * fps))
    add_rate(file_node, fps)
    media = add(file_node, "media")
    video = add(media, "video")
    sample = add(video, "samplecharacteristics")
    add_rate(sample, fps)
    add(sample, "width", asset["video"]["width"])
    add(sample, "height", asset["video"]["height"])
    add(sample, "anamorphic", "FALSE")
    add(sample, "pixelaspectratio", "square")
    add(sample, "fielddominance", "none")
    if asset.get("audio"):
        audio = add(media, "audio")
        sample = add(audio, "samplecharacteristics")
        add(sample, "depth", 16)
        add(sample, "samplerate", asset["audio"]["sample_rate"])
        add(audio, "channelcount", asset["audio"]["channels"])


def add_motion_filter(clipitem, rotation):
    filter_node = add(clipitem, "filter")
    effect = add(filter_node, "effect")
    add(effect, "name", "Basic Motion")
    add(effect, "effectid", "basic")
    add(effect, "effectcategory", "motion")
    add(effect, "effecttype", "motion")
    add(effect, "mediatype", "video")
    parameter = add(effect, "parameter")
    add(parameter, "parameterid", "rotation")
    add(parameter, "name", "Rotation")
    add(parameter, "value", rotation)
    add(parameter, "valuemin", -360)
    add(parameter, "valuemax", 360)


def add_links(clipitem, video_id, audio_id, clip_index):
    for reference, media_type in ((video_id, "video"), (audio_id, "audio")):
        link = add(clipitem, "link")
        add(link, "linkclipref", reference)
        add(link, "mediatype", media_type)
        add(link, "trackindex", 1)
        add(link, "clipindex", clip_index)


def add_audio_level_filter(clipitem, volume_db):
    if not volume_db:
        return
    filter_node = add(clipitem, "filter")
    effect = add(filter_node, "effect")
    add(effect, "name", "Audio Levels")
    add(effect, "effectid", "audiolevels")
    add(effect, "effectcategory", "audiolevels")
    add(effect, "effecttype", "audiolevels")
    add(effect, "mediatype", "audio")
    parameter = add(effect, "parameter")
    add(parameter, "parameterid", "level")
    add(parameter, "name", "Level")
    add(parameter, "value", round(10 ** (volume_db / 20), 6))


def export_xmeml(plan, assets):
    fps = int(plan["project"]["fps"])
    root = ET.Element("xmeml", {"version": "5"})
    sequence = add(root, "sequence", attributes={"id": SEQUENCE_ID})
    add(sequence, "name", SEQUENCE_NAME)
    add(sequence, "duration", frame(plan["project"]["duration_seconds"], fps))
    add_rate(sequence, fps)
    timecode = add(sequence, "timecode")
    add_rate(timecode, fps)
    add(timecode, "string", "00:00:00:00")
    add(timecode, "frame", 0)
    add(timecode, "displayformat", "NDF")
    media = add(sequence, "media")
    video_parent = add(media, "video")
    fmt = add(video_parent, "format")
    sample = add(fmt, "samplecharacteristics")
    add_rate(sample, fps)
    add(sample, "codec", attributes={"name": "H.264"})
    add(sample, "width", plan["project"]["width"])
    add(sample, "height", plan["project"]["height"])
    add(sample, "anamorphic", "FALSE")
    add(sample, "pixelaspectratio", "square")
    add(sample, "fielddominance", "none")
    video_track = add(video_parent, "track")
    audio_parent = add(media, "audio")
    audio_track = add(audio_parent, "track")

    seen_files = set()
    video_events = track(plan, "video")["events"]
    audio_events = track(plan, "audio")["events"]
    # Links only make sense while A/V mirror exactly; J/L-cut plans export
    # unlinked clips (importers handle both).
    mirrored = len(video_events) == len(audio_events) and all(
        v["asset_id"] == a["asset_id"]
        and v["source_start_seconds"] == a["source_start_seconds"]
        and v["timeline_start_seconds"] == a["timeline_start_seconds"]
        and v["duration_seconds"] == a["duration_seconds"]
        for v, a in zip(video_events, audio_events)
    )
    for index, video in enumerate(video_events, start=1):
        asset = assets[video["asset_id"]]
        video_id = f"video-{video['event_id']}"
        audio_id = f"audio-{audio_events[index - 1]['event_id']}" if mirrored else None
        start = frame(video["timeline_start_seconds"], fps)
        end = start + frame(video["duration_seconds"], fps)
        source_in = frame(video["source_start_seconds"], fps)
        source_out = source_in + frame(video["duration_seconds"], fps)

        clipitem = add(video_track, "clipitem", attributes={"id": video_id})
        add(clipitem, "masterclipid", f"master-{asset['asset_id']}")
        add(clipitem, "name", f"{video['event_id']} — {asset['filename']}")
        add(clipitem, "enabled", "TRUE")
        add(clipitem, "duration", math.ceil(asset["duration_seconds"] * fps))
        add_rate(clipitem, fps)
        add(clipitem, "start", start)
        add(clipitem, "end", end)
        add(clipitem, "in", source_in)
        add(clipitem, "out", source_out)
        if asset["asset_id"] not in seen_files:
            add_file_node(clipitem, asset, fps)
            seen_files.add(asset["asset_id"])
        else:
            add(clipitem, "file", attributes={"id": f"file-{asset['asset_id']}"})
        sourcetrack = add(clipitem, "sourcetrack")
        add(sourcetrack, "mediatype", "video")
        add(sourcetrack, "trackindex", 1)
        rotation = (video.get("reframe") or {}).get("rotation_degrees", 0)
        if rotation:
            # Internal/FFmpeg convention is clockwise-positive; FCP7 XMEML is
            # counterclockwise-positive, so the interchange value is negated.
            add_motion_filter(clipitem, -rotation)
        if mirrored:
            add_links(clipitem, video_id, audio_id, index)

    for index, audio in enumerate(audio_events, start=1):
        asset = assets[audio["asset_id"]]
        audio_id = f"audio-{audio['event_id']}"
        start = frame(audio["timeline_start_seconds"], fps)
        end = start + frame(audio["duration_seconds"], fps)
        source_in = frame(audio["source_start_seconds"], fps)
        source_out = source_in + frame(audio["duration_seconds"], fps)
        clipitem = add(audio_track, "clipitem", attributes={"id": audio_id})
        add(clipitem, "masterclipid", f"master-{asset['asset_id']}")
        add(clipitem, "name", f"{audio['event_id']} — {asset['filename']}")
        add(clipitem, "enabled", "TRUE")
        add(clipitem, "duration", math.ceil(asset["duration_seconds"] * fps))
        add_rate(clipitem, fps)
        add(clipitem, "start", start)
        add(clipitem, "end", end)
        add(clipitem, "in", source_in)
        add(clipitem, "out", source_out)
        if asset["asset_id"] not in seen_files:
            add_file_node(clipitem, asset, fps)
            seen_files.add(asset["asset_id"])
        else:
            add(clipitem, "file", attributes={"id": f"file-{asset['asset_id']}"})
        sourcetrack = add(clipitem, "sourcetrack")
        add(sourcetrack, "mediatype", "audio")
        add(sourcetrack, "trackindex", 1)
        add_audio_level_filter(clipitem, audio.get("volume_db"))
        if mirrored:
            add_links(clipitem, f"video-{video_events[index - 1]['event_id']}",
                      audio_id, index)

    overlays = broll_events(plan)
    broll_xml_track = None
    if overlays:
        broll_xml_track = add(video_parent, "track")
        for event in overlays:
            asset = assets[event["asset_id"]]
            start = frame(event["timeline_start_seconds"], fps)
            end = start + frame(event["duration_seconds"], fps)
            source_in = frame(event["source_start_seconds"], fps)
            source_out = source_in + frame(event["duration_seconds"], fps)
            clipitem = add(
                broll_xml_track, "clipitem",
                attributes={"id": f"video-{event['event_id']}"},
            )
            add(clipitem, "masterclipid", f"master-{asset['asset_id']}")
            add(clipitem, "name", f"{event['event_id']} — {asset['filename']}")
            add(clipitem, "enabled", "TRUE")
            add(clipitem, "duration", math.ceil(asset["duration_seconds"] * fps))
            add_rate(clipitem, fps)
            add(clipitem, "start", start)
            add(clipitem, "end", end)
            add(clipitem, "in", source_in)
            add(clipitem, "out", source_out)
            if asset["asset_id"] not in seen_files:
                add_file_node(clipitem, asset, fps)
                seen_files.add(asset["asset_id"])
            else:
                add(clipitem, "file", attributes={"id": f"file-{asset['asset_id']}"})
            sourcetrack = add(clipitem, "sourcetrack")
            add(sourcetrack, "mediatype", "video")
            add(sourcetrack, "trackindex", 2)

    title_track = add(video_parent, "track")
    for event in track(plan, "title")["events"]:
        start = frame(event["timeline_start_seconds"], fps)
        duration = frame(event["duration_seconds"], fps)
        generator = add(title_track, "generatoritem", attributes={"id": event["event_id"]})
        add(generator, "name", event["text"])
        add(generator, "duration", duration)
        add_rate(generator, fps)
        add(generator, "start", start)
        add(generator, "end", start + duration)
        add(generator, "in", 0)
        add(generator, "out", duration)
        add(generator, "enabled", "TRUE")
        effect = add(generator, "effect")
        add(effect, "name", "Text")
        add(effect, "effectid", "Text")
        add(effect, "effectcategory", "Text")
        add(effect, "effecttype", "generator")
        add(effect, "mediatype", "video")
        for parameter_id, name, value in (
            ("str", "Text", event["text"]),
            ("font", "Font", "Liberation Sans Bold"),
            ("size", "Size", "0.055" if event["event_id"] == "t01_hook" else "0.05"),
            ("center", "Center", "0 0.78"),
        ):
            parameter = add(effect, "parameter")
            add(parameter, "parameterid", parameter_id)
            add(parameter, "name", name)
            add(parameter, "value", value)

    add(video_track, "enabled", "TRUE")
    add(video_track, "locked", "FALSE")
    if broll_xml_track is not None:
        add(broll_xml_track, "enabled", "TRUE")
        add(broll_xml_track, "locked", "FALSE")
    add(title_track, "enabled", "TRUE")
    add(title_track, "locked", "FALSE")
    add(audio_track, "enabled", "TRUE")
    add(audio_track, "locked", "FALSE")
    add(audio_track, "outputchannelindex", 1)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(XMEML_PATH, encoding="UTF-8", xml_declaration=True)
    # Premiere/FCP exports carry the xmeml DOCTYPE; keep it for maximum
    # importer compatibility (verified against DaVinci Resolve 21).
    raw = XMEML_PATH.read_text(encoding="utf-8")
    if "<!DOCTYPE xmeml>" not in raw:
        declaration, body = raw.split("\n", 1)
        XMEML_PATH.write_text(
            f"{declaration}\n<!DOCTYPE xmeml>\n{body}", encoding="utf-8"
        )


def validate_exports(plan):
    fps = plan["project"]["fps"]
    expected_duration = plan["project"]["duration_seconds"]
    expected_video = len(track(plan, "video")["events"])
    expected_audio = len(track(plan, "audio")["events"])
    expected_titles = len(track(plan, "title")["events"])
    expected_broll = len(broll_events(plan))
    expected_tracks = 3 if expected_broll else 2
    checks = []

    timeline = otio.adapters.read_from_file(str(OTIO_PATH))
    checks.append(
        {
            "check": "otio_round_trip",
            "pass": abs(timeline.duration().to_seconds() - expected_duration) < 1 / fps,
            "detail": f"duration={timeline.duration().to_seconds():.3f}s tracks={len(timeline.tracks)} markers={len(timeline.tracks.markers)}",
        }
    )
    otio_broll = (
        len([item for item in timeline.tracks[2] if isinstance(item, otio.schema.Clip)])
        if expected_broll and len(timeline.tracks) > 2
        else 0
    )
    checks.append(
        {
            "check": "otio_structure",
            "pass": len(timeline.tracks) == expected_tracks
            and len(timeline.tracks[0]) == expected_video
            and len(timeline.tracks[1]) == expected_audio
            and otio_broll == expected_broll
            and len(timeline.tracks.markers) == expected_titles,
            "detail": f"video={len(timeline.tracks[0])} audio={len(timeline.tracks[1])} broll={otio_broll} title_markers={len(timeline.tracks.markers)}",
        }
    )

    root = ET.parse(XMEML_PATH).getroot()
    sequence = root.find("sequence")
    xml_video = sequence.findall("./media/video/track[1]/clipitem")
    xml_broll = (
        sequence.findall("./media/video/track[2]/clipitem") if expected_broll else []
    )
    xml_titles = sequence.findall(
        f"./media/video/track[{3 if expected_broll else 2}]/generatoritem"
    )
    xml_audio = sequence.findall("./media/audio/track/clipitem")
    sequence_duration = int(sequence.findtext("duration"))
    checks.append(
        {
            "check": "xmeml_parse_and_duration",
            "pass": sequence_duration == frame(expected_duration, int(fps)),
            "detail": f"duration_frames={sequence_duration}",
        }
    )
    checks.append(
        {
            "check": "xmeml_structure",
            "pass": len(xml_video) == expected_video
            and len(xml_audio) == expected_audio
            and len(xml_broll) == expected_broll
            and len(xml_titles) == expected_titles,
            "detail": f"video={len(xml_video)} audio={len(xml_audio)} broll={len(xml_broll)} titles={len(xml_titles)}",
        }
    )
    expected_rotations = [
        str(-(event.get("reframe") or {}).get("rotation_degrees", 0))
        for event in track(plan, "video")["events"]
        if (event.get("reframe") or {}).get("rotation_degrees", 0)
    ]
    rotation_values = [
        item.text
        for item in root.findall(".//parameter[parameterid='rotation']/value")
    ]
    checks.append(
        {
            "check": "xmeml_rotation",
            "pass": rotation_values == expected_rotations,
            "detail": f"rotation_values={rotation_values} expected={expected_rotations}",
        }
    )
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "valid": all(item["pass"] for item in checks),
        "checks": checks,
        "outputs": {"otio": str(OTIO_PATH), "xmeml": str(XMEML_PATH)},
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["valid"]:
        raise SystemExit(json.dumps(report, indent=2))
    return report


def main():
    import argparse

    global PLAN_PATH, INVENTORY_PATH, MEDIA_ROOT, OUTPUT_DIR
    global SEQUENCE_NAME, SEQUENCE_ID, OTIO_PATH, XMEML_PATH, REPORT_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--basename", default="timeline")
    args = parser.parse_args()

    PLAN_PATH = args.plan.resolve()
    INVENTORY_PATH = args.inventory.resolve()
    MEDIA_ROOT = args.media_root.resolve()
    OUTPUT_DIR = args.output_dir.resolve()
    if args.name:
        SEQUENCE_NAME = args.name
    if args.basename:
        SEQUENCE_ID = f"{args.basename}-sequence"
        OTIO_PATH = OUTPUT_DIR / f"{args.basename}.otio"
        XMEML_PATH = OUTPUT_DIR / f"{args.basename}-davinci.xml"
        REPORT_PATH = OUTPUT_DIR / f"{args.basename}-timeline-validation.json"

    plan = load_json(PLAN_PATH)
    inventory = load_json(INVENTORY_PATH)
    assets = {item["asset_id"]: item for item in inventory["assets"]}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_otio(plan, assets)
    export_xmeml(plan, assets)
    report = validate_exports(plan)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
