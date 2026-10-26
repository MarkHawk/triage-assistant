"""Ollama on another machine on the LAN.

Three things are never left to chance for a model of about 14B on a 16 GB GPU:
the context window is sent with every request (num_ctx), the answer length is
capped (num_predict), and a prompt the server had to cut is an error, because
Ollama truncates silently otherwise. The first request may include loading the
model into the GPU, so it gets its own, longer timeout.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from ..prompt import Budget, Prompt
from .base import Backend, BackendError, PromptTruncated

# If the server read at least this much of the window, assume it cut the prompt.
TRUNCATION_MARGIN = 16


class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, guard, budget: Budget, host: str, model: str, num_ctx: int, num_predict: int,
                 first_timeout_s: int, timeout_s: int, keep_alive: str):
        super().__init__(guard, budget)
        self.host, self.model = host.rstrip("/"), model
        self.num_ctx, self.num_predict = num_ctx, num_predict
        self.first_timeout_s, self.timeout_s, self.keep_alive = first_timeout_s, timeout_s, keep_alive
        self._used = False

    def request_body(self, prompt: Prompt, schema: dict) -> dict:
        return {
            "model": self.model,
            "stream": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": self.num_predict, "temperature": 0},
            "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
        }

    def _complete(self, prompt: Prompt, schema: dict) -> dict:
        body = json.dumps(self.request_body(prompt, schema)).encode()
        request = urllib.request.Request(f"{self.host}/api/chat", data=body, headers={"Content-Type": "application/json"})
        timeout = self.timeout_s if self._used else self.first_timeout_s
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                data = json.load(reply)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise BackendError(f"Ollama returned {exc.code}: {detail}") from None
        except (socket.timeout, TimeoutError):
            hint = " The first request also loads the model, so raise OLLAMA_FIRST_TIMEOUT_S." if not self._used else \
                " Raise OLLAMA_TIMEOUT_S, or check the machine is not busy."
            raise BackendError(f"Ollama did not answer within {timeout} seconds.{hint}") from None
        except urllib.error.URLError as exc:
            raise BackendError(f"Could not reach Ollama at {self.host}: {exc.reason}") from None
        self._used = True
        self.last_stats = {
            "prompt_tokens_read": data.get("prompt_eval_count"), "answer_tokens": data.get("eval_count"),
            "model_load_s": round(data.get("load_duration", 0) / 1e9, 1), "num_ctx": self.num_ctx,
            "done_reason": data.get("done_reason"),
        }

        read = data.get("prompt_eval_count")
        if isinstance(read, int) and read >= self.num_ctx - TRUNCATION_MARGIN:
            raise PromptTruncated(
                f"Ollama read {read} tokens, which fills the {self.num_ctx}-token window, so it probably cut the prompt. "
                "Raise OLLAMA_NUM_CTX (check the GPU has room) or send less.")
        if data.get("done_reason") == "length":
            raise BackendError(f"The answer was cut off at {self.num_predict} tokens (OLLAMA_RESERVE_OUTPUT_TOKENS).")
        content = (data.get("message") or {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            raise BackendError("Ollama did not return valid JSON; the model may be too small to follow the format.") from None
