#!/usr/bin/env python3
"""Reproduce the flat video track of edit-plan.v1 in OpenTake over MCP.

Trial step 3 (TRIAL_OPENTAKE.md): deterministic translation, not an LLM —
map the plan's media, place every video event with add_clips, then verify the
fields this trial emits: placement, duration, source trim, media, and linked
audio. The title track and richer edit properties are outside this adapter.

Requires: OpenTake running with external MCP paired (token in .env as
OPENTAKE_MCP_TOKEN) and a saved project open. The open project's existing
clips are removed first — run this against a scratch project. This is not the
production MCP adapter: it has one session, no retry/reconciliation, filename-
stem media identity, and no transaction or rollback across remove-then-add.

Usage: python3 app/scripts/opentake_adapter.py <project_id> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
URL = "http://127.0.0.1:19789/mcp"


def read_token() -> str:
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith("OPENTAKE_MCP_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENTAKE_MCP_TOKEN missing from .env")


class McpClient:
    def __init__(self) -> None:
        self.token = read_token()
        self.session: str | None = None
        self.next_id = 0
        init = self.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "video-edit-brain", "version": "0.1"},
        })
        server = init["serverInfo"]
        print(f"connected: {server['name']} {server['version']}")
        self.notify("notifications/initialized")

    def _post(self, payload: dict) -> tuple[dict | None, str | None]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(URL, json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            body = resp.read().decode()
            if "text/event-stream" in resp.headers.get("Content-Type", ""):
                data = [l[5:].strip() for l in body.splitlines() if l.startswith("data:")]
                body = data[-1] if data else ""
        return (json.loads(body) if body.strip() else None), sid

    def rpc(self, method: str, params: dict) -> dict:
        self.next_id += 1
        body, sid = self._post(
            {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        )
        if sid:
            self.session = sid
        if body is None:
            raise SystemExit(f"{method}: empty response")
        if "error" in body:
            raise SystemExit(f"{method}: {json.dumps(body['error'])}")
        return body["result"]

    def notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise SystemExit(f"{name} failed: {json.dumps(result)[:500]}")
        texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        if not texts:
            return {}
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return {"text": texts[0]}


def load_plan(project_id: str) -> tuple[dict, dict]:
    base = REPO / "runtime" / "projects" / project_id / "plan"
    plan = json.loads((base / "edit-plan.json").read_text())
    inventory = json.loads((base / "media-inventory.json").read_text())
    return plan, inventory


def plan_entries(plan: dict, fps: int) -> list[dict]:
    """Video-track events -> add_clips entries, all in project frames."""
    video = next(t for t in plan["tracks"] if t["kind"] == "video")
    entries = []
    for event in video["events"]:
        source_start = round(event["source_start_seconds"] * fps)
        duration = round(event["duration_seconds"] * fps)
        entries.append({
            "asset_id": event["asset_id"],
            "event_id": event["event_id"],
            "startFrame": round(event["timeline_start_seconds"] * fps),
            "durationFrames": duration,
            "trimStartFrame": source_start,
        })
    return entries


def verify(client: McpClient, entries: list[dict], ref_for: dict[str, str],
           readback_path: Path) -> list[str]:
    """Full verification per the step-3 review: geometry, source trims,
    A/V pairing — and the raw readback persisted for independent audit."""
    after = client.tool("get_timeline")
    readback_path.write_text(json.dumps(after, indent=1))
    video = sorted((c for t in after.get("tracks", []) if t.get("type") == "video"
                    for c in t.get("clips", [])), key=lambda c: c["startFrame"])
    audio = [c for t in after.get("tracks", []) if t.get("type") == "audio"
             for c in t.get("clips", [])]
    failures = []
    if len(video) != len(entries):
        failures.append(f"video clip count {len(video)} != {len(entries)}")
    if len(audio) != len(entries):
        failures.append(f"audio clip count {len(audio)} != {len(entries)}")
    audio_by_group = {}
    for c in audio:
        audio_by_group.setdefault(c.get("linkGroupId"), []).append(c)
    for want, got in zip(entries, video):
        eid = want["event_id"]
        # trimStartFrame is omitted from the encoding when zero.
        checks = [
            ("startFrame", want["startFrame"], got.get("startFrame", 0)),
            ("durationFrames", want["durationFrames"], got.get("durationFrames", 0)),
            ("trimStartFrame", want["trimStartFrame"], got.get("trimStartFrame", 0)),
        ]
        for field, expected, actual in checks:
            if actual != expected:
                failures.append(f"{eid}: {field} {actual} != {expected}")
        if got.get("mediaRef") != ref_for[want["asset_id"]]:
            failures.append(f"{eid}: wrong media {got.get('mediaRef')}")
        group = got.get("linkGroupId")
        partners = audio_by_group.get(group, [])
        if group is None or len(partners) != 1:
            failures.append(f"{eid}: expected exactly 1 linked audio, got {len(partners)}")
        else:
            partner = partners[0]
            for field in ("startFrame", "durationFrames", "trimStartFrame", "mediaRef"):
                if partner.get(field, 0 if field != "mediaRef" else None) !=                    got.get(field, 0 if field != "mediaRef" else None):
                    failures.append(f"{eid}: audio partner differs on {field}")
    unpaired = [g for g, cs in audio_by_group.items() for _ in cs
                if g not in {c.get("linkGroupId") for c in video}]
    if unpaired:
        failures.append(f"{len(unpaired)} unpaired audio clips")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    plan, inventory = load_plan(args.project_id)
    fps = plan["project"]["fps"]
    entries = plan_entries(plan, fps)
    assets = {a["asset_id"]: a for a in inventory["assets"]}
    needed = sorted({e["asset_id"] for e in entries})
    print(f"plan: {len(entries)} video events across {len(needed)} assets @ {fps}fps")

    client = McpClient()

    # 1. Map plan assets to OpenTake media refs by filename; import what's missing.
    media = client.tool("get_media")
    # Library names are extension-less stems (IMG_0997), ids under "id".
    by_stem = {item["name"]: item for item in media.get("entries", [])}
    ref_for: dict[str, str] = {}
    to_import = []
    for asset_id in needed:
        hit = by_stem.get(Path(assets[asset_id]["filename"]).stem)
        if hit:
            ref_for[asset_id] = hit["id"]
        else:
            to_import.append(asset_id)
    print(f"library matches: {len(ref_for)}, to import: {len(to_import)}")

    if args.dry_run:
        for e in entries[:5]:
            print("  ", e)
        return

    readback_path = REPO / "runtime" / "projects" / args.project_id / "opentake-readback.json"
    if args.verify_only:
        if to_import:
            raise SystemExit(f"missing media in library: {to_import}")
        failures = verify(client, entries, ref_for, readback_path)
        print(f"readback persisted: {readback_path}")
        if failures:
            print("VERDICT: FAIL")
            for f in failures[:25]:
                print("  ", f)
            sys.exit(1)
        print("VERDICT: PASS — geometry, source trims, and A/V pairing all exact")
        return

    for asset_id in to_import:
        path = (REPO / assets[asset_id]["source_path"]).resolve()
        if not path.is_file():
            raise SystemExit(f"missing media file: {path}")
        result = client.tool("import_media", {"source": {"path": str(path)}})
        ref = result.get("mediaId") or result.get("id")
        if not ref:
            raise SystemExit(f"import returned no id: {json.dumps(result)[:300]}")
        ref_for[asset_id] = ref
        print(f"imported {path.name} -> {ref}")

    # 2. Clear the scratch timeline (recorded first, for the trial log).
    before = client.tool("get_timeline")
    existing = [c["clipId"] for t in before.get("tracks", []) for c in t.get("clips", [])]
    if existing:
        print(f"removing {len(existing)} existing clips from the scratch project")
        client.tool("remove_clips", {"clipIds": existing})

    # 3. Place every event in one batch: all-or-nothing per the tool contract.
    batch = [
        {
            "mediaRef": ref_for[e["asset_id"]],
            "startFrame": e["startFrame"],
            "durationFrames": e["durationFrames"],
            "trimStartFrame": e["trimStartFrame"],
        }
        for e in entries
    ]
    client.tool("add_clips", {"entries": batch})
    print(f"placed {len(batch)} clips")

    # 4. Full verification (geometry + trims + A/V pairing), readback persisted.
    failures = verify(client, entries, ref_for, readback_path)
    print(f"readback persisted: {readback_path}")
    if failures:
        print("VERDICT: FAIL")
        for f in failures[:25]:
            print("  ", f)
        sys.exit(1)
    print("VERDICT: PASS — geometry, source trims, and A/V pairing all exact")


if __name__ == "__main__":
    main()
