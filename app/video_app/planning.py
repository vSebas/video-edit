from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from .providers import ChatClient, ProviderError, parse_json_content
from .semantic import utc_now

PROMPT_VERSION = "planning-v1"
MIN_EVENT_SECONDS = 0.4
# Grounding gate: share of a cut that approved evidence must cover, and the
# slack allowed at each edge for word snapping and moment padding.
MIN_SUPPORTED_FRACTION = 0.6
SUPPORT_EDGE_TOLERANCE = 0.5
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30
SOURCE_CONTEXT_MAX_CHARS = 2500

CONCEPT_SYSTEM_PROMPT = (
    "You are the creative director of a grounded video editing assistant. You "
    "propose short-form edits built ONLY from the supplied evidence. Rules:\n"
    "- Every beat must cite real asset ids and time ranges taken from the evidence.\n"
    "- Never invent content, dialogue, brands, or moments that are not in the evidence.\n"
    "- Weak coverage must be stated honestly in weaknesses and missing_shots.\n"
    "- missing_shots must contain concrete, filmable recording instructions.\n"
    "- Answer with a single JSON object only."
)


class PlanningError(RuntimeError):
    pass


def source_context_section(
    source_context: dict, max_chars: int = SOURCE_CONTEXT_MAX_CHARS
) -> str:
    lines = [
        "## Source context (derived, non-citable)",
        "Use this only for narrative order and relationships. Every concept "
        "citation must still come from the Evidence section below.",
    ]
    for asset in source_context.get("assets") or []:
        asset_id = str(asset.get("asset_id", "unknown"))
        summary = str(asset.get("summary", "")).strip()
        lines.append(f"### {asset_id}: {summary}")
        for event in asset.get("events") or []:
            anchors = ",".join(event.get("evidence_ids") or []) or "none"
            lines.append(
                f"- {event.get('event_id')} "
                f"[{float(event.get('start_seconds', 0)):.2f}-"
                f"{float(event.get('end_seconds', 0)):.2f}] "
                f"{event.get('label')}: {event.get('description')} "
                f"(evidence_ids: {anchors})"
            )
        for relationship in asset.get("relationships") or []:
            lines.append(
                f"- relation {relationship.get('kind')}: "
                f"{relationship.get('from_event')} -> "
                f"{relationship.get('to_event')}: "
                f"{relationship.get('description')}"
            )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    marker = "\n- … source context truncated"
    prefix = text[: max(max_chars - len(marker), 0)]
    if "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0]
    return (prefix + marker)[:max_chars]


def evidence_pack(
    project: dict, evidence: list[dict], source_context: dict | None = None
) -> str:
    """Compact text pack of technical facts and approved evidence, ordered by
    asset and source time, for planning prompts."""
    lines = []
    if source_context:
        lines.extend([source_context_section(source_context), ""])
    lines.extend([
        "## Assets (recorded timestamps are the REAL chronology — use them "
        "for ordering, time-of-day mood, and location continuity)"
    ])
    for asset in project.get("inventory", {}).get("assets", []):
        video = asset.get("video") or {}
        extras = []
        if asset.get("recorded_at"):
            extras.append(f"recorded {asset['recorded_at'][:16]}")
        if asset.get("location"):
            extras.append(
                f"GPS {asset['location']['latitude']:.4f},{asset['location']['longitude']:.4f}"
            )
        suffix = f" | {' | '.join(extras)}" if extras else ""
        lines.append(
            f"- {asset['asset_id']}: {asset['filename']} | {asset['media_type']} | "
            f"{asset['duration_seconds']:.1f}s | "
            f"{video.get('width')}x{video.get('height')}{suffix}"
        )
    lines.append("")
    lines.append(
        "## Evidence (asset [start-end]s type conf: description). Lines marked "
        "[UNVERIFIED] are unconfirmed claims: cite one only when it is clearly "
        "the strongest choice for a beat — the user will confirm or the editor "
        "will cut around it."
    )
    ordered = sorted(evidence, key=lambda item: (item["asset_id"], item["start_seconds"]))
    for item in ordered:
        marker = "" if item.get("verified", True) else "[UNVERIFIED] "
        eid = item.get("evidence_id")
        eid_tag = f"(id {eid}) " if eid else ""
        lines.append(
            f"- {marker}{eid_tag}{item['asset_id']} [{item['start_seconds']:.2f}-{item['end_seconds']:.2f}] "
            f"{item['evidence_type']} {item['confidence']:.2f}: {item['caption']}"
        )
    return "\n".join(lines)


LANGUAGE_NAMES = {"es": "Spanish", "en": "English"}


def language_instruction(footage_language: str | None) -> str:
    if not footage_language:
        return ""
    name = LANGUAGE_NAMES.get(footage_language, footage_language)
    return (
        f"\nThe footage speech is primarily {name} (possibly mixed with "
        "other languages). Write concept titles, hooks, and any on-screen "
        f"text in {name} so they match the creator's voice and audience. "
        "Keep quoted speech verbatim in its original language. Descriptions "
        "of structure may remain in English.\n"
    )


