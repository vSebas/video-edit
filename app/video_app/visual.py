from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from .providers import (
    ChatClient,
    ProviderError,
    image_part,
    parse_json_content,
    text_part,
    video_part,
)
from .semantic import RISK_PATTERNS, utc_now

PROMPT_VERSION = "live-visual-v2-video"
SEGMENT_MAX_BYTES = 7_000_000
SEGMENT_SCALES = (480, 360)
MIN_MOMENT_SECONDS = 0.6
SCENE_THRESHOLD = 0.30
MIN_SHOT_SECONDS = 1.5
MAX_SHOT_SECONDS = 8.0
FRAME_MAX_EDGE = 640
AUTO_APPROVE_MIN_CONFIDENCE = 0.75

SYSTEM_PROMPT = (
    "You are a footage logger for a video editing assistant. You describe only "
    "what is directly visible in the supplied frames. Rules:\n"
    "- State visible subjects, actions, settings, and camera behavior.\n"
    "- Never guess identity, emotion, intent, brands, speech, or events between frames.\n"
    "- If frames are blurry or ambiguous, say so and lower your confidence.\n"
    "- Answer with a single JSON object only."
)


class VisualAnalysisError(RuntimeError):
    pass


def detect_shots(path: Path, duration: float) -> list[tuple[float, float]]:
    """Deterministic shot boundaries from ffmpeg scene scores, bounded by
    MIN/MAX shot length. Falls back to fixed windows when detection fails."""
    boundaries: list[float] = []
    command = [
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        for match in re.finditer(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)", result.stderr):
            boundaries.append(float(match.group(1)))

    cuts = [0.0]
    for value in sorted(set(boundaries)):
        if 0 < value < duration and value - cuts[-1] >= MIN_SHOT_SECONDS:
            cuts.append(value)
    cuts.append(duration)

    shots: list[tuple[float, float]] = []
    for start, end in zip(cuts, cuts[1:]):
        if end - start <= 0:
            continue
        if end - start < MIN_SHOT_SECONDS and shots:
            shots[-1] = (shots[-1][0], end)
            continue
        span = end - start
        pieces = max(1, int(span // MAX_SHOT_SECONDS) + (1 if span % MAX_SHOT_SECONDS > 0.5 else 0))
        step = span / pieces
        for index in range(pieces):
            shots.append((start + index * step, start + (index + 1) * step))
    if not shots:
        shots = [(0.0, duration)]
    return [(round(start, 3), round(end, 3)) for start, end in shots]


def extract_frame(path: Path, timestamp: float) -> bytes:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(timestamp, 0):.3f}", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale='min({FRAME_MAX_EDGE},iw)':'min({FRAME_MAX_EDGE},ih)':force_original_aspect_ratio=decrease",
        "-f", "image2", "-c:v", "mjpeg", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode or not result.stdout:
        raise VisualAnalysisError(
            f"Keyframe extraction failed for {path.name} at {timestamp:.2f}s"
        )
    return result.stdout


def extract_segment(path: Path, start: float, end: float) -> bytes | None:
    """Downscaled, silent H.264 segment of the shot for native-video model
    input. Returns None if it cannot be kept under the payload budget."""
    for scale in SEGMENT_SCALES:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(start, 0):.3f}", "-t", f"{max(end - start, 0.1):.3f}",
            "-i", str(path),
            "-vf", f"scale={scale}:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-an", "-movflags", "+faststart+frag_keyframe+empty_moov",
            "-f", "mp4", "pipe:1",
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode == 0 and result.stdout and len(result.stdout) <= SEGMENT_MAX_BYTES:
            return result.stdout
    return None


def frame_timestamps(start: float, end: float) -> list[float]:
    span = end - start
    if span <= 0.5:
        return [start]
    return [start + span * fraction for fraction in (0.15, 0.5, 0.85)]


def risk_flags_for(caption: str) -> list[str]:
    return [name for name, pattern in RISK_PATTERNS if pattern.search(caption)]


RESPONSE_SHAPE = (
    'Respond with JSON: {"caption": "<2-3 factual sentences>", '
    '"visible_actions": ["<short action phrases>"], '
    '"on_screen_text": "<verbatim readable text or null>", '
    '"camera": "<static|handheld|pan|tilt|unclear>", '
    '"confidence": <0.0-1.0>, '
    '"best_moment": {"start_seconds": <float>, "end_seconds": <float>, '
    '"why": "<what makes this the strongest instant>"} or null, '
    '"moments": [{"start_seconds": <float>, "end_seconds": <float>, '
    '"label": "<short factual label>"}]}'
)


def describe_shot(
    client: ChatClient,
    path: Path,
    filename: str,
    start: float,
    end: float,
) -> tuple[dict, dict]:
    """Describe one shot, preferring native-video input (motion, order, and
    sub-shot timestamps) and falling back to sampled keyframes. Returns
    (parsed observation payload, raw exchange record)."""
    segment = None if end - start < 0.5 else extract_segment(path, start, end)
    if segment is not None:
        instruction = (
            f"This video is one shot from the clip '{filename}' "
            f"(covering {start:.2f}s to {end:.2f}s of the source). Describe it, "
            "and give timestamps RELATIVE TO THIS VIDEO for the strongest "
            "moment and up to 3 notable moments (peaks of action, gestures, "
            "reveals, eye contact).\n" + RESPONSE_SHAPE
        )
        content = [video_part(segment), text_part(instruction)]
        input_mode = "video"
    else:
        frames = [extract_frame(path, ts) for ts in frame_timestamps(start, end)]
        instruction = (
            f"These {len(frames)} frames were sampled in order from {start:.2f}s to "
            f"{end:.2f}s of the clip '{filename}'. Describe this shot; use null "
            "for best_moment since motion is not visible.\n" + RESPONSE_SHAPE
        )
        content = [text_part(instruction)] + [image_part(frame) for frame in frames]
        input_mode = "keyframes"
    response = client.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    parsed = parse_json_content(response["content"])
    if not isinstance(parsed, dict) or not str(parsed.get("caption", "")).strip():
        raise ProviderError(f"Shot description missing caption for {filename}")
    raw = {
        "filename": filename,
        "shot_start_seconds": start,
        "shot_end_seconds": end,
        "input_mode": input_mode,
        "prompt_version": PROMPT_VERSION,
        "model": response["model"],
        "content": response["content"],
        "usage": response["usage"],
    }
    return parsed, raw


def shot_moments(parsed: dict, start: float, end: float) -> list[dict]:
    """Convert model-reported segment-relative moments into absolute,
    clamped source ranges. The best moment is first."""
    span = end - start
    candidates = []
    best = parsed.get("best_moment")
    if isinstance(best, dict):
        candidates.append({**best, "label": best.get("why", "strongest moment"), "is_best": True})
    for item in parsed.get("moments") or []:
        if isinstance(item, dict):
            candidates.append({**item, "is_best": False})
    moments = []
    for item in candidates[:4]:
        try:
            rel_start = float(item["start_seconds"])
            rel_end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        absolute_start = min(max(start + rel_start, start), end)
        absolute_end = min(max(start + rel_end, start), end)
        if absolute_end - absolute_start < MIN_MOMENT_SECONDS:
            continue
        label = str(item.get("label", "")).strip() or "notable moment"
        if any(
            abs(existing["start"] - absolute_start) < 0.3
            and abs(existing["end"] - absolute_end) < 0.3
            for existing in moments
        ):
            continue
        moments.append(
            {
                "start": round(absolute_start, 3),
                "end": round(absolute_end, 3),
                "label": label,
                "is_best": bool(item.get("is_best")),
            }
        )
    return moments


def analyze_assets(
    client: ChatClient,
    assets: list[dict],
    media_root: Path,
    project_id: str,
    run_id: str,
) -> tuple[dict, list[dict]]:
    """Run live VLM analysis over video/image assets and return a
    schema-valid semantic-evidence.v1 document plus raw exchanges."""
    observations: list[dict] = []
    raw_records: list[dict] = []
    warnings: list[str] = []
    risk_count = 0
    sequence = 0

    for asset in assets:
        if asset.get("media_type") not in {"video", "image"}:
            continue
        path = (media_root / asset["source_path"]).resolve()
        if not path.is_file():
            warnings.append(f"Missing media file skipped: {asset['filename']}")
            continue
        duration = float(asset.get("duration_seconds") or 0.0)
        if asset["media_type"] == "image" or duration <= 0:
            shots = [(0.0, 0.0)]
        else:
            shots = detect_shots(path, duration)

        for start, end in shots:
            sequence += 1
            try:
                parsed, raw = describe_shot(
                    client, path, asset["filename"], start, end
                )
            except (ProviderError, VisualAnalysisError, json.JSONDecodeError) as exc:
                warnings.append(
                    f"Shot {asset['filename']} [{start:.2f}-{end:.2f}s] failed: {exc}"
                )
                continue
            raw_records.append(raw)
            caption = str(parsed["caption"]).strip()
            extras = []
            text = parsed.get("on_screen_text")
            if isinstance(text, str) and text.strip():
                extras.append(f"On-screen text: {text.strip()}")
            camera = parsed.get("camera")
            if isinstance(camera, str) and camera.strip() and camera != "unclear":
                extras.append(f"Camera: {camera.strip()}")
            if extras:
                caption = f"{caption} ({'; '.join(extras)})"
            try:
                confidence = min(max(float(parsed.get("confidence", 0.0)), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            flags = risk_flags_for(caption)
            if flags:
                risk_count += 1
            observations.append(
                {
                    "evidence_id": f"{run_id}-{sequence:04d}",
                    "clip_id": f"shot_{sequence:04d}",
                    "media_id": asset["asset_id"],
                    "asset_id": asset["asset_id"],
                    "filename": asset["filename"],
                    "raw_start_seconds": start,
                    "raw_end_seconds": end,
                    "start_seconds": start,
                    "end_seconds": min(end, duration) if duration else end,
                    "caption": caption,
                    "source": "model",
                    "normalization_status": "accepted",
                    "review_status": "pending",
                    "adjustments": [],
                    "rejection_reasons": [],
                    "risk_flags": flags,
                    "reviewed_caption": None,
                    "review_note": None,
                    "reviewed_at": None,
                    "model_confidence": confidence,
                    "evidence_type": "visual",
                }
            )
            for moment_index, moment in enumerate(
                shot_moments(parsed, start, end), start=1
            ):
                moment_caption = (
                    f"{'Best moment' if moment['is_best'] else 'Moment'} of this "
                    f"shot: {moment['label']}"
                )
                moment_flags = risk_flags_for(moment_caption)
                if moment_flags:
                    risk_count += 1
                observations.append(
                    {
                        "evidence_id": f"{run_id}-{sequence:04d}m{moment_index}",
                        "clip_id": f"shot_{sequence:04d}_m{moment_index}",
                        "media_id": asset["asset_id"],
                        "asset_id": asset["asset_id"],
                        "filename": asset["filename"],
                        "raw_start_seconds": moment["start"],
                        "raw_end_seconds": moment["end"],
                        "start_seconds": moment["start"],
                        "end_seconds": min(moment["end"], duration) if duration else moment["end"],
                        "caption": moment_caption,
                        "source": "model",
                        "normalization_status": "accepted",
                        "review_status": "pending",
                        "adjustments": [],
                        "rejection_reasons": [],
                        "risk_flags": moment_flags,
                        "reviewed_caption": None,
                        "review_note": None,
                        "reviewed_at": None,
                        "model_confidence": confidence,
                        "evidence_type": "visual",
                    }
                )

    analyzed_assets = [
        asset for asset in assets if asset.get("media_type") in {"video", "image"}
    ]
    normalized = {
        "schema_version": "semantic-evidence.v1",
        "generated_at": utc_now(),
        "project_id": project_id,
        "run_id": run_id,
        "provider": {
            "adapter": "owned-live-visual",
            "id": client.config.provider,
            "model": client.config.model,
        },
        "review_status": "pending",
        "safe_for_edit_plan": False,
        "summary": {
            "project_asset_count": len(assets),
            "provider_media_count": len(analyzed_assets),
            "mapped_media_count": len(analyzed_assets),
            "observation_count": len(observations),
            "accepted_range_count": len(observations),
            "rejected_count": 0,
            "clamped_count": 0,
            "risk_flagged_count": risk_count,
        },
        "unmapped_media": [],
        "warnings": warnings
        + [
            "Captions were produced by the owned live visual adapter from "
            "deterministic shot keyframes; risky claims remain pending review."
        ],
        "observations": observations,
    }
    return normalized, raw_records


def auto_review_decisions(normalized: dict) -> dict:
    """Approve unflagged, confident observations under an audited policy so
    daily use does not require reviewing every routine caption."""
    now = utc_now()
    decisions: dict[str, dict] = {}
    events: list[dict] = []
    for observation in normalized["observations"]:
        if observation["risk_flags"]:
            continue
        confidence = observation.get("model_confidence", 0.0)
        if confidence < AUTO_APPROVE_MIN_CONFIDENCE:
            continue
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "evidence_id": observation["evidence_id"],
            "action": "approve",
            "caption": observation["caption"],
            "note": (
                f"auto-approved (policy auto-live-v1): no risk flags, "
                f"model confidence {confidence:.2f}"
            ),
            "reviewed_at": now,
        }
        decisions[observation["evidence_id"]] = event
        events.append(event)
    return {
        "schema_version": "semantic-reviews.v1",
        "project_id": normalized["project_id"],
        "run_key": None,
        "updated_at": now,
        "decisions": decisions,
        "events": events,
    }
