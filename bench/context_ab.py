#!/usr/bin/env python3
"""Generate a blind-comparison pair with identical planner settings."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import httpx

BENCH_ROOT = Path(__file__).resolve().parent


def wait_for_job(client: httpx.Client, job_id: str, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] == "completed":
            return job["result"]
        if job["status"] == "failed":
            raise RuntimeError(job.get("error") or f"Job {job_id} failed")
        time.sleep(1)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout_seconds:.0f}s")


def generate(
    client: httpx.Client,
    project_id: str,
    provider: str,
    model: str | None,
    use_source_context: bool,
    timeout_seconds: float,
) -> dict:
    payload = {
        "provider": provider,
        "model": model,
        "use_source_context": use_source_context,
    }
    response = client.post(f"/api/projects/{project_id}/concepts", json=payload)
    response.raise_for_status()
    return wait_for_job(client, response.json()["job_id"], timeout_seconds)


def cited_ranges(document: dict) -> list[tuple[str, float, float]]:
    ranges = []
    for concept in document.get("concepts") or []:
        for beat in concept.get("structure") or []:
            for item in beat.get("evidence") or []:
                ranges.append(
                    (
                        item["asset_id"],
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                )
    return ranges


def unique_coverage(ranges: list[tuple[str, float, float]]) -> float:
    by_asset: dict[str, list[tuple[float, float]]] = {}
    for asset_id, start, end in ranges:
        if end > start:
            by_asset.setdefault(asset_id, []).append((start, end))
    coverage = 0.0
    for asset_ranges in by_asset.values():
        current_end = None
        for start, end in sorted(asset_ranges):
            if current_end is None or start > current_end:
                coverage += end - start
                current_end = end
            elif end > current_end:
                coverage += end - current_end
                current_end = end
    return round(coverage, 3)


def summary(document: dict) -> dict:
    ranges = cited_ranges(document)
    return {
        "titles": [item.get("title") for item in document.get("concepts") or []],
        "cited_ranges": len(ranges),
        "evidence_coverage_seconds": unique_coverage(ranges),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--provider", default="qwen")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--output-root", type=Path, default=BENCH_ROOT / "results-context"
    )
    args = parser.parse_args()

    safe_project = re.sub(r"[^a-zA-Z0-9._-]+", "_", args.project_id)
    output = args.output_root / safe_project
    output.mkdir(parents=True, exist_ok=True)
    headers = {}
    token = os.environ.get("VIDEO_EDITING_TOKEN", "").strip()
    if token:
        headers["x-vlog-token"] = token

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), headers=headers, timeout=60
    ) as client:
        runs_response = client.get(f"/api/projects/{args.project_id}/analysis/runs")
        runs_response.raise_for_status()
        runs = runs_response.json().get("runs") or []
        if not any(
            item.get("provider", {}).get("adapter") == "owned-source-context"
            for item in runs
        ):
            raise RuntimeError("Run source-context analysis before this A/B harness")

        baseline = generate(
            client, args.project_id, args.provider, args.model, False, args.timeout
        )
        sidecar = generate(
            client, args.project_id, args.provider, args.model, True, args.timeout
        )
        telemetry_response = client.get(
            f"/api/projects/{args.project_id}/analysis/telemetry"
        )
        telemetry_response.raise_for_status()
        telemetry = telemetry_response.json()

    (output / "baseline.json").write_text(
        json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
    )
    (output / "sidecar.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )
    (output / "telemetry.json").write_text(
        json.dumps(telemetry, indent=2) + "\n", encoding="utf-8"
    )

    print(json.dumps({"baseline": summary(baseline)}, ensure_ascii=False))
    print(json.dumps({"sidecar": summary(sidecar)}, ensure_ascii=False))
    print(f"saved: {output}")


if __name__ == "__main__":
    main()