def generate_concepts(
    client: ChatClient,
    project: dict,
    evidence: list[dict],
    concept_count: int = 2,
    guidance: str | None = None,
    keep_concepts: list[dict] | None = None,
    footage_language: str | None = None,
    source_context: dict | None = None,
) -> dict:
    if not evidence:
        raise PlanningError("No approved semantic evidence is available for planning")
    prompt = project.get("prompt") or (
        "Create a concise, engaging vertical short-form video from this footage."
    )
    guidance_block = (
        f"\nDirection from the user for THIS round (weigh it heavily): {guidance.strip()}\n"
        if guidance and guidance.strip()
        else ""
    )
    kept_block = ""
    if keep_concepts:
        kept_lines = "\n".join(
            f"- {item['title']}: {item['topic']}" for item in keep_concepts
        )
        kept_block = (
            "\nThe user already KEPT these concepts — do not repeat their angle, "
            f"propose genuinely different ones:\n{kept_lines}\n"
        )
    pack = evidence_pack(project, evidence, source_context)
    instruction = f"""User request: {prompt}
{guidance_block}{kept_block}{language_instruction(footage_language)}
{pack}

Propose {concept_count} short-form video concepts. The FIRST concept must
follow the user's stated intention as faithfully as the footage allows — it
is the primary proposal. Any additional concept may explore a different
angle, but only when the dominant footage content clearly supports it; do
not invent tangents from minor evidence.

Duration and structure rules:
- Primary platform is Instagram Reels. Around 90 seconds is a loose guide,
  not a target or a cap: let the total duration emerge from the available
  clips and the narrative — whatever length the story earns is right, round
  or not. Never pad or trim just to land on a particular number.
- Scene count follows content quality, not clip count: include a scene when
  the footage for it is genuinely good or moves the story forward, and skip
  weak material even if that means fewer scenes. A rich day may earn many
  scenes; a thin one should not be stretched.
- Whenever the story is limited by what was captured — you skip a beat for
  weak coverage, a transition is missing, or extra material would clearly
  strengthen the narrative — you MUST tell the user through missing_shots:
  a concrete, recordable instruction with priority and a fallback. Silent
  compromises are not acceptable; the user wants to know what to record.
- Missing material is not only video: recommend VOICEOVERS (voz en off)
  when narration would strengthen the story — say what to talk about, the
  tone, and a target length, so the user can record it and drop it in.
- CUTAWAYS (optional, per beat): when a beat is someone talking and OTHER
  footage visibly shows what they talk about, add 1-2 "cutaways" — short
  ranges (1.5-4s) shown OVER the speech while its audio continues. Only
  cite footage the observations support, never the same shot the beat
  already uses, and skip cutaways entirely when nothing genuinely
  illustrates the speech.
Respond with JSON:
{{
  "footage_summary": "<2-4 factual sentences about what the footage visibly covers>",
  "concepts": [
    {{
      "concept_id": "concept_<slug>",
      "title": "<short title>",
      "topic": "<one sentence>",
      "audience": "<one sentence>",
      "platforms": ["instagram_reel", "tiktok"],
      "target_duration_seconds": <exact seconds derived from content, not rounded>,
      "hook": "<how the video opens and why it holds attention>",
      "structure": [
        {{
          "beat_id": "<slug>",
          "purpose": "<why this beat exists>",
          "target_duration_seconds": <number>,
          "evidence": [
            {{
              "asset_id": "<existing asset id>",
              "evidence_ids": ["<the (id ...) values of the evidence lines this range draws on — REQUIRED>"],
              "start_seconds": <number>,
              "end_seconds": <number>,
              "observed_content": "<what the evidence says happens here>",
              "confidence": <0.0-1.0>
            }}
          ],
          "cutaways": [
            {{
              "asset_id": "<existing asset id, DIFFERENT footage than the beat's evidence>",
              "evidence_ids": ["<the (id ...) values this cutaway draws on — REQUIRED>"],
              "start_seconds": <number>,
              "end_seconds": <number>,
              "observed_content": "<what this shot visibly shows>",
              "confidence": <0.0-1.0>
            }}
          ]
        }}
      ],
      "editorial": {{
        "archetype": "<e.g. research_progress, academic_day_vlog, technical_explainer>",
        "narrative_shape": ["<ordered labels from: hook, setup, attempt, failure, debugging, retry, payoff, reflection, montage, explainer, daily_routine, reveal>"],
        "hook_type": "<one of: unexpected_result, question, bold_claim, in_media_res, greeting, visual_spectacle, problem_statement, none>",
        "tone": ["<1-4 from: calm, casual, chaotic, cinematic, cozy, dramatic, educational, emotional, energetic, formal, funny, informative, inspirational, intense, minimal, nostalgic, personal, playful, raw, reflective, sarcastic, serious, upbeat, wholesome — use these ENGLISH labels even for Spanish footage>"],
        "dialogue_density": "low|medium|high",
        "payoff": {{"present": <bool>, "approximate_story_position": "early|mid|late|none"}}
      }},
      "strengths": ["<strings>"],
      "weaknesses": ["<honest strings>"],
      "missing_shots": [
        {{
          "purpose": "<what gap it fills>",
          "recording_instruction": "<concrete instruction: framing, action, length>",
          "priority": "required|recommended|optional",
          "fallback": "<how to edit around it if not recorded, or null>"
        }}
      ]
    }}
  ]
}}
Each concept needs at least 3 beats. Keep every cited range inside the asset's
duration and at least {MIN_EVENT_SECONDS}s long."""

    response = client.chat(
        [
            {"role": "system", "content": CONCEPT_SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ],
        json_object=True,
        temperature=0.6,
        # Reasoning-heavy models spend thousands of tokens thinking before
        # writing; a tight cap silently truncates their answer to nothing.
        max_tokens=24000,
    )
    generation_telemetry = response.get("telemetry") or {}
    try:
        parsed = parse_json_content(response["content"])
    except json.JSONDecodeError as exc:
        raise PlanningError(f"Concept response was not valid JSON: {exc}") from exc

    document = {
        "schema_version": "creative-concepts.v1",
        "generated_at": utc_now(),
        "benchmark_id": f"{project['project_id']}-auto-{PROMPT_VERSION}",
        "footage_summary": str(parsed.get("footage_summary", "")).strip(),
        "concepts": list(keep_concepts or []) + (parsed.get("concepts") or []),
        "provenance": {
            "adapter": "owned-planning",
            "provider": client.config.provider,
            "model": client.config.model,
            "prompt_version": PROMPT_VERSION,
            "evidence_count": len(evidence),
            "guidance": (guidance or "").strip() or None,
            "kept_concept_ids": [item["concept_id"] for item in keep_concepts or []],
            "source_context": bool(source_context),
        },
    }
    _sanitize_concepts(
        document, project, evidence,
        kept_ids={item["concept_id"] for item in keep_concepts or []},
    )
    if len(document["concepts"]) < 2:
        raise PlanningError(
            "Fewer than two valid concepts survived grounding checks; "
            "rerun concept generation"
        )
    document["generation_usage"] = {
        "model": client.config.model,
        "prompt_tokens": generation_telemetry.get("prompt_tokens"),
        "completion_tokens": generation_telemetry.get("completion_tokens"),
    }
    return document


_STOPWORDS = {
    # Spanish + English function words; enough to isolate content words
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
    "en", "con", "por", "para", "que", "se", "su", "sus", "es", "está",
    "están", "hay", "como", "más", "muy", "esta", "este", "esto", "sobre",
    "the", "a", "an", "of", "in", "on", "with", "and", "is", "are", "to",
    "at", "his", "her", "their", "there", "then", "while", "shows", "shot",
    "clip", "video", "person", "persona", "gente", "camera", "cámara",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-záéíóúüñ]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _claim_unsupported(claim: str, observed_text: str) -> bool:
    """True when a citation's description shares no content words with the
    observations it overlaps — the shape of an in-range hallucination.
    Conservative: short/abstract claims are given the benefit of the doubt."""
    claim_words = _content_words(claim)
    if len(claim_words) < 3:
        return False
    support_words = _content_words(observed_text)
    if not support_words:
        return False
    overlap = len(claim_words & support_words)
    return overlap / len(claim_words) < 0.15


def _sanitize_concepts(
    document: dict, project: dict, evidence: list[dict] | None = None,
    kept_ids: set[str] | None = None,
) -> None:
    """Deterministically enforce grounding: real assets, clamped ranges,
    minimum beat coverage. Invalid evidence or beats are dropped.

    A citation is checked against the observations the planner was given —
    approved and pending alike, since citing a pending moment is allowed and
    the user confirms it later. A citation overlapping no observation at all
    is a fabrication and goes."""
    assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }
    observed: dict[str, list[tuple[float, float, str]]] | None = None
    id_index: dict[str, tuple[str, float, float]] = {}
    if evidence is not None:
        observed = {}
        for item in evidence:
            observed.setdefault(item["asset_id"], []).append(
                (
                    item["start_seconds"],
                    item["end_seconds"],
                    str(item.get("caption") or ""),
                )
            )
            if item.get("evidence_id"):
                id_index[item["evidence_id"]] = (
                    item["asset_id"], item["start_seconds"],
                    item["end_seconds"],
                )
    valid_concepts = []
    used_ids: set[str] = set()
    for concept in document["concepts"]:
        if not isinstance(concept, dict):
            continue
        concept_id = re.sub(
            r"[^a-z0-9_]+", "_", str(concept.get("concept_id", "")).lower()
        ).strip("_")
        if not concept_id or concept_id in used_ids:
            concept_id = f"concept_{len(valid_concepts) + 1}"
        used_ids.add(concept_id)
        concept["concept_id"] = concept_id
        # The lineage contract is SERVER-OWNED, never inferred from model
        # output (a writer omitting every id must not demote itself to the
        # lenient path). Fresh concepts are under contract whenever the
        # evidence the server supplied carries ids; kept concepts keep the
        # marker they were stamped with at their own generation.
        is_kept = kept_ids is not None and concept_id in kept_ids
        if is_kept:
            concept_has_lineage = bool(concept.get("lineage_contract"))
        else:
            concept_has_lineage = bool(id_index)
            concept["lineage_contract"] = concept_has_lineage
        concept.setdefault("platforms", ["instagram_reel", "tiktok"])
        editorial = concept.get("editorial")
        if isinstance(editorial, dict):
            from .style_intelligence import (
                HOOK_TYPES, NARRATIVE_LABELS, TONE_LABELS,
            )

            def _label_list(value, allowed, cap):
                # same coercion the style analyzer uses: a bare string must
                # not iterate into characters, and only whitelisted labels
                # survive into style matching
                if isinstance(value, str):
                    value = [part.strip() for part in value.split(",")]
                if not isinstance(value, list):
                    return []
                return [
                    str(x).lower() for x in value
                    if isinstance(x, str) and str(x).lower() in allowed
                ][:cap]

            payoff = editorial.get("payoff") if isinstance(editorial.get("payoff"), dict) else {}
            concept["editorial"] = {
                "archetype": re.sub(
                    r"[^a-z0-9_]+", "_", str(editorial.get("archetype") or "").lower()
                ).strip("_")[:48] or None,
                "narrative_shape": _label_list(
                    editorial.get("narrative_shape"), NARRATIVE_LABELS, 8
                ),
                "hook_type": editorial.get("hook_type")
                if editorial.get("hook_type") in HOOK_TYPES else None,
                "tone": _label_list(editorial.get("tone"), TONE_LABELS, 4),
                "dialogue_density": editorial.get("dialogue_density")
                if editorial.get("dialogue_density") in ("low", "medium", "high")
                else None,
                "payoff": {
                    # strict: "false" (string) must not become True
                    "present": payoff.get("present") is True,
                    "approximate_story_position": payoff.get("approximate_story_position")
                    if payoff.get("approximate_story_position")
                    in ("early", "mid", "late", "none") else None,
                },
            }
        else:
            concept.pop("editorial", None)
        concept.setdefault("strengths", [])
        concept.setdefault("weaknesses", [])
        concept.setdefault("missing_shots", [])
        for shot in concept["missing_shots"]:
            if isinstance(shot, dict):
                shot.setdefault("priority", "recommended")
                shot.setdefault("fallback", None)
                if shot.get("priority") not in {"required", "recommended", "optional"}:
                    shot["priority"] = "recommended"

        def clean_spans(items) -> list[dict]:
            spans = []
            for item in items or []:
                asset = assets.get(item.get("asset_id")) if isinstance(item, dict) else None
                if asset is None:
                    continue
                if asset.get("media_type") != "video":
                    continue
                duration = float(asset.get("duration_seconds") or 0.0)
                try:
                    start = max(0.0, float(item["start_seconds"]))
                    end = min(float(item["end_seconds"]), duration or float(item["end_seconds"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if end - start < MIN_EVENT_SECONDS:
                    continue
                supporting = None
                if observed is not None:
                    supporting = [
                        caption
                        for observed_start, observed_end, caption
                        in observed.get(asset["asset_id"], [])
                        if observed_start < end and observed_end > start
                    ]
                    if not supporting:
                        continue
                try:
                    confidence = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
                except (TypeError, ValueError):
                    confidence = 0.5
                claim = (
                    str(item.get("observed_content", "")).strip()
                    or "Unlabeled evidence range."
                )
                cited = [
                    str(eid) for eid in (item.get("evidence_ids") or [])
                    if isinstance(eid, str)
                ] if isinstance(item, dict) else []
                valid_ids = [
                    eid for eid in cited
                    if id_index.get(eid)
                    and id_index[eid][0] == asset["asset_id"]
                    and id_index[eid][1] < end and id_index[eid][2] > start
                ]
                if not valid_ids and id_index:
                    if concept_has_lineage:
                        # a FRESH document whose writer omitted or invented
                        # ids for this citation: fail closed — attach-all
                        # over/under-authorizes (review 'decisive gap')
                        continue
                    # legacy document (predates lineage): recover identity
                    # deterministically by overlap; the risky-strict checks
                    # still bound what this can authorize
                    valid_ids = [
                        eid for eid, (aid, ostart, oend) in id_index.items()
                        if aid == asset["asset_id"]
                        and ostart < end and oend > start
                    ][:6]
                span = {
                    "asset_id": asset["asset_id"],
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                    "observed_content": claim,
                    "confidence": confidence,
                    **({"evidence_ids": sorted(valid_ids)} if valid_ids else {}),
                }
                # A citation can overlap a real observation yet DESCRIBE
                # something else — the last place a plausible hallucination
                # slips through. Flag claims that share no content words
                # with what was actually observed there.
                if supporting is not None and _claim_unsupported(
                    claim, " ".join(supporting)
                ):
                    span["needs_review"] = True
                spans.append(span)
            return spans

        beats = []
        for beat in concept.get("structure") or []:
            if not isinstance(beat, dict):
                continue
            spans = clean_spans(beat.get("evidence"))
            # a cutaway that repeats the beat's own footage shows nothing new
            primary_assets = {span["asset_id"] for span in spans}
            cutaways = [
                c for c in clean_spans(beat.get("cutaways"))
                if c["asset_id"] not in primary_assets
            ][:2]
            if spans:
                beat_id = re.sub(
                    r"[^a-z0-9_]+", "_", str(beat.get("beat_id", "")).lower()
                ).strip("_") or f"beat_{len(beats) + 1}"
                taken = {b["beat_id"] for b in beats}
                if beat_id in taken:  # duplicate ids merge cutaway windows
                    suffix = 2
                    while f"{beat_id}_{suffix}" in taken:
                        suffix += 1
                    beat_id = f"{beat_id}_{suffix}"
                duration = sum(
                    span["end_seconds"] - span["start_seconds"] for span in spans
                )
                sanitized_beat = {
                    "beat_id": beat_id,
                    "purpose": str(beat.get("purpose", "")).strip() or "Unlabeled beat.",
                    "target_duration_seconds": round(
                        float(beat.get("target_duration_seconds") or duration), 3
                    ),
                    "evidence": spans,
                }
                if cutaways:
                    sanitized_beat["cutaways"] = cutaways
                beats.append(sanitized_beat)
        concept["structure"] = beats
        if len(beats) >= 3:
            valid_concepts.append(concept)
    document["concepts"] = valid_concepts


def sanitize_spans(project: dict, items: list) -> list[dict]:
    """Deterministic grounding for cut lists from any source: real assets,
    clamped ranges, minimum length. Invalid entries are dropped."""
    assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }
    spans = []
    for item in items:
        asset = assets.get(item.get("asset_id")) if isinstance(item, dict) else None
        if asset is None:
            continue
        # Only footage belongs on the video track. A cited voiceover or photo
        # would compile into it and break the render or the timeline export.
        if asset.get("media_type") != "video":
            continue
        duration = float(asset.get("duration_seconds") or 0.0)
        try:
            start = max(0.0, float(item["source_start_seconds"]))
            end = float(item["source_end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if duration:
            end = min(end, duration)
        if end - start < MIN_EVENT_SECONDS:
            continue
        try:
            confidence = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5
        slug = re.sub(
            r"[^a-z0-9_]+", "_", str(item.get("label", "")).lower()
        ).strip("_") or f"cut_{len(spans) + 1}"
        spans.append(
            {
                "label": slug,
                "asset_id": asset["asset_id"],
                "source_start_seconds": round(start, 3),
                "source_end_seconds": round(end, 3),
                "intent": str(item.get("intent", "")).strip() or "Unlabeled cut.",
                "observed_content": str(item.get("observed_content", "")).strip()
                or "Unlabeled evidence range.",
                "confidence": confidence,
            }
        )
    return spans


WORD_SNAP_PADDING = 0.12


def snap_boundary(value: float, words: list[dict], is_end: bool) -> float:
    """If a cut boundary lands inside a spoken word, move it to the nearer
    word edge (padded away from the word) so speech is never clipped
    mid-word — the classic transcript-editing rule."""
    for word in words:
        if word["start_seconds"] < value < word["end_seconds"]:
            to_start = value - word["start_seconds"]
            to_end = word["end_seconds"] - value
            if is_end:
                # Finish the word unless it barely began at the cut point.
                if to_start < 0.15:
                    return max(word["start_seconds"] - WORD_SNAP_PADDING, 0.0)
                return word["end_seconds"] + WORD_SNAP_PADDING
            # Include the word from its start unless it is nearly over.
            if to_end < 0.15:
                return word["end_seconds"] + WORD_SNAP_PADDING
            return max(word["start_seconds"] - WORD_SNAP_PADDING, 0.0)
    return value


def snap_spans_to_speech(
    spans: list[dict], speech_words: dict[str, list[dict]], project: dict
) -> list[dict]:
    """Adjust span boundaries so cuts respect word edges. Reverts a snap
    that would invert or over-shrink the span."""
    durations = {
        asset["asset_id"]: float(asset.get("duration_seconds") or 0.0)
        for asset in project.get("inventory", {}).get("assets", [])
    }
    snapped = []
    for span in spans:
        words = speech_words.get(span["asset_id"]) or []
        start = span["source_start_seconds"]
        end = span["source_end_seconds"]
        if words:
            new_start = snap_boundary(start, words, is_end=False)
            new_end = snap_boundary(end, words, is_end=True)
            duration = durations.get(span["asset_id"]) or new_end
            new_start = max(0.0, new_start)
            new_end = min(new_end, duration) if duration else new_end
            if new_end - new_start >= MIN_EVENT_SECONDS:
                start, end = new_start, new_end
        snapped.append(
            {
                **span,
                "source_start_seconds": round(start, 3),
                "source_end_seconds": round(end, 3),
            }
        )
    return snapped


def build_plan(
    project: dict,
    spans: list[dict],
    *,
    concept_id: str,
    benchmark_id: str,
    hook_text: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    revision: int = 1,
    speech_words: dict[str, list[dict]] | None = None,
    cutaways: list[dict] | None = None,
    style_application: dict | None = None,
) -> dict:
    """Deterministically assemble edit-plan.v1 from grounded spans with
    linked video/audio events, a hook title, and optional B-roll cutaways
    laid over their beat's window. Measured style targets (when given)
    bind the cutaway layout toward the reference's B-roll coverage —
    always within grounded material, never inventing content."""
    if not spans:
        raise PlanningError("No usable evidence ranges to build a plan from")
    if speech_words:
        spans = snap_spans_to_speech(spans, speech_words, project)
    inventory_assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }

    def detected_rotation(asset_id: str) -> int:
        return int(
            inventory_assets.get(asset_id, {}).get("suggested_rotation_degrees")
            or 0
        )

    video_events = []
    audio_events = []
    beat_windows: dict[str, list[float]] = {}
    timeline = 0.0
    for span in spans:
        # Quantize to the frame grid so per-event frame rounding cannot
        # accumulate drift between the plan, render, and OTIO/XMEML exports.
        # The source start lands on the grid too: exporters round it to a
        # frame while ffmpeg seeks to the raw float, so an unquantized start
        # makes the render and the NLE timeline disagree by one frame.
        source_start = round(max(0, round(span["source_start_seconds"] * fps)) / fps, 6)
        raw_duration = span["source_end_seconds"] - source_start
        duration = max(1, round(raw_duration * fps)) / fps
        duration = round(duration, 6)
        index = len(video_events) + 1
        base = {
            "asset_id": span["asset_id"],
            "source_start_seconds": source_start,
            "source_end_seconds": round(source_start + duration, 6),
            "timeline_start_seconds": round(timeline, 6),
            "duration_seconds": duration,
            "playback_rate": 1.0,
            "intent": span["intent"],
            "observed_content": span["observed_content"],
            "confidence": span["confidence"],
            "transition_out": {"type": "cut", "duration_seconds": 0.0},
            "text": None,
            # claim lineage persists into the canonical plan so revisions
            # and restores can authorize by identity, not text similarity
            **(
                {"evidence_ids": span["evidence_ids"]}
                if span.get("evidence_ids") else {}
            ),
        }
        rotation = detected_rotation(span["asset_id"])
        video_events.append(
            {
                "event_id": f"v{index:02d}_{span['label']}"[:64],
                **base,
                "reframe": {
                    "mode": "fit",
                    "center_x": 0.5,
                    "center_y": 0.5,
                    "scale": 1.0,
                    "rotation_degrees": rotation,
                    # auto-detected rotation deserves a human glance
                    "manual_review": bool(rotation),
                },
                "volume_db": None,
            }
        )
        audio_events.append(
            {
                "event_id": f"a{index:02d}_{span['label']}"[:64],
                **base,
                "volume_db": 0.0,
            }
        )
        window = beat_windows.setdefault(
            span["label"], [round(timeline, 6), round(timeline, 6)]
        )
        window[1] = round(timeline + duration, 6)
        timeline = round(timeline + duration, 6)

    broll_events = []
    # A measured reference B-roll ratio is a GLOBAL coverage budget
    # (target × timeline), spent across the approved cutaways in order —
    # not a per-cutaway cap, which over-covers sparse targets and
    # under-covers dense ones. A zero target emits no B-roll at all.
    # Coverage can only come from the cutaways the sanitizer already
    # approved — no target invents footage; shortfall is reported, not
    # papered over.
    _owners = (style_application or {}).get("owners") or {}
    target_ratio = (
        ((style_application or {}).get("targets") or {}).get("broll_ratio")
        if _owners.get("broll_ratio") == "compiler" else None
    )
    budget = (
        target_ratio * timeline if target_ratio is not None else None
    )
    # each beat's fair share of the budget, proportional to its window —
    # allocation order must not decide which beats get coverage
    beat_quota: dict[str, float] = {}
    if budget is not None:
        covered = [
            label for label in beat_windows
            if any(c["label"] == label for c in cutaways or [])
        ]
        total_window = sum(
            beat_windows[label][1] - beat_windows[label][0] for label in covered
        )
        if total_window > 0:
            beat_quota = {
                label: budget
                * (beat_windows[label][1] - beat_windows[label][0])
                / total_window
                for label in covered
            }
    if cutaways and (budget is None or budget >= 0.8):
        cursor_by_beat: dict[str, float] = {}
        placed_seconds: dict[int, float] = {}
        beat_placed: dict[str, float] = {}
        for shot in cutaways:
            window = beat_windows.get(shot["label"])
            if window is None:
                continue  # the beat's own evidence was dropped entirely
            beat_start, beat_end = window
            position = cursor_by_beat.get(shot["label"], beat_start + 0.4)
            available = beat_end - 0.2 - position
            span_length = shot["source_end_seconds"] - shot["source_start_seconds"]
            if budget is not None:
                if budget < 0.8:
                    break  # budget spent — stop, don't approximate
                quota = beat_quota.get(shot["label"], 0.0)
                # a fair share below the 0.8s atomic minimum is unusable —
                # lift it to the floor so small budgets still place SOME
                # B-roll; the global budget keeps the total honest, and
                # later beats can absorb whatever earlier beats left
                effective = max(quota, 0.8) if budget >= 0.8 else quota
                cap = min(budget, effective, 0.7 * (beat_end - beat_start))
            else:
                cap = 4.0
            duration = round(min(span_length, cap, available), 6)
            if duration < 0.8:
                continue  # the leftover pass may still afford this one
            source_start = round(
                max(0, round(shot["source_start_seconds"] * fps)) / fps, 6
            )
            duration = round(max(1, round(duration * fps)) / fps, 6)
            index = len(broll_events) + 1
            broll_events.append(
                {
                    "event_id": f"bro-{index:02d}_{shot['label']}"[:64],
                    "asset_id": shot["asset_id"],
                    "source_start_seconds": source_start,
                    "source_end_seconds": round(source_start + duration, 6),
                    "timeline_start_seconds": round(position, 6),
                    "duration_seconds": duration,
                    "playback_rate": 1.0,
                    "intent": shot["intent"],
                    "observed_content": shot["observed_content"],
                    "confidence": shot["confidence"],
                    **(
                        {"evidence_ids": shot["evidence_ids"]}
                        if shot.get("evidence_ids") else {}
                    ),
                    "reframe": {
                        "mode": "fit",
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "scale": 1.0,
                        "rotation_degrees": detected_rotation(shot["asset_id"]),
                        "manual_review": bool(detected_rotation(shot["asset_id"])),
                    },
                    "transition_out": {"type": "cut", "duration_seconds": 0.0},
                    "text": None,
                    "volume_db": None,
                }
            )
            cursor_by_beat[shot["label"]] = round(position + duration + 0.3, 6)
            placed_seconds[id(shot)] = duration
            beat_placed[shot["label"]] = (
                beat_placed.get(shot["label"], 0.0) + duration
            )
            if budget is not None:
                budget -= duration
                beat_quota[shot["label"]] = max(
                    0.0, beat_quota.get(shot["label"], 0.0) - duration
                )

        # leftover pass: quotas kept pass one fair, but truncated or
        # skipped cutaways may have left real budget unspent — spend it on
        # any cutaway with unused SOURCE material and window room, under
        # the same honesty bounds (0.7-window per beat, approved footage)
        for shot in cutaways if budget is not None else []:
            if budget < 0.8:
                break
            window = beat_windows.get(shot["label"])
            if window is None:
                continue
            beat_start, beat_end = window
            already = placed_seconds.get(id(shot), 0.0)
            position = cursor_by_beat.get(shot["label"], beat_start + 0.4)
            available = beat_end - 0.2 - position
            span_length = (
                shot["source_end_seconds"] - shot["source_start_seconds"]
                - already
            )
            cap = min(
                budget,
                0.7 * (beat_end - beat_start)
                - beat_placed.get(shot["label"], 0.0),
            )
            duration = round(min(span_length, cap, available), 6)
            if duration < 0.8:
                continue
            source_start = round(
                max(
                    0,
                    round((shot["source_start_seconds"] + already) * fps),
                ) / fps, 6,
            )
            duration = round(max(1, round(duration * fps)) / fps, 6)
            index = len(broll_events) + 1
            broll_events.append(
                {
                    "event_id": f"bro-{index:02d}_{shot['label']}"[:64],
                    "asset_id": shot["asset_id"],
                    "source_start_seconds": source_start,
                    "source_end_seconds": round(source_start + duration, 6),
                    "timeline_start_seconds": round(position, 6),
                    "duration_seconds": duration,
                    "playback_rate": 1.0,
                    "intent": shot["intent"],
                    "observed_content": shot["observed_content"],
                    "confidence": shot["confidence"],
                    **(
                        {"evidence_ids": shot["evidence_ids"]}
                        if shot.get("evidence_ids") else {}
                    ),
                    "reframe": {
                        "mode": "fit",
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "scale": 1.0,
                        "rotation_degrees": detected_rotation(shot["asset_id"]),
                        "manual_review": bool(detected_rotation(shot["asset_id"])),
                    },
                    "transition_out": {"type": "cut", "duration_seconds": 0.0},
                    "text": None,
                    "volume_db": None,
                }
            )
            cursor_by_beat[shot["label"]] = round(position + duration + 0.3, 6)
            beat_placed[shot["label"]] = (
                beat_placed.get(shot["label"], 0.0) + duration
            )
            budget -= duration

    title_events = [
        {
            "event_id": "t01_hook",
            "asset_id": None,
            "source_start_seconds": None,
            "source_end_seconds": None,
            "timeline_start_seconds": 0.0,
            "duration_seconds": round(min(2.5, timeline), 3),
            "playback_rate": 1.0,
            "intent": "Open with the concept title as the text hook.",
            "observed_content": None,
            "confidence": 1.0,
            "text": hook_text.strip()[:70] or "Daily vlog",
            "volume_db": None,
        }
    ]

    style_block = None
    if style_application is not None:
        # the ENTIRE resolved contract travels into the plan — a styled
        # plan must be able to say which style produced it and what was
        # unsupported, not just two numbers
        style_block = {
            **style_application,
            # achieved_plan and broll_shortfall_seconds are derived state,
            # owned by refresh_style_application (called on every mutation)
            "achieved_plan": None,
            "broll_shortfall_seconds": None,
        }

    plan = {
        "schema_version": "edit-plan.v1",
        "generated_at": utc_now(),
        "benchmark_id": benchmark_id,
        "concept_id": concept_id,
        "revision": revision,
        **({"style_application": style_block} if style_block else {}),
        "project": {
            "width": width,
            "height": height,
            "fps": fps,
            "duration_seconds": timeline,
            "background_color": "#000000",
        },
        "tracks": [
            {"track_id": "v1", "kind": "video", "events": video_events},
            {"track_id": "a1", "kind": "audio", "events": audio_events},
            {"track_id": "t1", "kind": "title", "events": title_events},
            *(
                [{"track_id": "v2", "kind": "video", "role": "broll",
                  "events": broll_events}]
                if broll_events else []
            ),
        ],
    }
    refresh_style_application(plan)
    return plan


def compute_achieved_plan(plan: dict) -> dict | None:
    """Planned visual grammar from the plan's CURRENT tracks — the same
    quantities the reference was measured with. Recomputable after any
    mutation, so the number can never silently go stale. Visible cut
    boundaries are primary cut edges NOT hidden under a B-roll overlay,
    plus B-roll in/out edges; t=0 is the start of the video, not a cut."""
    duration = float((plan.get("project") or {}).get("duration_seconds") or 0)
    if not duration:
        return None
    primary_events: list[dict] = []
    broll_events: list[dict] = []
    for track in plan.get("tracks") or []:
        if track.get("kind") != "video":
            continue
        if track.get("role") == "broll":
            broll_events.extend(track.get("events") or [])
        else:
            primary_events.extend(track.get("events") or [])
    broll_windows = [
        (e["timeline_start_seconds"],
         e["timeline_start_seconds"] + e["duration_seconds"])
        for e in broll_events
    ]

    def hidden(t: float) -> bool:
        return any(start < t < end for start, end in broll_windows)

    ordered = sorted(
        primary_events, key=lambda e: e["timeline_start_seconds"]
    )
    boundaries = {
        round(e["timeline_start_seconds"], 3)
        for e in ordered
        if e["timeline_start_seconds"] > 0
        and not hidden(e["timeline_start_seconds"])
    } | {
        round(edge, 3)
        for start, end in broll_windows
        for edge in (start, end)
        if 0 < edge < duration
    }
    # a clip END followed by a gap (black) is a visible transition too —
    # pixel scene detection will count it, so the plan metric must
    for event, following in zip(ordered, ordered[1:] + [None]):
        end = round(
            event["timeline_start_seconds"] + event["duration_seconds"], 3
        )
        next_start = (
            following["timeline_start_seconds"] if following else duration
        )
        if end < round(next_start, 3) - 1e-3 and end < duration and not hidden(end):
            boundaries.add(end)
    broll_seconds = sum(e["duration_seconds"] for e in broll_events)
    return {
        "cuts_per_minute": round(len(boundaries) / (duration / 60), 1),
        "broll_ratio": round(min(1.0, broll_seconds / duration), 2),
    }


def refresh_style_application(plan: dict) -> None:
    """Recompute EVERY derived style number on a plan that carries a
    style block. Each plan mutation path must call this — a stored
    achieved_plan or shortfall that survives an edit unchanged is a lie."""
    block = plan.get("style_application")
    if not block:
        return
    achieved = compute_achieved_plan(plan)
    block["achieved_plan"] = achieved
    targets = block.get("targets") or {}
    owners = block.get("owners") or {}
    target_ratio = targets.get("broll_ratio")
    duration = float((plan.get("project") or {}).get("duration_seconds") or 0)
    if (
        owners.get("broll_ratio") == "compiler"
        and target_ratio is not None
        and achieved is not None
        and duration
    ):
        # exact seconds from the tracks — the two-decimal achieved ratio
        # would hide a real shortfall on near-target plans
        broll_seconds = sum(
            e["duration_seconds"]
            for track in plan.get("tracks") or []
            if track.get("kind") == "video" and track.get("role") == "broll"
            for e in track.get("events") or []
        )
        block["broll_shortfall_seconds"] = round(
            max(0.0, target_ratio * duration - broll_seconds), 2
        )


def _claim_is_risky(text: str) -> bool:
    from .semantic import RISK_PATTERNS

    return any(pattern.search(text) for _, pattern in RISK_PATTERNS)


def _risky_claim_supported(claim: str, supporting: str) -> bool:
    """Strict support test for RISKY claims: no brevity benefit-of-doubt
    anywhere ('ganó la carrera' must find real support or fail)."""
    content = [
        w for w in re.findall(r"[\wáéíóúñü]+", claim.lower()) if len(w) > 3
    ]
    support_words = set(re.findall(r"[\wáéíóúñü]+", supporting.lower()))
    return bool(content) and bool(support_words.intersection(content))


def claim_supported(
    span: dict,
    approved_captions: dict[str, list[tuple[float, float, str]]],
    review_sets: dict | None = None,
) -> bool:
    """Semantic half of grounding. AUTHORIZATION IS BY EVIDENCE IDENTITY:
    a citation whose evidence_ids are all approved compiles; any rejected
    or pending id fails it closed. The lexical overlap check remains only
    as (a) defense-in-depth against embellishment beyond the approved
    captions and (b) the legacy fallback for citations without lineage —
    where a short claim that trips a RISK pattern no longer gets the
    benefit of the doubt (review blocker 2: 'Ganó la carrera' must not
    pass by being brief)."""
    if span.get("needs_review"):
        return False
    claim = str(span.get("observed_content") or "")
    ids = span.get("evidence_ids") or []
    if review_sets is not None and ids:
        approved_ids = review_sets.get("approved") or {}
        rejected = review_sets.get("rejected") or set()
        if any(eid in rejected for eid in ids):
            return False
        if not all(eid in approved_ids for eid in ids):
            return False  # pending lineage: confirm it, then compile
        supporting = " ".join(approved_ids[eid] for eid in ids)
        if _claim_is_risky(claim) and not _risky_claim_supported(
            claim, supporting
        ):
            return False  # approved footage, embellished claim
        return True
    supporting = " ".join(
        caption
        for start, end, caption in approved_captions.get(span["asset_id"], [])
        if start < span["source_end_seconds"]
        and end > span["source_start_seconds"]
    )
    if _claim_is_risky(claim):
        return _risky_claim_supported(claim, supporting) and not (
            _claim_unsupported(claim, supporting)
        )
    return not _claim_unsupported(claim, supporting)


def title_blocked(
    title: str,
    supporting_text: str,
    user_authored: bool = False,
) -> bool:
    """Rendered language gate (review blockers 1+5, finding 5): a title is
    blocked only when it ASSERTS something risky (outcome, speech content,
    identity, emotion, brand) that no approved caption supports. Poetic or
    descriptive titles pass regardless of vocabulary overlap; user-typed
    titles are the user's own speech and are exempt."""
    if user_authored or not title:
        return False
    if not _claim_is_risky(title):
        return False
    support_words = set(re.findall(r"[\wáéíóúñü]+", supporting_text.lower()))
    content = [w for w in re.findall(r"[\wáéíóúñü]+", title.lower()) if len(w) > 3]
    if not content:
        return True
    return not support_words.intersection(content)


def span_supported(span: dict, approved_ranges: dict[str, list[tuple[float, float]]]) -> bool:
    """A cut is grounded when most of what it shows was actually observed.

    Testing the midpoint alone let a span run arbitrarily far past its
    evidence on both sides; requiring strict containment would reject cuts
    whose edges word snapping legitimately nudged outside the observation,
    so the test is how much of the span approved ranges cover.
    """
    start = span["source_start_seconds"]
    end = span["source_end_seconds"]
    length = end - start
    if length <= 0:
        return False
    covered = 0.0
    cursor = start
    for range_start, range_end in sorted(approved_ranges.get(span["asset_id"], [])):
        low = max(cursor, range_start - SUPPORT_EDGE_TOLERANCE)
        high = min(end, range_end + SUPPORT_EDGE_TOLERANCE)
        if high > low:
            covered += high - low
            cursor = high
        if cursor >= end:
            break
    return covered / length >= MIN_SUPPORTED_FRACTION


def compile_edit_plan(
    project: dict,
    concepts_document: dict,
    concept_id: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    speech_words: dict[str, list[dict]] | None = None,
    approved_ranges: dict[str, list[tuple[float, float]]] | None = None,
    style_application: dict | None = None,
    approved_captions: dict[str, list[tuple[float, float, str]]] | None = None,
    review_sets: dict | None = None,
) -> dict:
    """Deterministically compile a sanitized concept into edit-plan.v1.

    Two grounding gates, both fail-closed:
    - PIXELS: approved observation ranges must cover each cut (time gate);
    - CLAIMS: each citation's stated content must be supported by APPROVED
      captions in its range, and citations flagged needs_review are
      dropped — time overlap with unrelated approved evidence must never
      launder an unverified claim into the cut or its title."""
    concept = next(
        (
            item
            for item in concepts_document.get("concepts", [])
            if item["concept_id"] == concept_id
        ),
        None,
    )
    if concept is None:
        raise PlanningError(f"Unknown concept: {concept_id}")
    concept_under_contract = bool(concept.get("lineage_contract"))

    spans = []
    cutaways = []
    for beat in concept["structure"]:
        for evidence in beat["evidence"]:
            spans.append(
                {
                    "label": beat["beat_id"],
                    "asset_id": evidence["asset_id"],
                    "source_start_seconds": evidence["start_seconds"],
                    "source_end_seconds": evidence["end_seconds"],
                    "intent": beat["purpose"],
                    "observed_content": evidence["observed_content"],
                    "confidence": evidence["confidence"],
                    # trust lineage rides the span into the gates — losing
                    # these here was review finding 3
                    "evidence_ids": evidence.get("evidence_ids") or [],
                    "needs_review": bool(evidence.get("needs_review")),
                }
            )
        for shot in beat.get("cutaways") or []:
            cutaways.append(
                {
                    "label": beat["beat_id"],
                    "asset_id": shot["asset_id"],
                    "source_start_seconds": shot["start_seconds"],
                    "source_end_seconds": shot["end_seconds"],
                    "intent": f"b-roll: {beat['purpose']}"[:120],
                    "observed_content": shot["observed_content"],
                    "confidence": shot["confidence"],
                    "evidence_ids": shot.get("evidence_ids") or [],
                    "needs_review": bool(shot.get("needs_review")),
                }
            )
    if approved_ranges is not None:
        supported = [span for span in spans if span_supported(span, approved_ranges)]
        if not supported:
            raise PlanningError(
                "Every range in this concept relies on unconfirmed claims; "
                "confirm the flagged moments it uses and compile again"
            )
        spans = supported
        # cutaways are decoration, not story: unsupported ones just drop
        cutaways = [c for c in cutaways if span_supported(c, approved_ranges)]
    if approved_captions is not None:
        spans = [
            s for s in spans
            if claim_supported(s, approved_captions, review_sets)
        ]
        if not spans:
            raise PlanningError(
                "Ninguna escena de esta historia tiene su AFIRMACIÓN "
                "confirmada por observaciones aprobadas — confirma las "
                "afirmaciones marcadas y vuelve a compilar"
            )
        cutaways = [
            c for c in cutaways
            if claim_supported(c, approved_captions, review_sets)
        ]
        # the TITLE is rendered language: risky assertions need support
        title_support = " ".join(
            caption
            for span in spans
            for start, end, caption in approved_captions.get(span["asset_id"], [])
            if start < span["source_end_seconds"]
            and end > span["source_start_seconds"]
        )
        title = str(concept.get("title") or "")
        if title_blocked(title, title_support):
            raise PlanningError(
                f"El título «{title}» afirma algo (desenlace, dicho, "
                "identidad) que ninguna observación aprobada respalda — "
                "confirma la evidencia correspondiente o cambia el título"
            )
    try:
        compiled = build_plan(
            project,
            spans,
            cutaways=cutaways,
            concept_id=concept_id,
            benchmark_id=f"{project['project_id']}-auto-{PROMPT_VERSION}",
            hook_text=str(concept.get("title") or ""),
            width=width,
            height=height,
            fps=fps,
            speech_words=speech_words,
            style_application=style_application,
        )
    except PlanningError as exc:
        raise PlanningError(f"The selected concept is not compilable: {exc}") from exc
    else:
        if concept_under_contract:
            compiled["lineage_contract"] = True
        return compiled


REVISION_SYSTEM_PROMPT = (
    "You are the editor of a grounded video editing assistant. You revise an "
    "existing cut list according to the user's instruction. Rules:\n"
    "- Only use source ranges that appear in the supplied evidence or in the "
    "current cut list; you may trim, split, drop, reorder, or extend within "
    "an asset's duration.\n"
    "- Never invent content that is not in the evidence.\n"
    "- Keep the edit coherent: preserve cuts the instruction does not touch.\n"
    "- Answer with a single JSON object only."
)


def revise_plan(
    client: ChatClient,
    project: dict,
    plan: dict,
    evidence: list[dict],
    instruction: str,
    speech_words: dict[str, list[dict]] | None = None,
    footage_language: str | None = None,
    approved_ranges: dict[str, list[tuple[float, float]]] | None = None,
    approved_captions: dict[str, list[tuple[float, float, str]]] | None = None,
    review_sets: dict | None = None,
) -> tuple[dict, str]:
    """Revise the current plan per a natural-language instruction, keeping
    media analysis untouched. Returns (new plan, revision note)."""
    instruction = instruction.strip()
    if not instruction:
        raise PlanningError("A revision instruction is required")
    video_events = next(
        track["events"] for track in plan["tracks"] if track["kind"] == "video"
    )
    title_events = next(
        (track["events"] for track in plan["tracks"] if track["kind"] == "title"),
        [],
    )
    current_title = title_events[0]["text"] if title_events else ""
    current_lines = "\n".join(
        f"{index}. {event['asset_id']} "
        f"[{event['source_start_seconds']:.2f}-{event['source_end_seconds']:.2f}] "
        f"intent: {event['intent']}"
        for index, event in enumerate(video_events, start=1)
    )
    pack = evidence_pack(project, evidence)
    request = f"""{language_instruction(footage_language)}Current cut list (timeline order):
{current_lines}

Current title text: {current_title!r}

{pack}

Revision instruction from the user: {instruction}

Respond with JSON:
{{
  "video_events": [
    {{
      "label": "<short slug>",
      "asset_id": "<existing asset id>",
      "source_start_seconds": <number>,
      "source_end_seconds": <number>,
      "intent": "<why this cut is here>",
      "observed_content": "<what the evidence says happens here>",
      "confidence": <0.0-1.0>
    }}
  ],
  "title_text": "<updated on-screen hook title, or the current one>",
  "revision_note": "<one sentence describing exactly what you changed>"
}}
Return the FULL revised cut list in timeline order, not only the changed
events. Every range must stay at least {MIN_EVENT_SECONDS}s long."""

    response = client.chat(
        [
            {"role": "system", "content": REVISION_SYSTEM_PROMPT},
            {"role": "user", "content": request},
        ],
        json_object=True,
        temperature=0.3,
        max_tokens=6000,
    )
    try:
        parsed = parse_json_content(response["content"])
    except json.JSONDecodeError as exc:
        raise PlanningError(f"Revision response was not valid JSON: {exc}") from exc

    spans = sanitize_spans(project, parsed.get("video_events") or [])
    # lineage is inherited deterministically from the current plan — the
    # revision model is never trusted to assert identity
    plan_events = [
        event
        for track in plan.get("tracks", [])
        if track.get("kind") == "video" and track.get("role") != "broll"
        for event in track.get("events", [])
        if event.get("evidence_ids")
    ]
    envelopes = (review_sets or {}).get("envelopes") or {}
    for span in spans:
        inherited: list[str] = []
        for event in plan_events:
            if (
                event["asset_id"] == span["asset_id"]
                and event["source_start_seconds"] < span["source_end_seconds"]
                and event["source_end_seconds"] > span["source_start_seconds"]
            ):
                inherited.extend(event["evidence_ids"])
        # an id transfers only if ITS OWN observed envelope covers the new
        # range — overlap with the old event is not identity for the new one
        validated = [
            eid for eid in sorted(set(inherited))
            if (env := envelopes.get(eid)) is not None
            and env[0] == span["asset_id"]
            and env[1] < span["source_end_seconds"]
            and env[2] > span["source_start_seconds"]
        ] if envelopes else sorted(set(inherited))
        if validated:
            span["evidence_ids"] = validated
    if plan.get("lineage_contract"):
        # a cut moved to footage the current plan never covered has no
        # inheritable identity — under the contract that fails closed
        # instead of dropping to the lenient lexical path
        uncovered = [s2 for s2 in spans if not s2.get("evidence_ids")]
        spans = [s2 for s2 in spans if s2.get("evidence_ids")]
        if uncovered and not spans:
            raise PlanningError(
                "La revisión movió los cortes a material sin identidad de "
                "evidencia; el plan quedó sin cambios — pide el cambio de "
                "otra forma o regenera las ideas"
            )
    if not spans:
        raise PlanningError(
            "The revision produced no valid cuts; the plan was left unchanged"
        )
    if approved_ranges is not None:
        # The same grounding gate compilation applies: a revision may not
        # introduce footage the evidence never covered.
        supported = [span for span in spans if span_supported(span, approved_ranges)]
        if not supported:
            raise PlanningError(
                "The revision moved every cut outside the confirmed evidence; "
                "the plan was left unchanged"
            )
        spans = supported
    if approved_captions is not None:
        spans = [
            s for s in spans
            if claim_supported(s, approved_captions, review_sets)
        ]
        if not spans:
            raise PlanningError(
                "La revisión introdujo afirmaciones que ninguna observación "
                "aprobada respalda; el plan quedó sin cambios"
            )
        revised_title = str(parsed.get("title_text") or current_title)
        title_support = " ".join(
            caption
            for span in spans
            for start, end, caption in approved_captions.get(span["asset_id"], [])
            if start < span["source_end_seconds"]
            and end > span["source_start_seconds"]
        )
        current_title_event = next(
            (
                event
                for track in plan.get("tracks", [])
                if track.get("kind") == "title"
                for event in track.get("events", [])
            ),
            {},
        )
        unchanged_user_title = (
            revised_title.strip() == str(current_title_event.get("text") or "").strip()
            and bool(current_title_event.get("user_authored"))
        )
        if not unchanged_user_title and title_blocked(revised_title, title_support):
            raise PlanningError(
                f"La revisión propuso el título «{revised_title}», que "
                "afirma algo sin respaldo en observaciones aprobadas; el "
                "plan quedó sin cambios"
            )
    new_plan = build_plan(
        project,
        spans,
        concept_id=plan["concept_id"],
        benchmark_id=plan["benchmark_id"],
        hook_text=str(parsed.get("title_text") or current_title),
        width=plan["project"]["width"],
        height=plan["project"]["height"],
        fps=plan["project"]["fps"],
        revision=int(plan.get("revision", 1)) + 1,
        speech_words=speech_words,
        # the style contract survives a revision; its derived grammar is
        # recomputed from the NEW tracks by build_plan
        style_application={
            k: v for k, v in (plan.get("style_application") or {}).items()
            if k not in ("achieved_plan",)
        } or None,
    )
    if plan.get("lineage_contract"):
        new_plan["lineage_contract"] = True
    if approved_captions is not None and unchanged_user_title:
        # the user's own title keeps its exemption across revisions
        for track in new_plan.get("tracks", []):
            if track.get("kind") == "title":
                for event in track.get("events", []):
                    event["user_authored"] = True
    note = str(parsed.get("revision_note", "")).strip() or "Plan revised."
    return new_plan, note


def validate_edit_plan(plan: dict, schema_path: Path, project: dict) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(plan), key=lambda err: list(err.path)
    )
    if errors:
        first = errors[0]
        raise PlanningError(
            f"Edit plan schema violation at {'/'.join(str(p) for p in first.path)}: {first.message}"
        )
    assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }
    duration = plan["project"]["duration_seconds"]
    epsilon = 0.5 / float(plan["project"]["fps"] or 30)
    for kind in ("video", "audio"):
        track = next((t for t in plan["tracks"] if t["kind"] == kind), None)
        if track is None:
            continue
        cursor = 0.0
        for event in sorted(
            track["events"], key=lambda e: e["timeline_start_seconds"]
        ):
            if event["timeline_start_seconds"] < cursor - epsilon:
                raise PlanningError(
                    f"{kind} track overlaps itself at "
                    f"{event['timeline_start_seconds']:.3f}s"
                )
            cursor = event["timeline_start_seconds"] + event["duration_seconds"]
        if cursor > duration + 0.05:
            raise PlanningError(
                f"{kind} track covers {cursor:.3f}s, past the plan duration "
                f"{duration:.3f}s"
            )
    audio_tracks = [t for t in plan["tracks"] if t["kind"] == "audio"]
    if len(audio_tracks) > 2:
        raise PlanningError("At most one voiceover track beyond the primary audio")
    if len(audio_tracks) == 2:
        if audio_tracks[1].get("role") != "voiceover":
            raise PlanningError(
                "A second audio track must declare role 'voiceover'"
            )
        for event in audio_tracks[1]["events"]:
            asset = assets.get(event.get("asset_id"))
            if asset is not None and asset.get("media_type") != "audio":
                raise PlanningError(
                    f"Voiceover event {event['event_id']} must use an audio "
                    "asset"
                )
            if (event["timeline_start_seconds"] + event["duration_seconds"]
                    > duration + 0.05):
                raise PlanningError(
                    f"Voiceover event {event['event_id']} extends past the "
                    "plan duration"
                )
    for track in plan["tracks"]:
        if track.get("role") not in ("broll", "voiceover"):
            continue
        ordered = sorted(
            track["events"], key=lambda e: e["timeline_start_seconds"]
        )
        for previous, current in zip(ordered, ordered[1:]):
            if (current["timeline_start_seconds"]
                    < previous["timeline_start_seconds"]
                    + previous["duration_seconds"] - epsilon):
                raise PlanningError(
                    f"{track.get('role')} events {previous['event_id']} and "
                    f"{current['event_id']} overlap"
                )
    video_tracks = [t for t in plan["tracks"] if t["kind"] == "video"]
    if len(video_tracks) > 2:
        raise PlanningError("At most one B-roll track is supported beyond the primary")
    if len(video_tracks) == 2:
        primary, broll = video_tracks
        if broll.get("role") != "broll":
            raise PlanningError(
                "A second video track must declare role 'broll'"
            )
        primary_end = max(
            (e["timeline_start_seconds"] + e["duration_seconds"]
             for e in primary["events"]), default=0.0,
        )
        for event in broll["events"]:
            if (event["timeline_start_seconds"] + event["duration_seconds"]
                    > primary_end + 0.05):
                raise PlanningError(
                    f"B-roll event {event['event_id']} extends past the primary "
                    "track end — an overlay needs a base underneath"
                )
    for track in plan["tracks"]:
        for event in track["events"]:
            asset_id = event.get("asset_id")
            if asset_id is None:
                continue
            asset = assets.get(asset_id)
            if asset is None:
                raise PlanningError(f"Plan references unknown asset: {asset_id}")
            duration = float(asset.get("duration_seconds") or 0.0)
            if duration and event["source_end_seconds"] > duration + 0.05:
                raise PlanningError(
                    f"Event {event['event_id']} exceeds {asset_id} duration "
                    f"({event['source_end_seconds']:.2f}s > {duration:.2f}s)"
                )
