"""Minimal OpenTake external-MCP client for the app side.

Only what the sync flow needs: one authenticated session and get_timeline.
The endpoint is loopback-only on the host, so this works when the app runs
host-side; from inside the Docker container the sync endpoints accept a
readback payload fetched by the host CLI instead (see
`app/scripts/opentake_adapter.py --sync`).
"""

from __future__ import annotations

import json
import os
import urllib.request


class OpenTakeMcpError(RuntimeError):
    pass


DEFAULT_URL = "http://127.0.0.1:19789/mcp"


class OpenTakeMcp:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = url or os.environ.get("OPENTAKE_MCP_URL", DEFAULT_URL)
        self.token = token or os.environ.get("OPENTAKE_MCP_TOKEN", "")
        if not self.token:
            raise OpenTakeMcpError("OPENTAKE_MCP_TOKEN is not set")
        self.session: str | None = None
        self.next_id = 0
        init = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "video-edit-brain", "version": "0.1"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.server = init.get("serverInfo", {})

    def _post(self, payload: dict) -> dict | None:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(
            self.url, json.dumps(payload).encode(), headers
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self.session = response.headers.get("Mcp-Session-Id") or self.session
                body = response.read().decode()
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    events = [
                        line[5:].strip()
                        for line in body.splitlines()
                        if line.startswith("data:")
                    ]
                    body = events[-1] if events else ""
        except OSError as exc:
            raise OpenTakeMcpError(f"OpenTake MCP unreachable: {exc}") from exc
        return json.loads(body) if body.strip() else None

    def _rpc(self, method: str, params: dict) -> dict:
        self.next_id += 1
        body = self._post(
            {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        )
        if body is None:
            raise OpenTakeMcpError(f"{method}: empty response")
        if "error" in body:
            raise OpenTakeMcpError(f"{method}: {json.dumps(body['error'])[:300]}")
        return body["result"]

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if result.get("isError"):
            raise OpenTakeMcpError(f"{name}: {json.dumps(result)[:300]}")
        texts = [
            item["text"]
            for item in result.get("content", [])
            if item.get("type") == "text"
        ]
        if not texts:
            return {}
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            # Some tool successes reply with plain prose.
            return {"text": texts[0]}

    def get_timeline(self) -> dict:
        return self.tool("get_timeline")
