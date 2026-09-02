"""Reference Style Intelligence — first vertical slice.

From the accepted design (docs/designs/SOCIAL_TREND_..._HANDOFF.md): given a
few reference short-form videos the user admires, extract reusable editing
grammar (style-observation.v1), aggregate it into a style-template.v1, score
it deterministically against each grounded concept (style-match.v1), and
condition idea generation on the chosen style. Style decides HOW a real
story is presented; evidence still decides WHAT happened — the grounding
gates are untouched.

Deliberately absent (gated by the design): Trend Scout, social providers,
clustering, music, preference ML.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import uuid
from pathlib import Path

from .providers import parse_json_content, video_part
from .semantic import utc_now
from .visual import detect_shots, extract_segment

STYLE_PROMPT_VERSION = "style-v1"

HOOK_TYPES = [
    "unexpected_result", "question", "bold_claim", "in_media_res",
    "greeting", "visual_spectacle", "problem_statement", "none",
]
NARRATIVE_LABELS = [
    "hook", "setup", "attempt", "failure", "debugging", "retry", "payoff",
    "reflection", "montage", "explainer", "daily_routine", "reveal",
]


class StyleError(RuntimeError):
    pass


# ------------------------------------------------------------------ #
# Deterministic extraction

def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise StyleError(f"ffprobe failed for {path.name}: {result.stderr[-200:]}")
    data = json.loads(result.stdout)
    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    fps = None
    if video and video.get("avg_frame_rate") not in (None, "0/0"):
        num, _, den = video["avg_frame_rate"].partition("/")
        try:
            fps = round(float(num) / float(den or 1), 3)
        except (ValueError, ZeroDivisionError):
            fps = None
    return {
        "duration": float(data.get("format", {}).get("duration") or 0.0),
        "width": int(video["width"]) if video else None,
        "height": int(video["height"]) if video else None,
        "fps": fps,
    }


def _speech_ratio(path: Path, duration: float) -> float:
    """1 - (silence fraction), via ffmpeg silencedetect. Deterministic and
    cheap; a proxy for dialogue density, not a transcription."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    silence = 0.0
    start = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.rsplit("silence_start:", 1)[1].strip())
            except ValueError:
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(
                    line.rsplit("silence_end:", 1)[1].split("|")[0].strip()
                )
                silence += max(0.0, end - start)
            except ValueError:
                pass
            start = None
    if start is not None and duration:
        silence += max(0.0, duration - start)
    if not duration:
        return 0.0
    return round(min(1.0, max(0.0, 1.0 - silence / duration)), 3)


def deterministic_observation(path: Path) -> tuple[dict, dict]:
    """(deterministic block, source block) for a reference video."""
    probe = _probe(path)
    duration = probe["duration"]
    if duration <= 0:
        raise StyleError(f"{path.name}: no readable duration")
    shots = detect_shots(path, duration)
    lengths = sorted(end - start for start, end in shots if end > start)
    if not lengths:
        lengths = [duration]
    quantiles = (
        statistics.quantiles(lengths, n=10) if len(lengths) >= 3 else None
    )
    deterministic = {
        "shot_count": len(lengths),
        "median_shot_seconds": round(statistics.median(lengths), 2),
        "shot_seconds_p10": round(quantiles[0], 2) if quantiles else None,
        "shot_seconds_p90": round(quantiles[-1], 2) if quantiles else None,
        "cuts_per_minute": round(max(0, len(lengths) - 1) / (duration / 60), 1),
        "speech_ratio": _speech_ratio(path, duration),
    }
    source = {
        "label": path.name,
        "duration_seconds": round(duration, 2),
        "width": probe["width"],
        "height": probe["height"],
        "fps": probe["fps"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    }
    return deterministic, source


# ------------------------------------------------------------------ #
# Semantic extraction (one VLM call per reference)

def semantic_observation(client, path: Path, duration: float) -> dict:
    """Editing-grammar reading of the reference. The model describes HOW the
    video is edited and told — never a source of story content."""
    segment = extract_segment(path, 0.0, min(duration, 180.0), keep_audio=True)
    prompt = (
        "You are analyzing the EDITING GRAMMAR of a short-form video (a "
        "reference another creator made). Do not describe its story content "
        "in detail — describe how it is edited and told. Answer ONLY JSON:\n"
        "{"
        f"\"hook_type\": one of {HOOK_TYPES}, "
        "\"narrative_shape\": ordered array from "
        f"{NARRATIVE_LABELS} (3-8 items), "
        "\"tone\": 1-4 short lowercase adjectives, "
        "\"payoff_position\": \"early\"|\"mid\"|\"late\"|\"none\", "
        "\"broll_ratio_estimate\": fraction 0..1 of screen time that is "
        "cutaway/B-roll rather than the main speaker/subject, "
        "\"caption_style\": \"none\"|\"minimal\"|\"heavy\", "
        "\"uses_voiceover\": bool (narration over other footage), "
        "\"notes\": one sentence on the single most distinctive editing "
        "choice, \"confidence\": 0..1}"
    )
    response = client.chat(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            video_part(segment),
        ]}],
        json_object=True,
        temperature=0.0,
    )
    parsed = parse_json_content(response["content"])
    if not isinstance(parsed, dict):
        raise StyleError("style analysis returned no object")
    semantic = {
        "hook_type": parsed.get("hook_type")
        if parsed.get("hook_type") in HOOK_TYPES else None,
        "narrative_shape": [
            item for item in (parsed.get("narrative_shape") or [])
            if item in NARRATIVE_LABELS
        ][:8],
        "tone": [str(t).lower()[:24] for t in (parsed.get("tone") or [])][:4],
        "payoff_position": parsed.get("payoff_position")
        if parsed.get("payoff_position") in ("early", "mid", "late", "none")
        else None,
        "broll_ratio_estimate": None,
        "caption_style": parsed.get("caption_style")
        if parsed.get("caption_style") in ("none", "minimal", "heavy") else None,
        "uses_voiceover": parsed.get("uses_voiceover")
        if isinstance(parsed.get("uses_voiceover"), bool) else None,
        "notes": str(parsed.get("notes") or "")[:300] or None,
        "confidence": min(max(float(parsed.get("confidence") or 0.5), 0.0), 1.0),
    }
    try:
        ratio = float(parsed.get("broll_ratio_estimate"))
        semantic["broll_ratio_estimate"] = min(max(ratio, 0.0), 1.0)
    except (TypeError, ValueError):
        pass
    return semantic


