"""OpenAI SDK wrapper: tier routing, structured outputs, response caching, budgets.

Tier 1 (cheap, bulk)  -> gpt-4o-mini  (classification, extraction)
Tier 2 (deep)         -> gpt-4o       (cross-document synthesis only)
Every result is cached by model + prompt version + schema + input hash, so identical source
text never costs a second call. Seeded results in data/seed/llm are read as a fallback.
"""
from __future__ import annotations

import json
import threading
from typing import Type, TypeVar

from pydantic import BaseModel

from .. import cache, config

T = TypeVar("T", bound=BaseModel)
_client = None
_client_lock = threading.Lock()


def available() -> bool:
    return bool(config.OPENAI_API_KEY)


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI
            _client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=120, max_retries=2)
        return _client


def _key(model: str, schema: Type[BaseModel], system: str, user: str, tag: str) -> str:
    return cache.sha(model, config.PROMPT_VERSION, schema.__name__, tag, system, user, n=32)


def cached_only(tier: str, schema: Type[T], system: str, user: str, tag: str = "") -> T | None:
    model = config.FAST_MODEL if tier == "cheap" else config.REASONING_MODEL
    val, _, _ = cache.get_layered("llm", _key(model, schema, system, user, tag) + ".json")
    if val is not None:
        try:
            return schema.model_validate(val)
        except Exception:
            return None
    return None


def call(tier: str, schema: Type[T], system: str, user: str, tag: str = "",
         max_tokens: int = 2500) -> T | None:
    """Return a validated pydantic object, or None if unavailable / over budget / malformed twice."""
    model = config.FAST_MODEL if tier == "cheap" else config.REASONING_MODEL
    name = _key(model, schema, system, user, tag) + ".json"
    hit = cached_only(tier, schema, system, user, tag)
    if hit is not None:
        return hit
    if not available() or not cache.budget_take("cheap" if tier == "cheap" else "deep"):
        return None
    for attempt in range(2):  # one retry on malformed output
        try:
            resp = _get_client().chat.completions.parse(
                model=model,
                temperature=0,
                max_tokens=max_tokens,
                response_format=schema,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            parsed = resp.choices[0].message.parsed
            if parsed is None:
                continue
            cache.put_cache("llm", name, json.loads(parsed.model_dump_json()))
            return parsed
        except Exception as e:
            if "rate" in str(e).lower() and attempt == 0:
                continue
            if attempt == 1:
                return None
    return None
