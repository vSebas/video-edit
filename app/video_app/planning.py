from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from .providers import ChatClient, ProviderError, parse_json_content
from .semantic import utc_now

PROMPT_VERSION = "planning-v1"
MIN_EVENT_SECONDS = 0.4
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30

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


def evidence_pack(project: dict, evidence: list[dict]) -> str:
    """Compact text pack of technical facts and approved evidence, ordered by
    asset and source time, for planning prompts."""
    lines = ["## Assets"]
    for asset in project.get("inventory", {}).get("assets", []):
        video = asset.get("video") or {}
        lines.append(
            f"- {asset['asset_id']}: {asset['filename']} | {asset['media_type']} | "
            f"{asset['duration_seconds']:.1f}s | "
            f"{video.get('width')}x{video.get('height')}"
        )
    lines.append("")
    lines.append("## Evidence (asset [start-end]s type conf: description)")
    ordered = sorted(evidence, key=lambda item: (item["asset_id"], item["start_seconds"]))
    for item in ordered:
        lines.append(
            f"- {item['asset_id']} [{item['start_seconds']:.2f}-{item['end_seconds']:.2f}] "
            f"{item['evidence_type']} {item['confidence']:.2f}: {item['caption']}"
        )
    return "\n".join(lines)


def generate_concepts(
    client: ChatClient,
    project: dict,
    evidence: list[dict],
    concept_count: int = 2,
) -> dict:
    if not evidence:
        raise PlanningError("No approved semantic evidence is available for planning")
    prompt = project.get("prompt") or (
        "Create a concise, engaging vertical short-form video from this footage."
    )
    pack = evidence_pack(project, evidence)
    instruction = f"""User request: {prompt}

{pack}

Propose exactly {concept_count} genuinely different short-form video concepts.
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
      "target_duration_seconds": <20-90>,
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
        max_tokens=8000,
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
        "concepts": parsed.get("concepts") or [],
        "provenance": {
            "adapter": "owned-planning",
            "provider": client.config.provider,
            "model": client.config.model,
            "prompt_version": PROMPT_VERSION,
            "evidence_count": len(evidence),
        },
    }
    _sanitize_concepts(document, project)
    if len(document["concepts"]) < 2:
        raise PlanningError(
            "Fewer than two valid concepts survived grounding checks; "
            "rerun concept generation"
        )
    return document


def _sanitize_concepts(document: dict, project: dict) -> None:
    """Deterministically enforce grounding: real assets, clamped ranges,
    minimum beat coverage. Invalid evidence or beats are dropped."""
    assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }
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

        beats = []
        for beat in concept.get("structure") or []:
            if not isinstance(beat, dict):
                continue
            spans = []
            for item in beat.get("evidence") or []:
                asset = assets.get(item.get("asset_id")) if isinstance(item, dict) else None
                if asset is None:
                    continue
                duration = float(asset.get("duration_seconds") or 0.0)
                try:
                    start = max(0.0, float(item["start_seconds"]))
                    end = min(float(item["end_seconds"]), duration or float(item["end_seconds"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if end - start < MIN_EVENT_SECONDS:
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
            if spans:
                beat_id = re.sub(
                    r"[^a-z0-9_]+", "_", str(beat.get("beat_id", "")).lower()
                ).strip("_") or f"beat_{len(beats) + 1}"
                duration = sum(
                    span["end_seconds"] - span["start_seconds"] for span in spans
                )
                beats.append(
                    {
                        "beat_id": beat_id,
                        "purpose": str(beat.get("purpose", "")).strip() or "Unlabeled beat.",
                        "target_duration_seconds": round(
                            float(beat.get("target_duration_seconds") or duration), 3
                        ),
                        "evidence": spans,
                    }
                )
        concept["structure"] = beats
        if len(beats) >= 3:
            valid_concepts.append(concept)
    document["concepts"] = valid_concepts


def compile_edit_plan(
    project: dict,
    concepts_document: dict,
    concept_id: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> dict:
    """Deterministically compile a sanitized concept into edit-plan.v1 with
    linked video/audio events and a hook title."""
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
    assets = {
        asset["asset_id"]: asset
        for asset in project.get("inventory", {}).get("assets", [])
    }

    video_events = []
    audio_events = []
    timeline = 0.0
    for beat in concept["structure"]:
        for span in beat["evidence"]:
            asset = assets[span["asset_id"]]
            start = span["start_seconds"]
            end = span["end_seconds"]
            duration = round(end - start, 3)
            index = len(video_events) + 1
            base = {
                "asset_id": span["asset_id"],
                "source_start_seconds": start,
                "source_end_seconds": end,
                "timeline_start_seconds": round(timeline, 3),
                "duration_seconds": duration,
                "playback_rate": 1.0,
                "intent": beat["purpose"],
                "observed_content": span["observed_content"],
                "confidence": span["confidence"],
                "transition_out": {"type": "cut", "duration_seconds": 0.0},
                "text": None,
            }
            video_events.append(
                {
                    "event_id": f"v{index:02d}_{beat['beat_id']}"[:64],
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
                    "event_id": f"a{index:02d}_{beat['beat_id']}"[:64],
                    **base,
                    "volume_db": 0.0,
                }
            )
            timeline = round(timeline + duration, 3)

    if not video_events:
        raise PlanningError("The selected concept has no usable evidence ranges")

    hook_text = str(concept.get("title") or "").strip()[:70] or "Daily vlog"
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
            "text": hook_text,
            "volume_db": None,
        }
    ]

    return {
        "schema_version": "edit-plan.v1",
        "generated_at": utc_now(),
        "benchmark_id": f"{project['project_id']}-auto-{PROMPT_VERSION}",
        "concept_id": concept_id,
        "revision": 1,
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
        ],
    }


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
