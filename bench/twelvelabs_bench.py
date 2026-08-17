#!/usr/bin/env python3
"""TwelveLabs grounding bench: Marengo moment search and Pegasus Q&A against
the same verified ground truth as grounding_bench.py. Clips are the small
downscaled bench copies. Results are written in the shared results format."""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from pathlib import Path

import httpx

BENCH_ROOT = Path(__file__).resolve().parent
API = "https://api.twelvelabs.io/v1.3"
HEADERS = {"x-api-key": os.environ["TWELVELABS_API_KEY"]}
CLIPS_DIR = Path(os.environ.get("BENCH_CLIPS_DIR", "/tmp/bench_clips"))
PROMPT = (
    "Question: when does the following happen in this video: \"{query}\"?\n"
    "Reply with ONLY a JSON object: {{\"start_seconds\": <float>, "
    "\"end_seconds\": <float>}}."
)


def ensure_index() -> str:
    listing = httpx.get(f"{API}/indexes", headers=HEADERS, timeout=30).json()
    for index in listing.get("data", []):
        if index["index_name"] == "vlog-bench":
            return index["_id"]
    created = httpx.post(
        f"{API}/indexes", headers=HEADERS, timeout=30,
        json={
            "index_name": "vlog-bench",
            "models": [
                {"model_name": "marengo3.0", "model_options": ["visual", "audio"]},
                {"model_name": "pegasus1.2", "model_options": ["visual", "audio"]},
            ],
        },
    ).json()
    return created["_id"]


def upload_clips(index_id: str) -> dict[str, str]:
    """filename -> video_id, uploading only what the index does not have."""
    existing: dict[str, str] = {}
    page = httpx.get(
        f"{API}/indexes/{index_id}/videos", headers=HEADERS,
        params={"page_limit": 50}, timeout=30,
    ).json()
    for video in page.get("data", []):
        name = (video.get("system_metadata") or {}).get("filename", "")
        existing[name] = video["_id"]

    truth = json.loads((BENCH_ROOT / "ground-truth.json").read_text())["items"]
    needed = sorted({item["filename"] for item in truth})
    tasks = []
    for filename in needed:
        if filename in existing:
            continue
        with open(CLIPS_DIR / filename, "rb") as handle:
            response = httpx.post(
                f"{API}/tasks", headers=HEADERS, timeout=120,
                data={"index_id": index_id},
                files={"video_file": (filename, handle, "video/mp4")},
            ).json()
        if "_id" not in response:
            raise RuntimeError(f"upload rejected for {filename}: {response}")
        tasks.append((filename, response["_id"]))
        print(f"uploading {filename}: task {response['_id']}", flush=True)

    for filename, task_id in tasks:
        for _ in range(120):
            task = httpx.get(f"{API}/tasks/{task_id}", headers=HEADERS, timeout=30).json()
            if task.get("status") == "ready":
                existing[filename] = task["video_id"]
                print(f"{filename} indexed", flush=True)
                break
            if task.get("status") == "failed":
                raise RuntimeError(f"indexing failed for {filename}: {task}")
            time.sleep(5)
        else:
            raise RuntimeError(f"indexing timed out for {filename}")
    return existing


def iou(a, b) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def marengo_span(index_id: str, video_id: str, query: str):
    # The search endpoint accepts multipart/form-data only.
    response = httpx.post(
        f"{API}/search", headers=HEADERS, timeout=60,
        files=[
            ("index_id", (None, index_id)),
            ("query_text", (None, query)),
            ("search_options", (None, "visual")),
            ("search_options", (None, "audio")),
            ("page_limit", (None, "50")),
        ],
    ).json()
    # Best-ranked hit within the correct source video.
    for clip in response.get("data", []):
        if clip.get("video_id") == video_id:
            return float(clip["start"]), float(clip["end"])
    return None


def pegasus_span(video_id: str, query: str):
    response = httpx.post(
        f"{API}/analyze", headers=HEADERS, timeout=120,
        json={"video_id": video_id, "prompt": PROMPT.format(query=query),
              "temperature": 0.0, "stream": False},
    ).json()
    text = response.get("data", "")
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return float(data["start_seconds"]), float(data["end_seconds"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def run(provider: str, ask) -> None:
    truth = json.loads((BENCH_ROOT / "ground-truth.json").read_text())["items"]
    results = []
    for item in truth:
        started = time.time()
        try:
            span = ask(item)
            score = iou(span, (item["truth_start"], item["truth_end"])) if span else 0.0
            results.append({
                "query": item["query"], "asset": item["asset_id"],
                "predicted": list(span) if span else None,
                "truth": [item["truth_start"], item["truth_end"]],
                "iou": round(score, 3),
                "seconds": round(time.time() - started, 1),
            })
        except Exception as exc:
            results.append({"query": item["query"], "asset": item["asset_id"],
                            "error": str(exc)[:200], "iou": 0.0,
                            "seconds": round(time.time() - started, 1)})
        print(json.dumps(results[-1]), flush=True)
    scores = [r["iou"] for r in results]
    summary = {
        "provider": provider,
        "mean_iou": round(statistics.mean(scores), 3),
        "hits_at_0.3": f"{sum(1 for s in scores if s >= 0.3)}/{len(scores)}",
        "errors": sum(1 for r in results if "error" in r),
        "median_latency_s": round(statistics.median(r["seconds"] for r in results), 1),
    }
    print("SUMMARY:", json.dumps(summary))
    (BENCH_ROOT / "results" / f"{provider}.json").write_text(
        json.dumps({"summary": summary, "results": results}, indent=1)
    )


def main() -> None:
    index_id = ensure_index()
    print("index:", index_id, flush=True)
    videos = upload_clips(index_id)
    print("videos:", videos, flush=True)
    truth = json.loads((BENCH_ROOT / "ground-truth.json").read_text())["items"]
    by_file = {item["filename"]: videos[item["filename"]] for item in truth}
    run("twelvelabs-marengo3.0",
        lambda item: marengo_span(index_id, by_file[item["filename"]], item["query"]))
    run("twelvelabs-pegasus1.2",
        lambda item: pegasus_span(by_file[item["filename"]], item["query"]))


if __name__ == "__main__":
    main()
