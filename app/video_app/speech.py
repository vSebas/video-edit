from __future__ import annotations

import math
from pathlib import Path

from .semantic import utc_now

PROMPT_VERSION = "local-asr-v1"
DEFAULT_MODEL_SIZE = "small"
# Flag a segment only when the transcript itself is doubtful: very low
# decode confidence OR Whisper's own no-speech detector fires. Ordinary
# non-English speech scores ~0.65-0.75 and must not be flagged.
MIN_SEGMENT_CONFIDENCE = 0.45
MAX_NO_SPEECH_PROBABILITY = 0.5


class SpeechAnalysisError(RuntimeError):
    pass


GPU_MODEL_SIZE = "large-v3"


def _load_model(model_size: str | None) -> tuple[object, str, str]:
    """Best available model: requested (or large-v3) on CUDA, falling back
    to the fast small/int8 CPU path. Returns (model, size, device)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechAnalysisError(
            "faster-whisper is not installed in this environment"
        ) from exc
    attempts = [
        (model_size or GPU_MODEL_SIZE, "cuda", "float16"),
        (model_size or DEFAULT_MODEL_SIZE, "cpu", "int8"),
    ]
    last_error: Exception | None = None
    for size, device, compute_type in attempts:
        try:
            return WhisperModel(size, device=device, compute_type=compute_type), size, device
        except Exception as exc:  # missing CUDA/toolkit raises RuntimeError variants
            last_error = exc
    raise SpeechAnalysisError(f"Could not load a Whisper model: {last_error}")


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
    model_size: str | None = None,
    progress=None,
) -> tuple[dict, list[dict]]:
    """Transcribe every asset with an audio stream into schema-valid
    semantic-evidence.v1 speech observations plus raw transcript records."""
    model, used_size, device = _load_model(model_size)
    observations: list[dict] = []
    raw_records: list[dict] = []
    warnings: list[str] = []
    sequence = 0
    eligible = 0

    audible = sum(1 for a in assets if a.get("audio"))
    for asset in assets:
        if not asset.get("audio"):
            continue
        eligible += 1
        if progress is not None:
            progress(eligible, audible)
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
                        and segment["no_speech_probability"] <= MAX_NO_SPEECH_PROBABILITY
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
            "model": f"whisper-{used_size}-{device}",
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
