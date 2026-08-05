from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "qwen": {
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "default_base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.7-plus",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-3.5-flash",
    },
}

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)

    def public_identity(self) -> dict:
        """Provenance for artifacts. Never includes the key."""
        return {"provider": self.provider, "model": self.model}


def resolve_provider(provider: str, model: str | None = None) -> ProviderConfig:
    defaults = PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise ProviderError(f"Unknown provider: {provider}")
    api_key = os.environ.get(defaults["api_key_env"], "").strip()
    if not api_key:
        raise ProviderError(
            f"Provider '{provider}' requires the {defaults['api_key_env']} environment variable"
        )
    base_url = (
        os.environ.get(defaults["base_url_env"], "").strip()
        or defaults["default_base_url"]
    ).rstrip("/")
    return ProviderConfig(
        provider=provider,
        model=(model or defaults["default_model"]).strip(),
        base_url=base_url,
        api_key=api_key,
    )


def image_part(jpeg_bytes: bytes) -> dict:
    encoded = base64.b64encode(jpeg_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def video_part(mp4_bytes: bytes) -> dict:
    encoded = base64.b64encode(mp4_bytes).decode("ascii")
    return {
        "type": "video_url",
        "video_url": {"url": f"data:video/mp4;base64,{encoded}"},
    }


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def parse_json_content(content: str) -> Any:
    """Parse a model response that should be JSON, tolerating code fences."""
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


class ChatClient:
    """Minimal OpenAI-compatible chat-completions client for owned adapters."""

    def __init__(
        self,
        config: ProviderConfig,
        timeout_seconds: float = 180.0,
        max_attempts: int = 3,
    ) -> None:
        self.config = config
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts

    def chat(
        self,
        messages: list[dict],
        *,
        json_object: bool = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self.config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        last_error = "unknown provider error"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(
                    url, json=payload, headers=headers, timeout=self._timeout
                )
            except httpx.HTTPError as exc:
                last_error = f"transport error: {type(exc).__name__}"
            else:
                if response.status_code == 200:
                    return self._extract(response.json())
                body = response.text[:400]
                last_error = f"HTTP {response.status_code}: {body}"
                if response.status_code not in RETRYABLE_STATUS:
                    break
            if attempt < self._max_attempts:
                time.sleep(2**attempt)
        raise ProviderError(
            f"{self.config.provider}/{self.config.model} request failed: {last_error}"
        )

    def _extract(self, data: dict) -> dict:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Unexpected {self.config.provider} response shape"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                f"{self.config.provider}/{self.config.model} returned empty content"
            )
        usage = data.get("usage") or {}
        return {
            "content": content,
            "model": data.get("model") or self.config.model,
            "finish_reason": choice.get("finish_reason"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            },
        }
