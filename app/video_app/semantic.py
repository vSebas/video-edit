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


# Captions describe Spanish-language footage and the model may write either
# language, so each hedge family lists its Spanish forms alongside the English.
RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "outcome_or_causality_inference",
        re.compile(
            r"\b(won|lost|achieved|resigned|because|managed to|failed to|"
            r"gan(?:[óo]|aron)|perdi(?:[óo]|eron)|logr(?:[óo]|aron)|"
            r"renunci(?:[óo]|aron)|porque|debido a|consigui(?:[óo]|eron)|"
            r"fracas(?:[óo]|aron)|termin(?:[óo]|aron) (?:de|con))\b",
            re.I,
        ),
    ),
    (
        "brand_or_product_claim",
        re.compile(
            r"\b(chobani|new balance|nike|adidas|brand(?:ed)?|marca|logotipo|"
            r"logo)\b",
            re.I,
        ),
    ),
    (
        "intent_or_emotion_inference",
        re.compile(
            r"\b(searching|curious|peaceful|confused|startled|calm|relaxed|"
            r"appears? to|seem(?:s|ingly)?|likely|intended|enjoy(?:s|ing)?|"
            r"wants?|tr(?:y|ies|ying) to|proud|worried|excited|"
            r"parece(?:n|ría)?|aparenta|probablemente|quiz[áa]s?|tal vez|"
            r"emocionad[oa]s?|content[oa]s?|nervios[oa]s?|tranquil[oa]s?|"
            r"confundid[oa]s?|curios[oa]s?|feliz|felices|triste|"
            r"quiere(?:n)?|intenta(?:n)?|disfruta(?:n)?|preocupad[oa]s?|"
            r"orgullos[oa]s?|se siente(?:n)?|molest[oa]s?|frustrad[oa]s?)\b",
            re.I,
        ),
    ),
    (
        "unverified_speech_claim",
        re.compile(
            r"\b(speaks?|speaking|talks?|talking|says?|dialogue|explains?|"
            r"asks?|answers?|whispers?|tells?|"
            r"habla(?:n|ndo)?|dice(?:n)?|diciendo|conversa(?:n|ndo)?|"
            r"comenta(?:n|ndo)?|di[áa]logo|narra(?:n|ndo)?|explica(?:n|ndo)?|"
            r"pregunta(?:n|ndo)?|responde(?:n|iendo)?|cuenta(?:n|ndo)?|"
            r"susurra(?:n|ndo)?|se oye(?:n)?|voz|voces)\b",
            re.I,
        ),
    ),
    (
        "identity_or_continuity_inference",
        re.compile(
            r"\b(same person|same day|recording session|continuous action|"
            r"same (?:woman|man|girl|boy|group)|returns?|later that|"
            r"la misma persona|el mismo d[íi]a|misma sesi[óo]n|"
            r"la misma (?:mujer|chica|persona)|el mismo (?:hombre|chico)|"
            r"contin[úu]a(?:n)?|regresa(?:n)?|m[áa]s tarde|despu[ée]s de)\b",
            re.I,
        ),
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
