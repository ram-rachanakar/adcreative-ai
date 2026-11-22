import json

import httpx
import pytest
from pydantic import BaseModel

from app.ai.llm import AnthropicProvider, LLMError, LLMNotConfigured, LLMRequest, extract_json


class Out(BaseModel):
    x: int


def req():
    return LLMRequest("t", "SYS", "USER", Out, "v1")


def reply(text, status=200, usage=(10, 5)):
    return httpx.Response(status, json={"content": [{"type": "text", "text": text}], "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})


def provider(handler, sleeps=None):
    return AnthropicProvider("sk-test", "configured-model", httpx.MockTransport(handler), sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_requires_configuration():
    with pytest.raises(LLMNotConfigured):
        AnthropicProvider("", "m")
    with pytest.raises(LLMNotConfigured):
        AnthropicProvider("k", "")


def test_request_shape_and_parse():
    seen = {}

    def h(r):
        seen["h"], seen["b"] = r.headers, json.loads(r.content)
        return reply('```json\n{"x": 3}\n```')
    res = provider(h).generate_json(req())
    assert res.parsed.x == 3 and (res.input_tokens, res.output_tokens, res.model, res.provider) == (10, 5, "configured-model", "anthropic")
    assert seen["h"]["x-api-key"] == "sk-test" and seen["h"]["anthropic-version"]
    assert seen["b"]["model"] == "configured-model" and "JSON Schema" in seen["b"]["system"] and seen["b"]["messages"][0]["content"] == "USER"


def test_invalid_output_retried_once_with_feedback_and_tokens_summed():
    calls = []

    def h(r):
        calls.append(json.loads(r.content))
        return reply("not json at all") if len(calls) == 1 else reply('{"x": 1}')
    res = provider(h).generate_json(req())
    assert res.attempts == 2 and res.input_tokens == 20 and len(calls[1]["messages"]) == 3


def test_two_invalid_outputs_fail():
    with pytest.raises(LLMError, match="schema validation"):
        provider(lambda r: reply('{"x": "nope"}')).generate_json(req())


def test_backoff_on_429_then_success():
    n = {"i": 0}
    sleeps = []

    def h(r):
        n["i"] += 1
        return httpx.Response(429) if n["i"] < 3 else reply('{"x": 2}')
    assert provider(h, sleeps).generate_json(req()).parsed.x == 2
    assert sleeps == [1, 2]


def test_gives_up_after_retries():
    with pytest.raises(LLMError, match="unavailable"):
        provider(lambda r: httpx.Response(503)).generate_json(req())


def test_client_error_not_retried_and_body_not_leaked():
    n = {"i": 0}

    def h(r):
        n["i"] += 1
        return httpx.Response(401, text="secret-details sk-test")
    with pytest.raises(LLMError) as e:
        provider(h).generate_json(req())
    assert n["i"] == 1 and "secret" not in str(e.value) and "sk-test" not in str(e.value)


def test_extract_json_variants():
    assert extract_json('Here: {"a": {"b": 1}} thanks') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        extract_json("nothing")
