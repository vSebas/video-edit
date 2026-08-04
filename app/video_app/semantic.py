from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class SemanticEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenStorylineArtifacts:
    session_state: Path
    load_media: Path
    split_shots: Path
    understand_clips: Path


RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "brand_or_product_claim",
        re.compile(r"\b(chobani|new balance|nike|adidas|brand(?:ed)?)\b", re.I),
    ),
    (
        "intent_or_emotion_inference",
        re.compile(
            r"\b(searching|curious|peaceful|confused|startled|calm|relaxed|"
            r"appears? to|seem(?:s|ingly)?|likely|intended)\b",
            re.I,
        ),
    ),
    (
        "unverified_speech_claim",
        re.compile(r"\b(speaks?|speaking|talks?|talking|says?|dialogue)\b", re.I),
    ),
    (
        "identity_or_continuity_inference",
        re.compile(r"\b(same person|same day|recording session|continuous action)\b", re.I),
    ),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def latest_artifact(run_dir: Path, node_name: str) -> Path:
    directory = run_dir / node_name
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        raise SemanticEvidenceError(f"Missing OpenStoryline {node_name} artifact")
    return files[-1]


def locate_openstoryline_artifacts(run_dir: Path) -> OpenStorylineArtifacts:
    session_state = run_dir / "session_state.json"
    if not session_state.is_file():
        raise SemanticEvidenceError("Missing OpenStoryline session_state.json")
    return OpenStorylineArtifacts(
        session_state=session_state,
        load_media=latest_artifact(run_dir, "load_media"),
        split_shots=latest_artifact(run_dir, "split_shots"),
        understand_clips=latest_artifact(run_dir, "understand_clips"),
    )


def _payload(path: Path, key: str) -> list[dict[str, Any]]:
    data = load_json(path)
    payload = data.get("payload")
    values = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise SemanticEvidenceError(f"Invalid {path.name}: payload.{key} must be an array")
    return values


