"""Try importing each candidate timeline XML and report which ones Resolve accepts.

Run from DaVinci Resolve's console (Workspace -> Console -> Py3):

    exec(open('/home/saveas/Documents/video-editing/app/scripts/resolve_try_imports.py').read())
"""

OUT = "/home/saveas/Documents/video-editing/runtime/projects/daily-skeleton-a/outputs"
CANDIDATES = [
    ("original", f"{OUT}/timeline-davinci.xml"),
    ("doctype", f"{OUT}/try1-doctype.xml"),
    ("doctype-no-title", f"{OUT}/try2-doctype-notitle.xml"),
    ("otio-fcp-adapter", f"{OUT}/try3-otio-fcp.xml"),
]

project_manager = resolve.GetProjectManager()  # noqa: F821 - console builtin
project = project_manager.GetCurrentProject()
if project is None:
    project = project_manager.CreateProject("xmeml-import-check")
media_pool = project.GetMediaPool()

for label, path in CANDIDATES:
    timeline = media_pool.ImportTimelineFromFile(path, {"timelineName": f"check-{label}"})
    if timeline:
        clips = timeline.GetItemListInTrack("video", 1) or []
        total = sum(item.GetDuration() for item in clips)
        print(f"{label}: IMPORTED - {len(clips)} clips in V1, {total} frames total")
    else:
        print(f"{label}: rejected")
