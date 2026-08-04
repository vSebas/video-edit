from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from .providers import ChatClient, ProviderError, image_part, parse_json_content, text_part
from .semantic import RISK_PATTERNS, utc_now

PROMPT_VERSION = "live-visual-v1"
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


def frame_timestamps(start: float, end: float) -> list[float]:
    span = end - start
    if span <= 0.5:
        return [start]
    return [start + span * fraction for fraction in (0.15, 0.5, 0.85)]


def risk_flags_for(caption: str) -> list[str]:
    return [name for name, pattern in RISK_PATTERNS if pattern.search(caption)]


def describe_shot(
    client: ChatClient,
    path: Path,
    filename: str,
    start: float,
    end: float,
) -> tuple[dict, dict]:
    """Returns (parsed observation payload, raw exchange record)."""
    frames = [extract_frame(path, ts) for ts in frame_timestamps(start, end)]
    instruction = (
        f"These {len(frames)} frames were sampled in order from {start:.2f}s to "
        f"{end:.2f}s of the clip '{filename}'. Describe this shot.\n"
        'Respond with JSON: {"caption": "<2-3 factual sentences>", '
        '"visible_actions": ["<short action phrases>"], '
        '"on_screen_text": "<verbatim readable text or null>", '
        '"camera": "<static|handheld|pan|tilt|unclear>", '
        '"confidence": <0.0-1.0>}'
    )
    content = [text_part(instruction)] + [image_part(frame) for frame in frames]
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
        "frame_timestamps": [round(ts, 3) for ts in frame_timestamps(start, end)],
        "prompt_version": PROMPT_VERSION,
        "model": response["model"],
        "content": response["content"],
        "usage": response["usage"],
    }
    return parsed, raw


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
