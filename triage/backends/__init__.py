from __future__ import annotations

from ..config import Config
from ..guard import Guard
from ..prompt import CLAUDE_BUDGET, Budget
from .base import Backend


def ollama_budget(config: Config) -> Budget:
    return Budget(prompt_tokens=config.ollama_prompt_budget_tokens)


def make_backend(config: Config, guard: Guard, name: str | None = None) -> Backend:
    name = name or config.backend
    if name == "claude":
        from .claude import ClaudeBackend

        if not config.claude_api_key:
            from ..config import ConfigError
            raise ConfigError("BACKEND=claude needs CLAUDE_API_KEY and CLAUDE_MODEL in .env")
        return ClaudeBackend(guard, CLAUDE_BUDGET, config.claude_api_key, config.claude_model,
                             refusal_fallback=config.claude_refusal_fallback)
    if name == "ollama":
        from ..config import ConfigError
        from .ollama import OllamaBackend

        if not (config.ollama_host and config.ollama_model):
            raise ConfigError("BACKEND=ollama needs OLLAMA_HOST and OLLAMA_MODEL in .env")
        return OllamaBackend(guard, ollama_budget(config), config.ollama_host, config.ollama_model,
                             config.ollama_num_ctx, config.ollama_reserve_output_tokens,
                             config.ollama_first_timeout_s, config.ollama_timeout_s, config.ollama_keep_alive)
    from .demo import DemoBackend

    return DemoBackend(guard, CLAUDE_BUDGET)
