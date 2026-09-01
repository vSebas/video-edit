from __future__ import annotations


TELEMETRY_KEYS = (
    "calls",
    "retries",
    "uploaded_bytes",
    "prompt_tokens",
    "completion_tokens",
    "wall_seconds",
    "unique_source_seconds",
)


def empty_telemetry() -> dict:
    return {
        "calls": 0,
        "retries": 0,
        "uploaded_bytes": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "wall_seconds": 0.0,
        "unique_source_seconds": 0.0,
    }


def aggregate_call_telemetry(records: list[dict]) -> dict:
    """Aggregate logical provider calls and de-overlap source coverage."""
    result = empty_telemetry()
    ranges: dict[str, list[tuple[float, float]]] = {}
    for record in records:
        call = record.get("telemetry")
        if not isinstance(call, dict):
            continue
        result["calls"] += 1
        result["retries"] += _integer(call.get("retries"))
        result["uploaded_bytes"] += _integer(call.get("request_bytes"))
        result["prompt_tokens"] += _integer(call.get("prompt_tokens"))
        result["completion_tokens"] += _integer(call.get("completion_tokens"))
        result["wall_seconds"] += _number(call.get("wall_seconds"))

        asset_id = record.get("asset_id")
        try:
            start = float(record["source_start_seconds"])
            end = float(record["source_end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if asset_id and end > start:
            ranges.setdefault(str(asset_id), []).append((start, end))

    for asset_ranges in ranges.values():
        end = None
        for start, stop in sorted(asset_ranges):
            if end is None or start > end:
                result["unique_source_seconds"] += stop - start
                end = stop
            elif stop > end:
                result["unique_source_seconds"] += stop - end
                end = stop

    result["wall_seconds"] = round(result["wall_seconds"], 3)
    result["unique_source_seconds"] = round(
        result["unique_source_seconds"], 3
    )
    return result


def _integer(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _number(value) -> float:
    try:
        return max(float(value or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0
