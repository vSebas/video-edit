from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from .providers import ProviderError, parse_json_content, text_part, video_part
from .semantic import utc_now
from .telemetry import aggregate_call_telemetry

PROMPT_VERSION = "source-context-v1"
INLINE_REQUEST_MAX_BYTES = 20_000_000
WINDOW_SECONDS = 180.0
WINDOW_OVERLAP_SECONDS = 10.0
MIN_WINDOW_SECONDS = 30.0
ENCODE_PROFILES = ((480, 30), (360, 34), (360, 38), (360, 42))
RELATIONSHIP_KINDS = {
    "setup_payoff",
    "question_answer",
    "action_reaction",
    "reference",
    "before_after",
    "speech_visual",
}

SYSTEM_PROMPT = (
    "You identify source-level narrative context in unedited footage. Use only "
    "what is visible or audible in the supplied video. Track setups, payoffs, "
    "questions, answers, actions, reactions, references, chronology, and links "
    "between speech and visuals. Do not select edit ranges or claim precise "
    "word-level timing. Return one JSON object only, with no extra keys."
)


class ContextAnalysisError(RuntimeError):
    pass


def encoded_request_bytes(media_bytes: int, prompt_bytes: int = 16_000) -> int:
    return 4 * ((media_bytes + 2) // 3) + prompt_bytes


def source_windows(
    duration: float,
    window_seconds: float = WINDOW_SECONDS,
    overlap_seconds: float = WINDOW_OVERLAP_SECONDS,
) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    if duration <= window_seconds:
        return [(0.0, round(duration, 3))]
    step = window_seconds - overlap_seconds
    if step <= 0:
        raise ValueError("Context window overlap must be smaller than the window")
    windows = []
    start = 0.0
    while start < duration:
        end = min(start + window_seconds, duration)
        windows.append((round(start, 3), round(end, 3)))
        if end >= duration:
            break
        start += step
    return windows


def extract_context_segment(
    path: Path, start: float, end: float, scale: int, crf: int
) -> bytes | None:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(start, 0.0):.3f}",
        "-t", f"{max(end - start, 0.1):.3f}",
        "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", f"scale={scale}:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode or not result.stdout:
        return None
    return result.stdout


def _encode_to_fit(
    path: Path,
    start: float,
    end: float,
    encoder,
    inline_limit: int,
) -> dict | None:
    for scale, crf in ENCODE_PROFILES:
        content = encoder(path, start, end, scale, crf)
        if content and encoded_request_bytes(len(content)) <= inline_limit:
            return {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "scale": scale,
                "crf": crf,
                "content": content,
            }
    return None


def _split_to_fit(
    path: Path,
    start: float,
    end: float,
    encoder,
    inline_limit: int,
) -> list[dict]:
    part = _encode_to_fit(path, start, end, encoder, inline_limit)
    if part is not None:
        return [part]
    if end - start <= MIN_WINDOW_SECONDS:
        raise ContextAnalysisError(
            f"Could not encode [{start:.1f}-{end:.1f}s] below the inline limit"
        )
    midpoint = (start + end) / 2.0
    half_overlap = WINDOW_OVERLAP_SECONDS / 2.0
    return _split_to_fit(
        path, start, min(midpoint + half_overlap, end), encoder, inline_limit
    ) + _split_to_fit(
        path, max(midpoint - half_overlap, start), end, encoder, inline_limit
    )


def prepare_source_parts(
    path: Path,
    duration: float,
    encoder=extract_context_segment,
    inline_limit: int = INLINE_REQUEST_MAX_BYTES,
) -> list[dict]:
    """Prefer one source call; otherwise use the fewest three-minute windows."""
    whole = _encode_to_fit(path, 0.0, duration, encoder, inline_limit)
    if whole is not None:
        return [whole]
    parts = []
    for start, end in source_windows(duration):
        parts.extend(_split_to_fit(path, start, end, encoder, inline_limit))
    return parts


def _window_instruction(filename: str, start: float, end: float) -> str:
    return f"""Analyze this source window from '{filename}'. It represents
{start:.2f}s to {end:.2f}s in the original source. All event timestamps in
your answer MUST be RELATIVE to this supplied window, starting at 0. Give
each event a distinct short label; relationship from_event and to_event must
exactly equal labels in the events array.

Return STRICT JSON with exactly this shape:
{{
  "summary": "<concise source/window narrative summary>",
  "language": "<primary spoken language code or und>",
  "events": [
    {{"start_seconds": <number>, "end_seconds": <number>,
      "label": "<unique short label>", "description": "<what happens>"}}
  ],
  "relationships": [
    {{"kind": "setup_payoff|question_answer|action_reaction|reference|before_after|speech_visual",
      "from_event": "<event label>", "to_event": "<event label>",
      "description": "<why they are linked>"}}
  ],
  "people": ["<visible or speaking person descriptors, no guessed identities>"],
  "topics": ["<topic>"]
}}"""


