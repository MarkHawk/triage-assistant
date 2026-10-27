"""Settings from the environment and a gitignored .env file.

Real environment variables win over .env. No third-party parser: the format
is KEY=VALUE per line, with # comments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ("claude", "ollama", "demo")


class ConfigError(ValueError):
    """A setting is missing or not usable."""


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _int(env: dict[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from None
    if number < minimum:
        raise ConfigError(f"{key} must be at least {minimum}, got {number}")
    return number


@dataclass(frozen=True)
class Config:
    lab_dir: Path
    backend: str
    claude_api_key: str
    claude_model: str
    claude_refusal_fallback: bool
    ollama_host: str
    ollama_model: str
    ollama_num_ctx: int
    ollama_reserve_output_tokens: int
    ollama_first_timeout_s: int
    ollama_timeout_s: int
    ollama_keep_alive: str
    log_secrets: str  # "mask" (default) or "refuse"
    support_persona: str  # "vendor" (default) or "internal"
    vendor_owns: tuple[str, ...]  # in vendor mode, what is ours even when the ticket does not say "your"
    support_signoff: str  # how every customer reply ends; "" means the persona's default

    def signoff(self, company: str) -> str:
        if self.support_signoff:
            return self.support_signoff
        return "Sign-in service support" if self.support_persona == "vendor" else f"{company} IT Support"

    @property
    def ollama_prompt_budget_tokens(self) -> int:
        """Tokens available to the prompt: the context window minus the answer's room."""
        return self.ollama_num_ctx - self.ollama_reserve_output_tokens

    def require_backend_settings(self) -> None:
        """Raise ConfigError if the chosen backend lacks what it needs."""
        if self.backend == "claude" and not (self.claude_api_key and self.claude_model):
            raise ConfigError("BACKEND=claude needs CLAUDE_API_KEY and CLAUDE_MODEL in .env")
        if self.backend == "ollama" and not (self.ollama_host and self.ollama_model):
            raise ConfigError("BACKEND=ollama needs OLLAMA_HOST and OLLAMA_MODEL in .env")


def load_config(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    env = parse_env_file(env_file if env_file is not None else REPO_ROOT / ".env")
    env.update(os.environ if environ is None else environ)

    backend = env.get("BACKEND", "").strip() or "demo"
    if backend not in BACKENDS:
        raise ConfigError(f"BACKEND must be one of {', '.join(BACKENDS)}, got {backend!r}")

    lab = Path(env.get("LAB_DIR", "").strip() or "../identity-lab").expanduser()
    if not lab.is_absolute():
        lab = REPO_ROOT / lab

    num_ctx = _int(env, "OLLAMA_NUM_CTX", 12288, minimum=2048)
    reserve = _int(env, "OLLAMA_RESERVE_OUTPUT_TOKENS", 1500, minimum=256)
    if reserve >= num_ctx:
        raise ConfigError("OLLAMA_RESERVE_OUTPUT_TOKENS must be smaller than OLLAMA_NUM_CTX")

    log_secrets = env.get("LOG_SECRETS", "").strip().lower() or "mask"
    if log_secrets not in ("mask", "refuse"):
        raise ConfigError(f"LOG_SECRETS must be mask or refuse, got {log_secrets!r}")

    persona = env.get("SUPPORT_PERSONA", "").strip().lower() or "vendor"
    if persona not in ("vendor", "internal"):
        raise ConfigError(f"SUPPORT_PERSONA must be vendor or internal, got {persona!r}")

    return Config(
        lab_dir=lab.resolve(),
        backend=backend,
        claude_api_key=env.get("CLAUDE_API_KEY", "").strip(),
        claude_model=env.get("CLAUDE_MODEL", "").strip() or "claude-sonnet-5-5",
        claude_refusal_fallback=env.get("CLAUDE_REFUSAL_FALLBACK", "1").strip().lower() not in ("0", "no", "false", "off"),
        ollama_host=env.get("OLLAMA_HOST", "").strip().rstrip("/"),
        ollama_model=env.get("OLLAMA_MODEL", "").strip(),
        ollama_num_ctx=num_ctx,
        ollama_reserve_output_tokens=reserve,
        ollama_first_timeout_s=_int(env, "OLLAMA_FIRST_TIMEOUT_S", 600),
        ollama_timeout_s=_int(env, "OLLAMA_TIMEOUT_S", 300),
        ollama_keep_alive=env.get("OLLAMA_KEEP_ALIVE", "").strip() or "30m",
        log_secrets=log_secrets,
        support_persona=persona,
        vendor_owns=tuple(x.strip() for x in (env.get("VENDOR_OWNS", "").strip() or "sign-in service, identity provider, API").split(",") if x.strip()),
        support_signoff=env.get("SUPPORT_SIGNOFF", "").strip(),
    )
