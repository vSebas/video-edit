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
        lines.append(
            f"- {marker}{item['asset_id']} [{item['start_seconds']:.2f}-{item['end_seconds']:.2f}] "
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
              "start_seconds": <number>,
              "end_seconds": <number>,
              "observed_content": "<what the evidence says happens here>",
              "confidence": <0.0-1.0>
            }}
          ],
          "cutaways": [
            {{
              "asset_id": "<existing asset id, DIFFERENT footage than the beat's evidence>",
              "start_seconds": <number>,
              "end_seconds": <number>,
              "observed_content": "<what this shot visibly shows>",
              "confidence": <0.0-1.0>
            }}
          ]
        }}
      ],
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
    _sanitize_concepts(document, project, evidence)
    if len(document["concepts"]) < 2:
        raise PlanningError(
            "Fewer than two valid concepts survived grounding checks; "
            "rerun concept generation"
        )
    return document


def _sanitize_concepts(
    document: dict, project: dict, evidence: list[dict] | None = None
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
    observed: dict[str, list[tuple[float, float]]] | None = None
    if evidence is not None:
        observed = {}
        for item in evidence:
            observed.setdefault(item["asset_id"], []).append(
                (item["start_seconds"], item["end_seconds"])
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
        concept.setdefault("platforms", ["instagram_reel", "tiktok"])
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
                if observed is not None and not any(
                    observed_start < end and observed_end > start
                    for observed_start, observed_end in observed.get(asset["asset_id"], [])
                ):
                    continue
                try:
                    confidence = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
                except (TypeError, ValueError):
                    confidence = 0.5
                spans.append(
                    {
                        "asset_id": asset["asset_id"],
                        "start_seconds": round(start, 3),
                        "end_seconds": round(end, 3),
                        "observed_content": str(item.get("observed_content", "")).strip()
                        or "Unlabeled evidence range.",
                        "confidence": confidence,
                    }
                )
            return spans

        beats = []
        for beat in concept.get("structure") or []:
            if not isinstance(beat, dict):
                continue
            spans = clean_spans(beat.get("evidence"))
            cutaways = clean_spans(beat.get("cutaways"))[:2]
            # a cutaway that repeats the beat's own footage shows nothing new
            primary_assets = {span["asset_id"] for span in spans}
            cutaways = [c for c in cutaways if c["asset_id"] not in primary_assets]
            if spans:
                beat_id = re.sub(
                    r"[^a-z0-9_]+", "_", str(beat.get("beat_id", "")).lower()
                ).strip("_") or f"beat_{len(beats) + 1}"
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
) -> dict:
    """Deterministically assemble edit-plan.v1 from grounded spans with
    linked video/audio events, a hook title, and optional B-roll cutaways
    laid over their beat's window."""
    if not spans:
        raise PlanningError("No usable evidence ranges to build a plan from")
    if speech_words:
        spans = snap_spans_to_speech(spans, speech_words, project)
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
        }
        video_events.append(
            {
                "event_id": f"v{index:02d}_{span['label']}"[:64],
                **base,
                "reframe": {
                    "mode": "fit",
                    "center_x": 0.5,
                    "center_y": 0.5,
                    "scale": 1.0,
                    "rotation_degrees": 0,
                    "manual_review": False,
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
    if cutaways:
        cursor_by_beat: dict[str, float] = {}
        for shot in cutaways:
            window = beat_windows.get(shot["label"])
            if window is None:
                continue  # the beat's own evidence was dropped entirely
            beat_start, beat_end = window
            position = cursor_by_beat.get(shot["label"], beat_start + 0.4)
            available = beat_end - 0.2 - position
            span_length = shot["source_end_seconds"] - shot["source_start_seconds"]
            duration = round(min(span_length, 4.0, available), 6)
            if duration < 0.8:
                continue  # no honest room left in this beat's window
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
                    "reframe": {
                        "mode": "fit",
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "scale": 1.0,
                        "rotation_degrees": 0,
                        "manual_review": False,
                    },
                    "transition_out": {"type": "cut", "duration_seconds": 0.0},
                    "text": None,
                    "volume_db": None,
                }
            )
            cursor_by_beat[shot["label"]] = round(position + duration + 0.3, 6)

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

    return {
        "schema_version": "edit-plan.v1",
        "generated_at": utc_now(),
        "benchmark_id": benchmark_id,
        "concept_id": concept_id,
        "revision": revision,
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
) -> dict:
    """Deterministically compile a sanitized concept into edit-plan.v1."""
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
    try:
        return build_plan(
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
        )
    except PlanningError as exc:
        raise PlanningError(f"The selected concept is not compilable: {exc}") from exc


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
    )
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
    for kind in ("video", "audio"):
        track = next((t for t in plan["tracks"] if t["kind"] == kind), None)
        if track is None:
            continue
        cursor = 0.0
        for event in sorted(
            track["events"], key=lambda e: e["timeline_start_seconds"]
        ):
            if event["timeline_start_seconds"] < cursor - 0.02:
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