def normalize_window_payload(payload: dict, window_duration: float) -> dict:
    if not isinstance(payload, dict):
        raise ContextAnalysisError("Source context response was not an object")
    events = []
    seen_labels: set[str] = set()
    for item in payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        try:
            start = min(max(float(item["start_seconds"]), 0.0), window_duration)
            end = min(max(float(item["end_seconds"]), 0.0), window_duration)
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label", "")).strip()
        description = str(item.get("description", "")).strip()
        if not label or not description or end <= start:
            continue
        base = label
        suffix = 2
        while label.casefold() in seen_labels:
            label = f"{base} {suffix}"
            suffix += 1
        seen_labels.add(label.casefold())
        events.append(
            {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "label": label,
                "description": description,
            }
        )

    labels = {event["label"] for event in events}
    relationships = []
    for item in payload.get("relationships") or []:
        if not isinstance(item, dict) or item.get("kind") not in RELATIONSHIP_KINDS:
            continue
        source = str(item.get("from_event", "")).strip()
        target = str(item.get("to_event", "")).strip()
        description = str(item.get("description", "")).strip()
        if source in labels and target in labels and source != target and description:
            relationships.append(
                {
                    "kind": item["kind"],
                    "from_event": source,
                    "to_event": target,
                    "description": description,
                }
            )
    return {
        "summary": str(payload.get("summary", "")).strip(),
        "language": str(payload.get("language", "und")).strip() or "und",
        "events": events,
        "relationships": relationships,
        "people": _strings(payload.get("people")),
        "topics": _strings(payload.get("topics")),
    }


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        text = str(item).strip() if isinstance(item, str) else ""
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def _range_iou(left: dict, right: dict) -> float:
    overlap = max(
        0.0,
        min(left["end_seconds"], right["end_seconds"])
        - max(left["start_seconds"], right["start_seconds"]),
    )
    union = (
        max(left["end_seconds"], right["end_seconds"])
        - min(left["start_seconds"], right["start_seconds"])
    )
    return overlap / union if union > 0 else 0.0


def merge_window_contexts(
    asset_id: str,
    duration: float,
    windows: list[dict],
    duplicate_iou: float = 0.5,
) -> dict:
    events: list[dict] = []
    relationships: list[dict] = []
    summaries = []
    languages = []
    people = []
    topics = []
    seen_relationships = set()

    for window in sorted(windows, key=lambda item: item["start_seconds"]):
        offset = float(window["start_seconds"])
        context = window["context"]
        if context["summary"] and context["summary"] not in summaries:
            summaries.append(context["summary"])
        languages.append(context["language"])
        people.extend(context["people"])
        topics.extend(context["topics"])
        references = {}
        prior_event_count = len(events)
        for item in context["events"]:
            absolute = {
                "start_seconds": round(min(offset + item["start_seconds"], duration), 3),
                "end_seconds": round(min(offset + item["end_seconds"], duration), 3),
                "label": item["label"],
                "description": item["description"],
            }
            duplicate = next(
                (
                    event
                    for event in events[:prior_event_count]
                    if _range_iou(event, absolute) >= duplicate_iou
                ),
                None,
            )
            if duplicate is None and absolute["end_seconds"] > absolute["start_seconds"]:
                absolute["event_id"] = f"{asset_id}_event_{len(events) + 1:03d}"
                absolute["evidence_ids"] = []
                events.append(absolute)
                duplicate = absolute
            if duplicate is not None:
                references[item["label"]] = duplicate["event_id"]

        for item in context["relationships"]:
            source = references.get(item["from_event"])
            target = references.get(item["to_event"])
            key = (item["kind"], source, target)
            if not source or not target or source == target or key in seen_relationships:
                continue
            seen_relationships.add(key)
            relationships.append(
                {
                    "kind": item["kind"],
                    "from_event": source,
                    "to_event": target,
                    "description": item["description"],
                }
            )

    language_counts = Counter(item for item in languages if item and item != "und")
    language = language_counts.most_common(1)[0][0] if language_counts else "und"
    return {
        "summary": " ".join(summaries),
        "language": language,
        "events": events,
        "relationships": relationships,
        "people": _strings(people),
        "topics": _strings(topics),
    }


