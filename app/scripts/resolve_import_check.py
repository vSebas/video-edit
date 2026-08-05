"""XMEML import verification for the daily-skeleton-a timeline.

Run from DaVinci Resolve's built-in console (Workspace -> Console -> Py3):

    exec(open('/home/saveas/Documents/video-editing/app/scripts/resolve_import_check.py').read())

The free Linux edition only executes scripts from this console; the `resolve`
object is predefined there.
"""

import json

ROOT = "/home/saveas/Documents/video-editing"
XML = f"{ROOT}/runtime/projects/daily-skeleton-a/outputs/timeline-davinci.xml"
PLAN = f"{ROOT}/runtime/projects/daily-skeleton-a/plan/edit-plan.json"

project_manager = resolve.GetProjectManager()  # noqa: F821 - console builtin
project = project_manager.GetCurrentProject()
if project is None:
    project = project_manager.CreateProject("xmeml-import-check")

media_pool = project.GetMediaPool()
timeline = media_pool.ImportTimelineFromFile(
    XML, {"timelineName": "Daily Skeleton A - import check"}
)
if not timeline:
    print("IMPORT FAILED: Resolve rejected the XMEML file")
else:
    plan = json.load(open(PLAN))
    fps = plan["project"]["fps"]
    expected = plan["tracks"][0]["events"]
    items = timeline.GetItemListInTrack("video", 1) or []
    audio_items = timeline.GetItemListInTrack("audio", 1) or []
    print("timeline:", timeline.GetName())
    print(
        "video tracks:", timeline.GetTrackCount("video"),
        "| audio tracks:", timeline.GetTrackCount("audio"),
    )
    print(f"clips in V1: {len(items)} (expected {len(expected)})")
    print(f"clips in A1: {len(audio_items)} (expected {len(expected)})")
    all_ok = len(items) == len(expected)
    total_frames = 0
    for index, (item, event) in enumerate(zip(items, expected), start=1):
        name = item.GetName()
        duration = item.GetDuration()
        expected_duration = round(event["duration_seconds"] * fps)
        total_frames += duration
        ok = abs(duration - expected_duration) <= 1
        all_ok = all_ok and ok
        print(
            f"  {index}. {name} | {duration}f (expected {expected_duration}f) "
            f"{'OK' if ok else 'MISMATCH'}"
        )
    expected_total = round(plan["project"]["duration_seconds"] * fps)
    print(f"total V1 duration: {total_frames}f (expected {expected_total}f)")
    print(
        "VERDICT:",
        "PASS" if all_ok and abs(total_frames - expected_total) <= len(items) else "CHECK MISMATCHES ABOVE",
    )