def _original_media_names(session: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (session.get("load_media") or {}).values():
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        name = item.get("name")
        if path and name:
            result[Path(path).stem] = str(name)
    return result


def _model_name(session: dict[str, Any], explicit_model: str | None) -> str:
    if explicit_model:
        return explicit_model
    config = session.get("custom_vlm_config") or {}
    for key in ("model", "model_name", "model_id"):
        if config.get(key):
            return str(config[key])
    return str(session.get("vlm_model_key") or "unknown")


def _risk_flags(caption: str) -> list[str]:
    return [name for name, pattern in RISK_PATTERNS if pattern.search(caption)]


def normalize_openstoryline_run(
    project: dict[str, Any],
    run_dir: Path,
    provider: str,
    explicit_model: str | None = None,
) -> tuple[dict[str, Any], OpenStorylineArtifacts]:
    artifacts = locate_openstoryline_artifacts(run_dir)
    session = load_json(artifacts.session_state)
    session_id = str(session.get("session_id") or run_dir.name)

    assets = project.get("inventory", {}).get("assets", [])
    assets_by_filename: dict[str, dict[str, Any]] = {}
    duplicate_filenames: set[str] = set()
    for asset in assets:
        filename = str(asset.get("filename") or "").casefold()
        if not filename:
            continue
        if filename in assets_by_filename:
            duplicate_filenames.add(filename)
        assets_by_filename[filename] = asset
    if duplicate_filenames:
        names = ", ".join(sorted(duplicate_filenames))
        raise SemanticEvidenceError(f"Project has ambiguous duplicate filenames: {names}")

    original_names = _original_media_names(session)
    loaded_media = _payload(artifacts.load_media, "media")
    loaded_media_by_id = {
        str(item.get("media_id")): item for item in loaded_media if item.get("media_id")
    }
    media_to_asset: dict[str, dict[str, Any]] = {}
    unmapped_media: list[dict[str, str]] = []
    for media_id, item in loaded_media_by_id.items():
        original_name = original_names.get(media_id)
        asset = assets_by_filename.get(original_name.casefold()) if original_name else None
        if asset:
            media_to_asset[media_id] = asset
        else:
            unmapped_media.append(
                {"media_id": media_id, "original_filename": original_name or "unknown"}
            )

    split_clips = _payload(artifacts.split_shots, "clips")
    clips_by_id = {
        str(item.get("clip_id")): item for item in split_clips if item.get("clip_id")
    }
    captions = _payload(artifacts.understand_clips, "clip_captions")

    observations: list[dict[str, Any]] = []
    for position, caption_item in enumerate(captions, start=1):
        clip_id = str(caption_item.get("clip_id") or "")
        caption = str(caption_item.get("caption") or "").strip()
        split_clip = clips_by_id.get(clip_id)
        caption_media_id = str((caption_item.get("source_ref") or {}).get("media_id") or "")
        split_ref = (split_clip or {}).get("source_ref") or {}
        media_id = str(split_ref.get("media_id") or caption_media_id)
        asset = media_to_asset.get(media_id)
        adjustments: list[str] = []
        rejection_reasons: list[str] = []

        if not caption:
            rejection_reasons.append("empty_caption")
        if split_clip is None:
            rejection_reasons.append("missing_split_range")
        if caption_media_id and media_id and caption_media_id != media_id:
            rejection_reasons.append("caption_split_media_mismatch")
        if asset is None:
            rejection_reasons.append("unmapped_source_asset")

        raw_start = float(split_ref.get("start", 0)) / 1000 if split_clip else 0.0
        raw_end = float(split_ref.get("end", 0)) / 1000 if split_clip else 0.0
        start = max(raw_start, 0.0)
        if start != raw_start:
            adjustments.append("start_clamped_to_zero")
        duration = float(asset.get("duration_seconds") or 0) if asset else 0.0
        end = min(raw_end, duration) if duration > 0 else raw_end
        if duration > 0 and end != raw_end:
            adjustments.append("end_clamped_to_source_duration")
        if end <= start:
            rejection_reasons.append("empty_or_inverted_range")
        if duration <= 0:
            rejection_reasons.append("source_duration_unavailable")

        observations.append(
            {
                "evidence_id": f"{session_id[:12]}-{position:04d}",
                "clip_id": clip_id or None,
                "media_id": media_id or None,
                "asset_id": asset.get("asset_id") if asset else None,
                "filename": asset.get("filename") if asset else original_names.get(media_id),
                "raw_start_seconds": round(raw_start, 6),
                "raw_end_seconds": round(raw_end, 6),
                "start_seconds": round(start, 6),
                "end_seconds": round(end, 6),
                "caption": caption,
                "source": "model",
                "normalization_status": "rejected" if rejection_reasons else "accepted",
                "review_status": "pending",
                "adjustments": adjustments,
                "rejection_reasons": rejection_reasons,
                "risk_flags": _risk_flags(caption),
            }
        )

    accepted = [item for item in observations if item["normalization_status"] == "accepted"]
    flagged = [item for item in accepted if item["risk_flags"]]
    clamped = [
        item
        for item in accepted
        if "end_clamped_to_source_duration" in item["adjustments"]
    ]
    warnings = [
        "Provider captions are candidate evidence and require review before planning.",
        "Speech, identity, intent, emotion, brand, and OCR claims require dedicated evidence.",
    ]
    if unmapped_media:
        warnings.append("One or more provider media IDs could not be mapped to project filenames.")
    if clamped:
        warnings.append("Provider shot endpoints beyond ffprobe duration were clamped.")

    normalized = {
        "schema_version": "semantic-evidence.v1",
        "generated_at": utc_now(),
        "project_id": project["project_id"],
        "run_id": session_id,
        "provider": {
            "adapter": "openstoryline",
            "id": provider,
            "model": _model_name(session, explicit_model),
        },
        "review_status": "pending",
        "safe_for_edit_plan": False,
        "summary": {
            "project_asset_count": len(assets),
            "provider_media_count": len(loaded_media_by_id),
            "mapped_media_count": len(media_to_asset),
            "observation_count": len(observations),
            "accepted_range_count": len(accepted),
            "rejected_count": len(observations) - len(accepted),
            "clamped_count": len(clamped),
            "risk_flagged_count": len(flagged),
        },
        "unmapped_media": unmapped_media,
        "warnings": warnings,
        "observations": observations,
    }
    return normalized, artifacts


def validate_semantic_evidence(value: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "document"
        raise SemanticEvidenceError(f"Normalized evidence schema error at {location}: {first.message}")
