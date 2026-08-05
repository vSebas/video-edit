"""Verify the currently open timeline against the compiled edit plan.

Run from DaVinci Resolve's console (Workspace -> Console -> Py3) after
importing the timeline:

    exec(open('/home/saveas/Documents/video-editing/app/scripts/resolve_verify_current.py').read())
"""

import json

PLAN = "/home/saveas/Documents/video-editing/runtime/projects/daily-skeleton-a/plan/edit-plan.json"

project = resolve.GetProjectManager().GetCurrentProject()  # noqa: F821
timeline = project.GetCurrentTimeline()
if timeline is None:
    print("No timeline is open - double-click the imported timeline first")
else:
    plan = json.load(open(PLAN))
    fps = plan["project"]["fps"]
    expected = plan["tracks"][0]["events"]
    items = timeline.GetItemListInTrack("video", 1) or []
    audio_items = timeline.GetItemListInTrack("audio", 1) or []
    print("timeline:", timeline.GetName())
    print(f"V1 clips: {len(items)} (expected {len(expected)}) | "
          f"A1 clips: {len(audio_items)} (expected {len(expected)})")
    all_ok = len(items) == len(expected)
    total = 0
    for index, (item, event) in enumerate(zip(items, expected), start=1):
        duration = item.GetDuration()
        expected_duration = round(event["duration_seconds"] * fps)
        total += duration
        ok = abs(duration - expected_duration) <= 1
        all_ok = all_ok and ok
        print(f"  {index}. {item.GetName()} | {duration}f (expected {expected_duration}f, "
              f"{event['asset_id']}) {'OK' if ok else 'MISMATCH'}")
    expected_total = round(plan["project"]["duration_seconds"] * fps)
    print(f"V1 total: {total}f (expected {expected_total}f)")
    resolution = f"{timeline.GetSetting('timelineResolutionWidth')}x{timeline.GetSetting('timelineResolutionHeight')}"
    print(f"timeline resolution: {resolution} @ {timeline.GetSetting('timelineFrameRate')}fps")
    print("VERDICT:", "PASS" if all_ok and abs(total - expected_total) <= len(items) else "CHECK MISMATCHES ABOVE")
