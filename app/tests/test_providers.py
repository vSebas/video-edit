"""Provider client resilience: empty content from a 200 response is RETRYABLE —
either a transient hiccup or a reasoning model (deepseek-v4-pro) whose token
budget was consumed by reasoning_content before the visible answer. On
finish_reason "length" the retry escalates the budget so it can actually finish
(the original bug: the chat failed hard with 'returned empty content')."""

import pytest

import video_app.providers as providers_mod
from video_app.providers import ChatClient, ProviderConfig, ProviderError


def _cfg():
    return ProviderConfig(provider="qwen", model="deepseek-v4-pro",
                          api_key="k", base_url="http://fake")


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = ""

    def json(self):
        return self._body


def _choice(content, finish):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_empty_length_response_retries_with_bigger_budget(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(dict(json))
        if len(calls) == 1:
            # reasoning ate the whole cap: empty content, finish_reason=length
            return _Resp(_choice("", "length"))
        return _Resp(_choice('{"kind":"reply","text":"hola"}', "stop"))

    monkeypatch.setattr(providers_mod.httpx, "post", fake_post)
    monkeypatch.setattr(providers_mod.time, "sleep", lambda s: None)

    result = ChatClient(_cfg()).chat(
        [{"role": "user", "content": "hola"}], max_tokens=900)
    assert result["content"].startswith('{"kind"')
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 900
    assert calls[1]["max_tokens"] == 3600          # 4x escalation on "length"


def test_plain_empty_content_retries_then_fails_with_clear_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _Resp(_choice("", "stop"))       # empty every time (no length)

    monkeypatch.setattr(providers_mod.httpx, "post", fake_post)
    monkeypatch.setattr(providers_mod.time, "sleep", lambda s: None)

    with pytest.raises(ProviderError, match="empty content"):
        ChatClient(_cfg(), max_attempts=2).chat(
            [{"role": "user", "content": "hola"}], max_tokens=900)