def anchor_events(asset_id: str, events: list[dict], observations: list[dict]) -> list[dict]:
    relevant = sorted(
        (
            item
            for item in observations
            if item.get("asset_id") == asset_id
            and item.get("normalization_status") == "accepted"
            and item.get("evidence_id")
        ),
        key=lambda item: (item.get("start_seconds", 0), item["evidence_id"]),
    )
    anchored = []
    for event in events:
        evidence_ids = [
            item["evidence_id"]
            for item in relevant
            if float(item.get("start_seconds", 0)) < event["end_seconds"]
            and float(item.get("end_seconds", 0)) > event["start_seconds"]
        ]
        anchored.append({**event, "evidence_ids": evidence_ids})
    return anchored


def analyze_context(
    client,
    assets: list[dict],
    media_root: Path,
    project_id: str,
    run_id: str,
    observations: list[dict],
    encoder=extract_context_segment,
    inline_limit: int = INLINE_REQUEST_MAX_BYTES,
) -> tuple[dict, list[dict], dict]:
    raw_records = []
    analyzed_assets = []
    warnings = []
    video_assets = [asset for asset in assets if asset.get("media_type") == "video"]
    if not video_assets:
        raise ContextAnalysisError("The project has no video assets for source context")

    for asset in video_assets:
        path = (media_root / asset["source_path"]).resolve()
        duration = float(asset.get("duration_seconds") or 0.0)
        if not path.is_file() or duration <= 0:
            warnings.append(f"Missing or empty video skipped: {asset['filename']}")
            continue
        try:
            parts = prepare_source_parts(
                path, duration, encoder=encoder, inline_limit=inline_limit
            )
        except ContextAnalysisError as exc:
            warnings.append(f"{asset['filename']}: {exc}")
            continue

        window_results = []
        for part in parts:
            start = part["start_seconds"]
            end = part["end_seconds"]
            record = {
                "asset_id": asset["asset_id"],
                "filename": asset["filename"],
                "source_start_seconds": start,
                "source_end_seconds": end,
                "encoded_bytes": len(part["content"]),
                "scale": part["scale"],
                "crf": part["crf"],
                "input_mode": "video+audio",
                "prompt_version": PROMPT_VERSION,
                "model": client.config.model,
            }
            try:
                response = client.chat(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                video_part(part["content"]),
                                text_part(_window_instruction(asset["filename"], start, end)),
                            ],
                        },
                    ],
                    json_object=True,
                    temperature=0.1,
                    max_tokens=12000,
                )
                record.update(
                    {
                        "content": response["content"],
                        "usage": response.get("usage") or {},
                        "telemetry": response.get("telemetry"),
                    }
                )
                payload = parse_json_content(response["content"])
                context = normalize_window_payload(payload, end - start)
            except (ProviderError, ContextAnalysisError, json.JSONDecodeError) as exc:
                telemetry = getattr(exc, "telemetry", None)
                if telemetry:
                    record["telemetry"] = telemetry
                record["error"] = str(exc)
                raw_records.append(record)
                warnings.append(
                    f"Context {asset['filename']} [{start:.1f}-{end:.1f}s] failed: {exc}"
                )
                continue
            raw_records.append(record)
            window_results.append({"start_seconds": start, "context": context})

        if not window_results:
            continue
        merged = merge_window_contexts(asset["asset_id"], duration, window_results)
        merged["events"] = anchor_events(
            asset["asset_id"], merged["events"], observations
        )
        analyzed_assets.append(
            {
                "asset_id": asset["asset_id"],
                "filename": asset["filename"],
                "duration_seconds": duration,
                **merged,
            }
        )

    if not analyzed_assets:
        raise ContextAnalysisError(
            "Source context produced no asset results; " + "; ".join(warnings[:3])
        )
    generated_at = utc_now()
    event_count = sum(len(asset["events"]) for asset in analyzed_assets)
    relationship_count = sum(len(asset["relationships"]) for asset in analyzed_assets)
    normalized = {
        "schema_version": "source-context.v1",
        "generated_at": generated_at,
        "project_id": project_id,
        "run_id": run_id,
        "provider": {
            "adapter": "owned-source-context",
            "id": client.config.provider,
            "model": client.config.model,
        },
        "safe_for_edit_plan": False,
        "summary": {
            "video_asset_count": len(video_assets),
            "analyzed_asset_count": len(analyzed_assets),
            "event_count": event_count,
            "relationship_count": relationship_count,
            "anchored_event_count": sum(
                bool(event["evidence_ids"])
                for asset in analyzed_assets
                for event in asset["events"]
            ),
        },
        "warnings": warnings
        + [
            "Derived source context is non-citable and is excluded from semantic evidence."
        ],
        "assets": analyzed_assets,
    }
    return normalized, raw_records, aggregate_call_telemetry(raw_records)
