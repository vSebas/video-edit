from __future__ import annotations

import math
from pathlib import Path

from .semantic import utc_now

PROMPT_VERSION = "local-asr-v1"
DEFAULT_MODEL_SIZE = "small"
MIN_SEGMENT_CONFIDENCE = 0.30


class SpeechAnalysisError(RuntimeError):
    pass


def _load_model(model_size: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechAnalysisError(
            "faster-whisper is not installed in this environment"
        ) from exc
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_asset(model, path: Path) -> tuple[list[dict], dict]:
    segments, info = model.transcribe(
        str(path),
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
    )
    results = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        confidence = min(max(math.exp(segment.avg_logprob), 0.0), 1.0)
        results.append(
            {
                "start_seconds": round(segment.start, 3),
                "end_seconds": round(segment.end, 3),
                "text": text,
                "confidence": round(confidence, 4),
                "no_speech_probability": round(segment.no_speech_prob, 4),
                "words": [
                    {
                        "word": word.word.strip(),
                        "start_seconds": round(word.start, 3),
                        "end_seconds": round(word.end, 3),
                    }
                    for word in (segment.words or [])
                ],
            }
        )
    detection = {
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration_seconds": round(info.duration, 3),
    }
    return results, detection


def analyze_speech(
    assets: list[dict],
    media_root: Path,
    project_id: str,
    run_id: str,
    model_size: str = DEFAULT_MODEL_SIZE,
) -> tuple[dict, list[dict]]:
    """Transcribe every asset with an audio stream into schema-valid
    semantic-evidence.v1 speech observations plus raw transcript records."""
    model = _load_model(model_size)
    observations: list[dict] = []
    raw_records: list[dict] = []
    warnings: list[str] = []
    sequence = 0
    eligible = 0

    for asset in assets:
        if not asset.get("audio"):
            continue
        eligible += 1
        path = (media_root / asset["source_path"]).resolve()
        if not path.is_file():
            warnings.append(f"Missing media file skipped: {asset['filename']}")
            continue
        try:
            segments, detection = transcribe_asset(model, path)
        except Exception as exc:  # transcription library errors vary widely
            warnings.append(f"Transcription failed for {asset['filename']}: {exc}")
            continue
        raw_records.append(
            {
                "filename": asset["filename"],
                "asset_id": asset["asset_id"],
                "prompt_version": PROMPT_VERSION,
                "detection": detection,
                "segments": segments,
            }
        )
        if not segments:
            warnings.append(f"No speech detected in {asset['filename']}.")
            continue
        duration = float(asset.get("duration_seconds") or 0.0)
        for segment in segments:
            sequence += 1
            end = segment["end_seconds"]
            observations.append(
                {
                    "evidence_id": f"{run_id}-{sequence:04d}",
                    "clip_id": None,
                    "media_id": asset["asset_id"],
                    "asset_id": asset["asset_id"],
                    "filename": asset["filename"],
                    "raw_start_seconds": segment["start_seconds"],
                    "raw_end_seconds": end,
                    "start_seconds": segment["start_seconds"],
                    "end_seconds": min(end, duration) if duration else end,
                    "caption": f"Spoken ({detection['language']}): \"{segment['text']}\"",
                    "source": "model",
                    "normalization_status": "accepted",
                    "review_status": "pending",
                    "adjustments": [],
                    "rejection_reasons": [],
                    "risk_flags": (
                        []
                        if segment["confidence"] >= MIN_SEGMENT_CONFIDENCE
                        else ["low_confidence_transcription"]
                    ),
                    "reviewed_caption": None,
                    "review_note": None,
                    "reviewed_at": None,
                    "model_confidence": segment["confidence"],
                    "evidence_type": "speech",
                }
            )

    normalized = {
        "schema_version": "semantic-evidence.v1",
        "generated_at": utc_now(),
        "project_id": project_id,
        "run_id": run_id,
        "provider": {
            "adapter": "local-asr",
            "id": "faster-whisper",
            "model": f"whisper-{model_size}-int8-cpu",
        },
        "review_status": "pending",
        "safe_for_edit_plan": False,
        "summary": {
            "project_asset_count": len(assets),
            "provider_media_count": eligible,
            "mapped_media_count": eligible,
            "observation_count": len(observations),
            "accepted_range_count": len(observations),
            "rejected_count": 0,
            "clamped_count": 0,
            "risk_flagged_count": sum(
                1 for item in observations if item["risk_flags"]
            ),
        },
        "unmapped_media": [],
        "warnings": warnings
        + [
            "Transcripts were produced locally by faster-whisper; no audio "
            "left this machine. Word timings are stored in the raw record."
        ],
        "observations": observations,
    }
    return normalized, raw_records
