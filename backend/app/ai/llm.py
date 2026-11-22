"""Provider-independent LLM interface. Business code depends only on `LLMProvider`."""
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    pass


@dataclass
class LLMRequest:
    task: str                       # e.g. "concepts" | "brief" (used by the mock and for telemetry)
    system: str
    user: str
    schema: type[BaseModel]
    prompt_version: str
    context: dict = field(default_factory=dict)  # structured inputs; real providers only see them via `user`
    max_tokens: int = 4000
    images: list[bytes] = field(default_factory=list)   # PNG/JPEG bytes sent as image blocks (vision tasks)


@dataclass
class LLMResult:
    parsed: BaseModel
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_json(self, req: LLMRequest) -> LLMResult: ...


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(t[start:end + 1])


class AnthropicProvider(LLMProvider):
    """Claude via the Messages API. Model comes from config (never hardcoded).
    NOTE: exercised only against a mock transport so far — no live API call has been made."""
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not api_key or not model:
            raise LLMNotConfigured("ANTHROPIC_API_KEY and LLM_MODEL must be set to use the anthropic provider")
        self.api_key, self.model, self.transport, self.sleep = api_key, model, transport, sleep

    def _call(self, system: str, messages: list[dict], max_tokens: int) -> tuple[str, int, int]:
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        for attempt in range(4):
            try:
                with httpx.Client(transport=self.transport, timeout=120) as c:
                    r = c.post(self.URL, json=body, headers=headers)
            except httpx.TransportError as e:
                if attempt == 3:
                    raise LLMError(f"Network error calling Anthropic: {type(e).__name__}") from e
                self.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 529):
                if attempt == 3:
                    raise LLMError(f"Anthropic API unavailable ({r.status_code})")
                self.sleep(2 ** attempt)  # exponential backoff
                continue
            if r.status_code >= 400:
                raise LLMError(f"Anthropic API error {r.status_code}")  # body deliberately not echoed
            d = r.json()
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            u = d.get("usage", {})
            return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
        raise LLMError("unreachable")

    def generate_json(self, req: LLMRequest) -> LLMResult:
        schema_hint = json.dumps(req.schema.model_json_schema())
        system = f"{req.system}\n\nRespond with ONE JSON object only, no prose, matching this JSON Schema:\n{schema_hint}"
        if req.images:
            import base64
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(i).decode()}} for i in req.images]
            messages = [{"role": "user", "content": [*blocks, {"type": "text", "text": req.user}]}]
        else:
            messages = [{"role": "user", "content": req.user}]
        tin = tout = 0
        for attempt in (1, 2):
            text, i, o = self._call(system, messages, req.max_tokens)
            tin, tout = tin + i, tout + o
            try:
                return LLMResult(req.schema.model_validate(extract_json(text)), self.name, self.model, tin, tout, attempt)
            except (ValueError, ValidationError) as e:
                if attempt == 2:
                    raise LLMError("Model returned output that failed schema validation") from e
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"Your response was invalid: {str(e)[:600]}. Return corrected JSON only."}]
        raise LLMError("unreachable")


def get_llm_provider() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "anthropic":
        return AnthropicProvider(s.anthropic_api_key, s.llm_model)
    from app.ai.mock_llm import MockLLM
    return MockLLM()
