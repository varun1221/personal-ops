"""Model provider factory.

Kept behind one function so the provider is a config change, not a code change.
Develop against a model with reliable tool calling, then flip to the free tier and
see what breaks — the difference is instructive, and it is never the graph's fault.
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter

DEFAULTS = {
    "anthropic": "claude-sonnet-5",
    "mistral": "mistral-small-latest",
}

# Requests per second, per provider. An agent turn is several calls in quick
# succession — a tool call, a result, a follow-up — so a loop that would look
# harmless interactively trips a free tier's limiter in seconds. Mistral's free
# tier 429s well below 1 rps sustained.
RATE_LIMITS = {"anthropic": 4.0, "mistral": 0.9}


def _limiter(provider: str) -> InMemoryRateLimiter:
    rate = float(os.environ.get("MODEL_RPS") or RATE_LIMITS.get(provider, 1.0))
    return InMemoryRateLimiter(requests_per_second=rate, check_every_n_seconds=0.1,
                               max_bucket_size=1)


def get_model(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    provider = (provider or os.environ.get("MODEL_PROVIDER") or "anthropic").lower()
    model = model or os.environ.get("MODEL_NAME") or DEFAULTS.get(provider)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        _require_key("ANTHROPIC_API_KEY", provider)
        return ChatAnthropic(
            model=model,
            temperature=0,
            max_tokens=4096,
            max_retries=5,
            rate_limiter=_limiter(provider),
        )

    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        _require_key("MISTRAL_API_KEY", provider)
        # max_retries covers the 429s the limiter does not prevent; without
        # both, a long agent turn on the free tier dies mid-loop.
        return ChatMistralAI(
            model=model,
            temperature=0,
            max_retries=5,
            timeout=120,
            rate_limiter=_limiter(provider),
        )

    raise ValueError(
        f"Unknown MODEL_PROVIDER {provider!r}; expected one of {sorted(DEFAULTS)}"
    )


def _require_key(variable: str, provider: str) -> None:
    if not os.environ.get(variable):
        raise RuntimeError(
            f"{variable} is not set, but MODEL_PROVIDER={provider}. "
            f"Add it to .env."
        )


def describe() -> str:
    provider = (os.environ.get("MODEL_PROVIDER") or "anthropic").lower()
    return f"{provider}/{os.environ.get('MODEL_NAME') or DEFAULTS.get(provider, '?')}"