def build_observation(deterministic: dict, source: dict, semantic: dict) -> dict:
    return {
        "schema_version": "style-observation.v1",
        "observation_id": f"obs-{uuid.uuid4().hex[:10]}",
        "generated_at": utc_now(),
        "source": source,
        "deterministic": deterministic,
        "semantic": semantic,
    }


# ------------------------------------------------------------------ #
# Aggregation: observations → template

def aggregate_template(name: str, observations: list[dict]) -> dict:
    """Merge observations into a style template. A single observation is
    promoted directly with its own confidence; several merge by median/mode.
    """
    if not observations:
        raise StyleError("A style needs at least one observation")

    def median_of(getter):
        values = [v for v in (getter(o) for o in observations) if v is not None]
        return round(statistics.median(values), 2) if values else None

    def mode_of(getter):
        values = [v for v in (getter(o) for o in observations) if v is not None]
        if not values:
            return None
        return statistics.mode(values)

    shapes = [o["semantic"].get("narrative_shape") or [] for o in observations]
    shape = max(shapes, key=len) if any(shapes) else []
    tones: list[str] = []
    for o in observations:
        for t in o["semantic"].get("tone") or []:
            if t not in tones:
                tones.append(t)
    speech = median_of(lambda o: o["deterministic"]["speech_ratio"])
    dialogue_density = (
        None if speech is None
        else "high" if speech > 0.7 else "low" if speech < 0.35 else "medium"
    )
    broll_ratio = median_of(lambda o: o["semantic"].get("broll_ratio_estimate"))
    payoff = mode_of(lambda o: o["semantic"].get("payoff_position"))
    grammar = {
        "narrative_shape": shape,
        "hook_type": mode_of(lambda o: o["semantic"].get("hook_type")),
        "tone": tones[:4],
        "median_shot_seconds": median_of(
            lambda o: o["deterministic"]["median_shot_seconds"]
        ),
        "cuts_per_minute": median_of(
            lambda o: o["deterministic"]["cuts_per_minute"]
        ),
        "broll_ratio": broll_ratio,
        "payoff_position": payoff,
        "jl_transitions": None,
        "uses_voiceover": mode_of(lambda o: o["semantic"].get("uses_voiceover")),
        "caption_style": mode_of(lambda o: o["semantic"].get("caption_style")),
    }
    confidence = round(
        min(
            statistics.median(
                [o["semantic"]["confidence"] for o in observations]
            ),
            0.55 if len(observations) == 1 else 1.0,
        ),
        2,
    )
    return {
        "schema_version": "style-template.v1",
        "style_id": f"style-{uuid.uuid4().hex[:8]}",
        "name": name,
        "generated_at": utc_now(),
        "source_observations": [o["observation_id"] for o in observations],
        "confidence": confidence,
        "grammar": grammar,
        "requirements": {
            "needs_payoff": payoff in ("early", "mid", "late") or None,
            "dialogue_density": dialogue_density,
            "min_distinct_shots": max(
                2, round(median_of(lambda o: o["deterministic"]["shot_count"]) or 2)
            ),
            "needs_broll": (broll_ratio or 0) > 0.15 or None,
        },
    }


# ------------------------------------------------------------------ #
# Concept × style matching (deterministic — the design's §5 core unit)

def _concept_editorial(concept: dict) -> dict:
    return concept.get("editorial") or {}


