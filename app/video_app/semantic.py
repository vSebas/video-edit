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


def validate_semantic_evidence(value: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "document"
        raise SemanticEvidenceError(f"Normalized evidence schema error at {location}: {first.message}")
