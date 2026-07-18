#!/usr/bin/env python3
"""Translate edit-plan.v1 into a native OpenTake .opentake directory bundle."""

import json
import math
from pathlib import Path


POC_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = POC_ROOT / "artifacts/edit-plan.json"
INVENTORY_PATH = POC_ROOT / "artifacts/media-inventory.json"
OUTPUT_BUNDLE = POC_ROOT / "artifacts/timelines/morning-routine.opentake"


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def track(plan, kind):
    return next(item for item in plan["tracks"] if item["kind"] == kind)


def frames(seconds, fps):
    return round(seconds * fps)


def clip_base(event, media_type, source_clip_type, fps):
    return {
        "id": event["event_id"],
        "mediaRef": event.get("asset_id") or "",
        "mediaType": media_type,
        "sourceClipType": source_clip_type,
        "startFrame": frames(event["timeline_start_seconds"], fps),
        "durationFrames": frames(event["duration_seconds"], fps),
        "trimStartFrame": frames(event.get("source_start_seconds") or 0, fps),
        "trimEndFrame": 0,
        "speed": event.get("playback_rate", 1.0),
        "volume": 1.0,
        "fadeInFrames": 0,
        "fadeOutFrames": 0,
        "opacity": 1.0,
        "transform": {
            "centerX": 0.5,
            "centerY": 0.5,
            "width": 1.0,
            "height": 1.0,
            "rotation": 0.0,
            "flipHorizontal": False,
            "flipVertical": False,
        },
        "crop": {"top": 0.0, "left": 0.0, "bottom": 0.0, "right": 0.0},
    }


def main():
    plan = load_json(PLAN_PATH)
    inventory = load_json(INVENTORY_PATH)
    assets = {item["asset_id"]: item for item in inventory["assets"]}
    fps = int(plan["project"]["fps"])

    video_clips = []
    for event in track(plan, "video")["events"]:
        clip = clip_base(event, "video", "video", fps)
        asset_frames = math.ceil(assets[event["asset_id"]]["duration_seconds"] * fps)
        clip["trimEndFrame"] = max(
            0,
            asset_frames - clip["trimStartFrame"] - clip["durationFrames"],
        )
        clip["transform"]["rotation"] = (event.get("reframe") or {}).get(
            "rotation_degrees", 0
        )
        clip["linkGroupId"] = f"link-{event['event_id'][1:3]}"
        video_clips.append(clip)

    audio_clips = []
    for event in track(plan, "audio")["events"]:
        clip = clip_base(event, "audio", "video", fps)
        asset_frames = math.ceil(assets[event["asset_id"]]["duration_seconds"] * fps)
        clip["trimEndFrame"] = max(
            0,
            asset_frames - clip["trimStartFrame"] - clip["durationFrames"],
        )
        clip["volume"] = 10 ** ((event.get("volume_db") or 0.0) / 20.0)
        clip["linkGroupId"] = f"link-{event['event_id'][1:3]}"
        audio_clips.append(clip)

    title_clips = []
    for index, event in enumerate(track(plan, "title")["events"]):
        clip = clip_base(event, "text", "text", fps)
        clip["textContent"] = event["text"]
        clip["textStyle"] = {
            "fontName": "Liberation Sans Bold",
            "fontSize": 56.0 if index == 0 else 58.0,
            "fontScale": 1.0,
            "color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
            "alignment": "center",
            "shadow": {
                "enabled": True,
                "color": {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.85},
                "offsetX": 0.0,
                "offsetY": -2.0,
                "blur": 6.0,
            },
            "background": {
                "enabled": True,
                "color": {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.42},
            },
            "border": {
                "enabled": False,
                "color": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0},
            },
        }
        clip["transform"].update(
            {"centerX": 0.5, "centerY": 0.14, "width": 0.9, "height": 0.12}
        )
        title_clips.append(clip)

    timeline = {
        "fps": fps,
        "width": plan["project"]["width"],
        "height": plan["project"]["height"],
        "settingsConfigured": True,
        "tracks": [
            {
                "id": "titles",
                "type": "text",
                "muted": False,
                "hidden": False,
                "syncLocked": True,
                "clips": title_clips,
            },
            {
                "id": "v1",
                "type": "video",
                "muted": False,
                "hidden": False,
                "syncLocked": True,
                "clips": video_clips,
            },
            {
                "id": "a1",
                "type": "audio",
                "muted": False,
                "hidden": False,
                "syncLocked": True,
                "clips": audio_clips,
            },
        ],
    }

    manifest = {
        "version": 2,
        "entries": [
            {
                "id": asset["asset_id"],
                "name": asset["filename"],
                "type": "video",
                "source": {
                    "external": {
                        "absolutePath": str(
                            (POC_ROOT / asset["source_path"]).resolve()
                        )
                    }
                },
                "duration": asset["duration_seconds"],
                "sourceWidth": asset["video"]["width"],
                "sourceHeight": asset["video"]["height"],
                "sourceFPS": 30.0,
                "hasAudio": True,
            }
            for asset in assets.values()
        ],
        "folders": [],
    }

    OUTPUT_BUNDLE.mkdir(parents=True, exist_ok=True)
    (OUTPUT_BUNDLE / "project.json").write_text(
        json.dumps(timeline, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT_BUNDLE / "media.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(OUTPUT_BUNDLE)


if __name__ == "__main__":
    main()
