#!/usr/bin/env python3
"""Sealed-blind A/B for the source-context sidecar (P7 protocol).

The 2026-09-01 first run was contaminated: labeled files sat beside the
blind copies and the key was in the same folder. This version enforces the
protocol:

- The judge sees ONLY `judge/SET-1.json` and `judge/SET-2.json` — concepts
  stripped of every treatment tell, with set assignment randomized.
- The labeled documents, telemetry, and the key live in `sealed/`, whose
  README says not to open it before judging.
- After judging, run `--reveal SET-1|SET-2` to unseal, print the mapping,
  and append the verdict to `verdicts.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx

BENCH_ROOT = Path(__file__).resolve().parent

# Keys that could reveal which set had the sidecar treatment. Everything
# not needed for judging story quality is dropped.
JUDGE_KEYS_CONCEPT = {
    "concept_id", "title", "topic", "hook", "target_duration_seconds",
    "structure", "missing_shots",
}
JUDGE_KEYS_BEAT = {"purpose", "narrative", "evidence", "cutaways", "duration_seconds"}


def sanitize_for_judge(document: dict) -> dict:
    """Only the creative content survives; provenance, telemetry, and any
    treatment markers are stripped."""
    concepts = []
    for concept in document.get("concepts") or []:
        clean = {k: concept[k] for k in JUDGE_KEYS_CONCEPT if k in concept}
        clean["structure"] = [
            {k: beat[k] for k in JUDGE_KEYS_BEAT if k in beat}
            for beat in concept.get("structure") or []
        ]
        concepts.append(clean)
    return {"concepts": concepts}


def reveal(output: Path, winner: str, notes: str) -> None:
    key_path = output / "sealed" / "key.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    treatment = key[winner]
    verdict = {
        "judged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "winner_set": winner,
        "winner_treatment": treatment,
        "key": key,
        "notes": notes,
    }
    with (output / "verdicts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(verdict, ensure_ascii=False) + "\n")
    print(f"{winner} was: {treatment}")
    print(f"full mapping: {json.dumps(key)}")
    print(f"recorded in {output / 'verdicts.jsonl'}")


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
    parser.add_argument(
        "--reveal", choices=["SET-1", "SET-2"], default=None,
        help="After judging: name the winning set to unseal the mapping",
    )
    parser.add_argument("--notes", default="", help="Judge notes for --reveal")
    args = parser.parse_args()

    safe_project = re.sub(r"[^a-zA-Z0-9._-]+", "_", args.project_id)
    output = args.output_root / safe_project
    if args.reveal:
        reveal(output, args.reveal, args.notes)
        return
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

    sealed = output / "sealed"
    judge = output / "judge"
    sealed.mkdir(parents=True, exist_ok=True)
    judge.mkdir(parents=True, exist_ok=True)

    # Randomized assignment; only sealed/key.json knows which is which.
    flip = secrets.choice([True, False])
    assignment = {
        "SET-1": "sidecar" if flip else "baseline",
        "SET-2": "baseline" if flip else "sidecar",
    }
    documents = {"baseline": baseline, "sidecar": sidecar}
    for set_name, treatment in assignment.items():
        (judge / f"{set_name}.json").write_text(
            json.dumps(sanitize_for_judge(documents[treatment]),
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (sealed / "key.json").write_text(json.dumps(assignment) + "\n")
    (sealed / "baseline.json").write_text(
        json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
    )
    (sealed / "sidecar.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )
    (sealed / "telemetry.json").write_text(
        json.dumps(telemetry, indent=2) + "\n", encoding="utf-8"
    )
    (sealed / "README.md").write_text(
        "DO NOT open anything in this folder before judging.\n"
        "Judge from ../judge/SET-1.json and ../judge/SET-2.json only, then\n"
        "run context_ab.py <project> --reveal SET-1|SET-2 [--notes ...].\n"
    )
    (judge / "INSTRUCTIONS.md").write_text(
        "Compare SET-1 and SET-2 as story proposals for this footage.\n"
        "The workbench story list shows one of these sets (last generated) —\n"
        "do not compare against it before judging.\n"
        "Pick the set you would rather edit into a vlog. Do not open the\n"
        "sealed/ folder. Reveal the mapping only after deciding:\n"
        f"  python3 bench/context_ab.py {args.project_id} --reveal SET-1 "
        "--notes '...'\n"
    )

    print("Sealed blind pair ready:")
    print(f"  judge from: {judge}/SET-1.json and SET-2.json")
    print(f"  key sealed: {sealed}/key.json (do not open)")


if __name__ == "__main__":
    main()
