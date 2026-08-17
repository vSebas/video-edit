#!/usr/bin/env python3
"""Temporal-grounding bake-off: ask each model WHEN a verified event happens
in a full clip and score IoU against independently checked ground truth.

Usage (inside the app container, workspace mounted at host path):
    python bench/grounding_bench.py --providers qwen3.7-plus qwen3-vl-plus \
        gemini-3.5-flash --media-root /path/to/workspace
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BENCH_ROOT = Path(__file__).resolve().parent
MEDIA_DIR = "Crayotter/crayotter-data/user_temp"
PROMPT = (
    "Watch this video clip carefully. Question: when does the following "
    "happen: \"{query}\"?\n"
    "Reply with ONLY a JSON object: {{\"start_seconds\": <float>, "
    "\"end_seconds\": <float>}} using timestamps relative to this video."
)


def clip_bytes(media_root: Path, filename: str, cache: dict) -> bytes:
    if filename not in cache:
        source = media_root / MEDIA_DIR / filename
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-vf", "scale=480:-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-an", "-movflags", "+faststart+frag_keyframe+empty_moov",
                "-f", "mp4", "pipe:1",
            ],
            capture_output=True, check=True,
        )
        cache[filename] = result.stdout
    return cache[filename]


def parse_span(text: str) -> tuple[float, float] | None:
    text = text.strip()
    fenced = re.search(r"\{[^{}]*\}", text, re.S)
    if not fenced:
        return None
    try:
        data = json.loads(fenced.group(0))
        return float(data["start_seconds"]), float(data["end_seconds"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def ask_qwen(model: str, video: bytes, query: str) -> tuple[str, dict]:
    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {
                "url": "data:video/mp4;base64," + base64.b64encode(video).decode()}},
            {"type": "text", "text": PROMPT.format(query=query)},
        ]}],
    }
    response = httpx.post(
        os.environ["DASHSCOPE_BASE_URL"].rstrip("/") + "/chat/completions",
        json=payload,
        headers={"Authorization": "Bearer " + os.environ["DASHSCOPE_API_KEY"]},
        timeout=240,
    )
    response.raise_for_status()
    body = response.json()
    return body["choices"][0]["message"]["content"], body.get("usage", {})


def ask_gemini(model: str, video: bytes, query: str) -> tuple[str, dict]:
    payload = {
        "contents": [{"parts": [
            {"inline_data": {
                "mime_type": "video/mp4",
                "data": base64.b64encode(video).decode()}},
            {"text": PROMPT.format(query=query)},
        ]}],
        "generationConfig": {"temperature": 0.0},
    }
    response = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        json=payload,
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        timeout=240,
    )
    response.raise_for_status()
    body = response.json()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return text, body.get("usageMetadata", {})


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def run_item(provider: str, item: dict, video: bytes) -> dict:
    started = time.time()
    try:
        if provider.startswith("gemini"):
            text, usage = ask_gemini(provider, video, item["query"])
        else:
            text, usage = ask_qwen(provider, video, item["query"])
        span = parse_span(text)
        score = iou(span, (item["truth_start"], item["truth_end"])) if span else 0.0
        return {
            "query": item["query"], "asset": item["asset_id"],
            "predicted": span, "truth": [item["truth_start"], item["truth_end"]],
            "iou": round(score, 3), "seconds": round(time.time() - started, 1),
            "usage": usage,
        }
    except Exception as exc:  # bench keeps going on individual failures
        return {
            "query": item["query"], "asset": item["asset_id"],
            "error": str(exc)[:200], "iou": 0.0,
            "seconds": round(time.time() - started, 1),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", nargs="+", required=True)
    parser.add_argument("--media-root", type=Path, default=BENCH_ROOT.parent)
    parser.add_argument("--output", type=Path, default=BENCH_ROOT / "results")
    args = parser.parse_args()

    truth = json.loads((BENCH_ROOT / "ground-truth.json").read_text())["items"]
    cache: dict = {}
    for item in truth:
        clip_bytes(args.media_root, item["filename"], cache)

    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    for provider in args.providers:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(
                lambda item: run_item(provider, item, cache[item["filename"]]),
                truth,
            ))
        scores = [r["iou"] for r in results]
        mean_iou = statistics.mean(scores)
        hits = sum(1 for s in scores if s >= 0.3)
        errors = sum(1 for r in results if "error" in r)
        row = {
            "provider": provider,
            "mean_iou": round(mean_iou, 3),
            "hits_at_0.3": f"{hits}/{len(scores)}",
            "errors": errors,
            "median_latency_s": round(statistics.median(r["seconds"] for r in results), 1),
        }
        summary.append(row)
        (args.output / f"{provider.replace('/', '_')}.json").write_text(
            json.dumps({"summary": row, "results": results}, indent=1)
        )
        print(json.dumps(row))
    print("\n=== SUMMARY ===")
    for row in sorted(summary, key=lambda r: -r["mean_iou"]):
        print(f"{row['provider']:24s} mean IoU {row['mean_iou']:.3f}  "
              f"hits {row['hits_at_0.3']}  errors {row['errors']}  "
              f"median {row['median_latency_s']}s")


if __name__ == "__main__":
    main()
