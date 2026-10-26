"""Claude through the Anthropic SDK.

Structured output uses output_config.format, not a forced tool choice, which
Claude Sonnet 5.5 rejects. Thinking is left at the model's default and effort is
set explicitly. A refusal is surfaced, never treated as an empty answer.
"""

from __future__ import annotations

import json

from ..prompt import Budget, Prompt
from .base import Backend, BackendError, Refused

MAX_TOKENS = 16000  # thinking counts toward this, so it is not set tight
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeBackend(Backend):
    name = "claude"

    def __init__(self, guard, budget: Budget, api_key: str, model: str, effort: str = "medium",
                 refusal_fallback: bool = True, client=None):
        super().__init__(guard, budget)
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self._client = client

    def request_kwargs(self, prompt: Prompt, schema: dict) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": prompt.system,
            "messages": [{"role": "user", "content": prompt.user}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if self.refusal_fallback:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        return kwargs

    def _complete(self, prompt: Prompt, schema: dict) -> dict:
        import anthropic

        kwargs = self.request_kwargs(prompt, schema)
        try:
            api = self._client.beta.messages if self.refusal_fallback else self._client.messages
            response = api.create(**kwargs)
        except anthropic.AuthenticationError:
            raise BackendError("Claude rejected the API key. Check CLAUDE_API_KEY in .env.") from None
        except anthropic.RateLimitError:
            raise BackendError("Claude is rate limiting this key. Wait a moment and try again.") from None
        except anthropic.APIConnectionError as exc:
            raise BackendError(f"Could not reach the Claude API: {exc}") from None
        except anthropic.APIStatusError as exc:
            raise BackendError(f"The Claude API returned {exc.status_code}: {exc.message}") from None

        usage = getattr(response, "usage", None)
        iterations = getattr(usage, "iterations", None) or []
        self.last_stats = {
            "model_served": getattr(response, "model", self.model),
            "input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None),
            "stop_reason": response.stop_reason, "refusal_fallback_ran": any(getattr(i, "type", "") == "fallback_message" for i in iterations),
        }
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise Refused(f"Claude declined to answer (category: {category or 'unspecified'}).")
        if response.stop_reason == "max_tokens":
            raise BackendError("Claude's answer was cut off at the token limit.")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise BackendError("Claude did not return valid JSON.") from None
