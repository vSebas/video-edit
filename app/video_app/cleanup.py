"""Spanish dialogue cleanup as a first-class app capability (P1).

The brain computes conservative filler/dead-air candidate ranges from OUR
word-level transcript, mapped through the live OpenTake clip layout into
project frames; the hands apply the approved subset in one atomic
`ripple_delete_ranges`. Trial-proven logic promoted from
`app/scripts/opentake_cleanup.py`; candidates are revision-bound so an
apply can never target a timeline that changed after review.

"este"/"o sea" are legitimate Spanish words, so they only count as filler
with a trailing hesitation gap; pure hesitations always qualify. Whisper
tends to omit "eh"-type disfluencies entirely, so absence of candidates is
common on clean speech — the UI says so rather than implying failure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PURE_FILLERS = {"eh", "em", "mmm", "eee", "ehh", "emm"}
GAP_FILLERS = {"este", "o sea", "digamos", "como que"}
HESITATION_GAP = 0.35
DEAD_AIR_MIN = 1.2
DEAD_AIR_KEEP = 0.4
WORD_PAD = 0.04


class CleanupError(RuntimeError):
    pass


def clip_layout(readback: dict, bridge: dict, inventory: dict) -> list[dict]:
    """Live video clips joined to asset ids via the bridge media mapping."""
    asset_for_ref = {ref: asset for asset, ref in bridge.get("media", {}).items()}
    video_tracks = sorted(
        (t for t in readback.get("tracks", []) if t.get("type") == "video"),
        key=lambda t: t.get("trackIndex")
        if isinstance(t.get("trackIndex"), int) else 999,
    )
    clips = []
    # Dialogue lives on the primary track only; B-roll overlays are
    # picture-only and must never produce cut candidates.
    for track in video_tracks[:1]:
        for clip in track.get("clips", []):
            clips.append({
                "clipId": clip["clipId"],
                "asset_id": asset_for_ref.get(clip.get("mediaRef")),
                "startFrame": clip["startFrame"],
                "durationFrames": clip["durationFrames"],
                "trimStartFrame": clip.get("trimStartFrame", 0),
            })
    return sorted(clips, key=lambda c: c["startFrame"])


def candidates_for(words: dict, clips: list[dict], fps: int) -> list[dict]:
    """Filler + dead-air ranges inside placed source windows, project frames."""
    found = []
    for clip in clips:
        asset_words = words.get(clip["asset_id"]) or []
        src_start = clip["trimStartFrame"] / fps
        src_end = (clip["trimStartFrame"] + clip["durationFrames"]) / fps

        def to_timeline(src_a: float, src_b: float) -> tuple[int, int]:
            low = clip["startFrame"]
            high = clip["startFrame"] + clip["durationFrames"]
            a = clip["startFrame"] + round((src_a - src_start) * fps)
            b = clip["startFrame"] + round((src_b - src_start) * fps)
            return max(a, low), min(b, high)

        inside = [w for w in asset_words
                  if w["start"] >= src_start - 0.01 and w["end"] <= src_end + 0.01]
        for i, word in enumerate(inside):
            gap_after = (inside[i + 1]["start"] - word["end"]) if i + 1 < len(inside) \
                else src_end - word["end"]
            reason = None
            span = (word["start"], word["end"])
            if word["word"] in PURE_FILLERS:
                reason = f"muletilla «{word['word']}»"
            elif word["word"] in GAP_FILLERS and gap_after >= HESITATION_GAP:
                reason = f"muletilla «{word['word']}» + pausa {gap_after:.2f}s"
            else:
                bigram = (f"{word['word']} {inside[i + 1]['word']}"
                          if i + 1 < len(inside) else "")
                if bigram in GAP_FILLERS:
                    next_gap = (inside[i + 2]["start"] - inside[i + 1]["end"]
                                if i + 2 < len(inside) else src_end - inside[i + 1]["end"])
                    if next_gap >= HESITATION_GAP:
                        reason = f"muletilla «{bigram}» + pausa {next_gap:.2f}s"
                        span = (word["start"], inside[i + 1]["end"])
            if reason:
                a, b = to_timeline(span[0] - WORD_PAD, span[1] + WORD_PAD)
                if b > a:
                    found.append({
                        "frames": [a, b], "reason": reason, "clip": clip["clipId"],
                        "context": " ".join(x["word"] for x in inside[max(0, i - 2):i + 3]),
                    })
            if i + 1 < len(inside) and gap_after >= DEAD_AIR_MIN:
                a, b = to_timeline(word["end"] + DEAD_AIR_KEEP / 2,
                                   inside[i + 1]["start"] - DEAD_AIR_KEEP / 2)
                if b - a >= int(0.4 * fps):
                    found.append({
                        "frames": [a, b],
                        "reason": f"silencio {gap_after:.1f}s → {DEAD_AIR_KEEP}s",
                        "clip": clip["clipId"],
                        "context": f"…{word['word']} | {inside[i + 1]['word']}…",
                    })
    found.sort(key=lambda c: c["frames"][0])
    return found


def timeline_fingerprint(readback: dict) -> str:
    """Binds a candidate list to the exact timeline it was computed from —
    media identity and track placement included, not just geometry."""
    clips = [
        (t.get("type"), t.get("trackIndex"), c.get("clipId"),
         c.get("mediaRef"), c.get("startFrame"),
         c.get("durationFrames"), c.get("trimStartFrame", 0))
        for t in readback.get("tracks", []) for c in t.get("clips", [])
    ]
    header = (readback.get("fps"), readback.get("width"), readback.get("height"))
    return hashlib.sha256(
        json.dumps([header, sorted(clips, key=str)]).encode()
    ).hexdigest()[:16]


def transcript_words(runs_dir: Path) -> dict[str, list[dict]]:
    """Newest ASR run's words (with text) per asset, by manifest recency."""
    newest, newest_at = None, ""
    for manifest_path in runs_dir.glob("asr-live-*/manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("imported_at", "") > newest_at:
            newest, newest_at = manifest_path.parent, manifest["imported_at"]
    if newest is None:
        raise CleanupError("Run speech analysis before dialogue cleanup")
    words: dict[str, list[dict]] = {}
    data = json.loads((newest / "raw" / "transcripts.json").read_text())
    for record in data.get("transcripts", []):
        asset_words = words.setdefault(record["asset_id"], [])
        for segment in record.get("segments", []):
            for w in segment.get("words", []):
                asset_words.append({
                    "word": w["word"].strip().lower().strip(".,¿?¡!…"),
                    "start": w["start_seconds"],
                    "end": w["end_seconds"],
                })
    for asset_words in words.values():
        asset_words.sort(key=lambda w: w["start"])
    return words
