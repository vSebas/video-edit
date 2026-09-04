"""Real platform music providers (HTTP), behind the MusicDiscoveryProvider
protocol from music.py.

Instagram exposes an OFFICIAL audio API (Instagram Platform → Content Publishing
→ Audio API, the `/ig_audio` edge): search the audio library by query, and —
crucially — omitting the query returns TRENDING music / original sounds. It
returns only audio authorized for third-party use, so the selection can differ
from the native app, but it is official (no scraping, no ToS gray area).

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/

The provider stays inert until an access token is configured, so nothing here
runs (or can break) by default.

Env (all optional; token unset → provider disabled, model fallback is used):
  INSTAGRAM_ACCESS_TOKEN   user access token (Business/Creator; instagram_basic
                           + instagram_content_publish)
  INSTAGRAM_GRAPH_URL      base host (default https://graph.instagram.com)
  INSTAGRAM_API_VERSION    graph version (default v21.0)
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .music import MusicCandidate, MusicIntent

_TIMEOUT = 8.0
_FIELDS = "id,title,artist_name,duration_ms,audio_type"


class InstagramMusicProvider:
    """Official Instagram Audio API (`/ig_audio`). `available()` gates on a
    configured access token; with none set the model fallback is used instead."""

    name = "instagram"

    def __init__(self, env: dict | None = None):
        import os

        env = env if env is not None else os.environ
        self._token = (env.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
        host = (env.get("INSTAGRAM_GRAPH_URL") or "https://graph.instagram.com").strip()
        version = (env.get("INSTAGRAM_API_VERSION") or "v21.0").strip()
        self._endpoint = f"{host.rstrip('/')}/{version}/ig_audio"

    def available(self) -> bool:
        return bool(self._token)

    def _query_string(self, intent: MusicIntent) -> str:
        terms = list(intent.mood)
        if intent.energy:
            terms.append(intent.energy)
        return " ".join(terms).strip()

    def _fetch(self, params: dict) -> list[dict]:
        params = {**params, "fields": _FIELDS, "access_token": self._token}
        url = f"{self._endpoint}?{urllib.parse.urlencode({k: v for k, v in params.items() if v})}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            raise RuntimeError(
                f"Instagram Audio API error: {payload['error'].get('message', 'unknown')}"
            )
        rows = payload.get("data") if isinstance(payload, dict) else payload
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def _normalize(self, row: dict, trending: bool) -> MusicCandidate:
        duration = None
        ms = row.get("duration_ms")
        try:
            duration = round(float(ms) / 1000.0, 3) if ms is not None else None
        except (TypeError, ValueError):
            duration = None
        return MusicCandidate(
            provider=self.name,
            platform="instagram",
            platform_audio_id=str(row.get("id")) if row.get("id") is not None else None,
            title=str(row.get("title")).strip() if row.get("title") else None,
            artist=str(row.get("artist_name")).strip() if row.get("artist_name") else None,
            duration_seconds=duration,
            trend_state="rising" if trending else None,
            # the API returns audio authorized for third-party use → addable
            account_usable=True,
            provenance="instagram_audio_api",
        )

    def search_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        query = self._query_string(intent)
        rows = self._fetch({"q": query} if query else {})
        return [self._normalize(r, trending=not query) for r in rows][:8]

    def trending_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        # omitting `q` returns trending original sounds / music
        rows = self._fetch({})
        return [self._normalize(r, trending=True) for r in rows][:8]