def match_concept(template: dict, concept: dict, inventory: dict) -> dict:
    grammar = template["grammar"]
    requirements = template["requirements"]
    editorial = _concept_editorial(concept)
    reasons: list[str] = []
    missing: list[str] = []

    # narrative fit: overlap of declared shapes (unknown -> neutral 0.5)
    concept_shape = set(editorial.get("narrative_shape") or [])
    style_shape = set(grammar.get("narrative_shape") or [])
    if concept_shape and style_shape:
        narrative_fit = round(
            len(concept_shape & style_shape) / len(style_shape | concept_shape), 2
        )
        if narrative_fit >= 0.5:
            reasons.append("la forma narrativa coincide con el estilo")
        else:
            missing.append("la historia no sigue la forma narrativa del estilo")
    else:
        narrative_fit = 0.5

    # payoff fit
    payoff_needed = bool(requirements.get("needs_payoff"))
    concept_payoff = editorial.get("payoff", {})
    has_payoff = bool(concept_payoff.get("present"))
    if payoff_needed and has_payoff:
        payoff_fit = 1.0
        reasons.append("hay un desenlace real que el estilo necesita")
    elif payoff_needed and not has_payoff:
        payoff_fit = 0.2
        missing.append("el estilo pide un desenlace y esta historia no lo tiene")
    else:
        payoff_fit = 0.8

    # pacing feasibility: enough distinct evidence to cut at the style's rate
    evidence_count = sum(
        len(beat.get("evidence") or []) for beat in concept.get("structure") or []
    )
    target = float(concept.get("target_duration_seconds") or 60)
    cuts_per_minute = grammar.get("cuts_per_minute")
    if cuts_per_minute:
        needed = max(2, round(cuts_per_minute * target / 60))
        pacing_feasibility = round(min(1.0, evidence_count / needed), 2)
        if pacing_feasibility < 0.6:
            missing.append(
                f"el estilo corta ~{cuts_per_minute:g} veces/min y la historia "
                f"solo tiene {evidence_count} momentos citados"
            )
        else:
            reasons.append("hay momentos suficientes para el ritmo del estilo")
    else:
        pacing_feasibility = 0.7

    # broll feasibility: unused video assets beyond the concept's own
    used = {
        e.get("asset_id")
        for beat in concept.get("structure") or []
        for e in beat.get("evidence") or []
    }
    spare = [
        a for a in inventory.get("assets", [])
        if a.get("media_type") == "video" and a["asset_id"] not in used
    ]
    if requirements.get("needs_broll"):
        broll_feasibility = 1.0 if spare else 0.3
        if spare:
            reasons.append(
                f"hay {len(spare)} clips libres para B-roll como pide el estilo"
            )
        else:
            missing.append(
                "el estilo usa mucho B-roll y no queda metraje libre — graba "
                "cortes de apoyo"
            )
    else:
        broll_feasibility = 0.9

    # tone (informational)
    concept_tone = set(editorial.get("tone") or [])
    style_tone = set(grammar.get("tone") or [])
    tone_fit = (
        round(len(concept_tone & style_tone) / len(style_tone), 2)
        if concept_tone and style_tone else None
    )

    score = round(
        0.35 * narrative_fit
        + 0.25 * payoff_fit
        + 0.25 * pacing_feasibility
        + 0.15 * broll_feasibility,
        2,
    )
    return {
        "schema_version": "style-match.v1",
        "style_id": template["style_id"],
        "concept_id": concept["concept_id"],
        "generated_at": utc_now(),
        "score": score,
        "components": {
            "narrative_fit": narrative_fit,
            "payoff_fit": payoff_fit,
            "pacing_feasibility": pacing_feasibility,
            "broll_feasibility": broll_feasibility,
            "tone_fit": tone_fit,
        },
        "reasons": reasons,
        "missing": missing,
    }


# ------------------------------------------------------------------ #
# Style-conditioned generation: template → planner guidance

def style_guidance(template: dict) -> str:
    """A structured guidance block for the concept writer. Style shapes the
    telling; grounding still owns the content — the writer's own rules
    enforce that."""
    grammar = template["grammar"]
    lines = [
        f"EDITING STYLE TARGET (from reference analysis, "
        f"\"{template['name']}\") — shape HOW the story is told; never "
        "invent content for it:",
    ]
    if grammar.get("narrative_shape"):
        lines.append(
            "- Narrative shape: " + " → ".join(grammar["narrative_shape"])
        )
    if grammar.get("hook_type"):
        lines.append(f"- Hook type: {grammar['hook_type']}")
    if grammar.get("tone"):
        lines.append("- Tone: " + ", ".join(grammar["tone"]))
    if grammar.get("median_shot_seconds"):
        lines.append(
            f"- Pacing: median shot ≈ {grammar['median_shot_seconds']:g}s "
            f"({grammar.get('cuts_per_minute') or '?'} cuts/min) — prefer "
            "more, shorter evidence ranges over few long ones"
        )
    if grammar.get("broll_ratio"):
        lines.append(
            f"- B-roll: ≈{round(grammar['broll_ratio'] * 100)}% of screen "
            "time — propose cutaways generously where footage supports them"
        )
    if grammar.get("payoff_position") in ("early", "mid", "late"):
        lines.append(f"- Payoff position: {grammar['payoff_position']}")
    if grammar.get("uses_voiceover"):
        lines.append(
            "- The style narrates over footage: recommend a voiceover in "
            "missing_shots if the story would benefit"
        )
    return "\n".join(lines)
