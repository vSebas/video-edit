"""Diagnose media support and retry timeline import with DNxHR proxies.

Run from DaVinci Resolve's console (Workspace -> Console -> Py3):

    exec(open('/home/saveas/Documents/video-editing/app/scripts/resolve_probe.py').read())
"""

ROOT = "/home/saveas/Documents/video-editing"
ORIGINAL = f"{ROOT}/Crayotter/crayotter-data/user_temp/IMG_0997.mp4"
PROXY = f"{ROOT}/runtime/projects/daily-skeleton-a/outputs/proxies/IMG_0997.mov"
XML = f"{ROOT}/runtime/projects/daily-skeleton-a/outputs/try4-proxies.xml"

project_manager = resolve.GetProjectManager()  # noqa: F821 - console builtin
project = project_manager.GetCurrentProject()
if project is None:
    project = project_manager.CreateProject("xmeml-import-check")
media_pool = project.GetMediaPool()

for label, path in (("original H.264 mp4", ORIGINAL), ("DNxHR proxy mov", PROXY)):
    items = media_pool.ImportMedia([path])
    if items:
        clip = items[0]
        print(f"{label}: IMPORTED ({clip.GetClipProperty('Resolution')} "
              f"{clip.GetClipProperty('Video Codec')} / {clip.GetClipProperty('Audio Codec')})")
    else:
        print(f"{label}: NOT IMPORTABLE")

timeline = media_pool.ImportTimelineFromFile(XML, {"timelineName": "check-proxies"})
if timeline:
    clips = timeline.GetItemListInTrack("video", 1) or []
    total = sum(item.GetDuration() for item in clips)
    print(f"proxy timeline: IMPORTED - {len(clips)} clips in V1, {total} frames "
          f"(expected 6 clips, ~937 frames)")
    for index, item in enumerate(clips, start=1):
        print(f"  {index}. {item.GetName()} {item.GetDuration()}f")
else:
    print("proxy timeline: rejected")
