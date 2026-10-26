"""Keeps secrets away from the model.

Two kinds of input, two policies:

* Log lines (from the lab) and the final prompt: any secret is a hard refusal.
  The lab masks its logs already, so a secret here means something went wrong
  and nothing may be sent.
* Customer tickets: customers do paste tokens and cookies. Those are masked,
  flagged to the engineer, and triage carries on; the reply then advises the
  customer not to send such values again.

A value from the lab's own .env is a hard refusal everywhere, tickets included.
Messages and findings name the kind of secret, never the text that matched.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import parse_env_file

_MASK = "[MASKED:{kind}]"
_NOT_MASKED = r"(?!\[MASKED|\*{3})"

# (kind, regex). The group named "secret" is the part that is masked.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(
        r"(?P<secret>-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9+/=\s]{20,}))",
        re.DOTALL)),
    ("jwt", re.compile(r"(?P<secret>eyJ[\w-]{5,}\.eyJ[\w-]{5,}\.[\w-]*)")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+(?P<secret>" + _NOT_MASKED + r"[A-Za-z0-9._~+/=-]{16,})")),
    ("cookie", re.compile(r"(?im)^[ \t]*(?:set-)?cookie:[ \t]*(?P<secret>" + _NOT_MASKED + r"[\w.-]+=.+)$")),
    ("cookie", re.compile(
        r"(?i)(?<![\w-])(?:KEYCLOAK_SESSION(?:_LEGACY)?|KEYCLOAK_IDENTITY(?:_LEGACY)?|AUTH_SESSION_ID(?:_LEGACY)?"
        r"|KC_RESTART|session(?:id)?)=(?P<secret>" + _NOT_MASKED + r"[^;\s\"']{8,})")),
    ("login-value", re.compile(
        r"(?<![\w-])(?:code|state|session_state|nonce|code_challenge|SAMLResponse|SAMLRequest|Signature"
        r"|session_code|client_data)=(?P<secret>" + _NOT_MASKED + r"[^&\s\"'<>]{12,})")),
    ("saml-message", re.compile(
        r"(?<![A-Za-z0-9+/=])(?P<secret>(?:PHNhbW[wx]|PD94bWw)[A-Za-z0-9+/=]{200,})")),
    ("secret-value", re.compile(
        r"(?i)(?<![\w-])(?:password|passwd|pwd|client_secret|secret|api[_-]?key|access_token|id_token|refresh_token|token)"
        r"[\"']?[ \t]*[=:][ \t]*[\"']?(?P<secret>" + _NOT_MASKED + r"[^\s&;,\"'<>]{4,})")),
]

_LAB_KEY = re.compile(r"PASSWORD|SECRET|TOKEN|KEY")
_LAB_MIN_LENGTH = 8


class SecretFound(Exception):
    """Something that must not be sent was found. The message holds counts only."""


@dataclass(frozen=True)
class Finding:
    kind: str
    count: int


@dataclass(frozen=True)
class MaskedTicket:
    text: str
    findings: tuple[Finding, ...]

    @property
    def flagged(self) -> bool:
        return bool(self.findings)

    @property
    def engineer_flag(self) -> str:
        """Shown to the engineer in the output."""
        if not self.findings:
            return ""
        what = ", ".join(f"{f.count} x {f.kind}" for f in self.findings)
        return (f"WARNING: the ticket contained secret values ({what}). They were masked before "
                "anything was sent to a model. Consider asking the customer to reset anything that "
                "was exposed.")

    @property
    def model_note(self) -> str:
        """Added to the model's input so the customer reply carries the advice."""
        if not self.findings:
            return ""
        return ("Note from the system: the customer pasted secret values (shown as [MASKED:...]). "
                "Do not try to read them. In the customer reply, politely advise them not to send "
                "passwords, tokens, cookies or SAML responses in a ticket.")


def load_lab_secrets(lab_dir: Path) -> frozenset[str]:
    """Secret-looking values from the lab's .env. Kept in memory only."""
    values = parse_env_file(lab_dir / ".env")
    return frozenset(v for k, v in values.items() if _LAB_KEY.search(k) and len(v) >= _LAB_MIN_LENGTH)


class Guard:
    def __init__(self, lab_secrets: frozenset[str] | set[str] = frozenset()):
        self._lab_secrets = frozenset(lab_secrets)

    @classmethod
    def for_lab(cls, lab_dir: Path) -> "Guard":
        return cls(load_lab_secrets(lab_dir))

    # -- scanning ---------------------------------------------------------------

    def _lab_hits(self, text: str) -> int:
        return sum(1 for s in self._lab_secrets if s in text)

    def scan(self, text: str) -> list[Finding]:
        """Every kind of secret in the text, with counts. Lab values come first."""
        found: Counter[str] = Counter()
        lab = self._lab_hits(text)
        if lab:
            found["lab-env-value"] = lab
        for kind, pattern in _PATTERNS:
            for _ in pattern.finditer(text):
                found[kind] += 1
        return [Finding(k, n) for k, n in found.items()]

    @staticmethod
    def _describe(findings: list[Finding]) -> str:
        return ", ".join(f"{f.count} x {f.kind}" for f in findings)

    # -- policies ---------------------------------------------------------------

    def check_log(self, text: str, source: str = "log text") -> None:
        """Hard refusal for log-sourced text."""
        findings = self.scan(text)
        if findings:
            raise SecretFound(f"refused: {source} contains {self._describe(findings)}; nothing was sent")

    def check_outbound(self, untrusted: list[str], trusted: list[str] | None = None) -> None:
        """The last gate, at the backend boundary, over every part of the prompt.

        Untrusted parts (logs, the ticket after masking) get every check. Trusted
        parts (the system prompt, runbook sections) only get the lab-.env check:
        runbooks legitimately show example commands and sample values.
        """
        for text in untrusted:
            self.check_log(text, source="the outgoing prompt")
        for text in trusted or []:
            if self._lab_hits(text):
                raise SecretFound("refused: the outgoing prompt contains a value from the lab's .env; nothing was sent")

    def mask_ticket(self, text: str) -> MaskedTicket:
        """Mask pasted secrets in a ticket. A lab .env value is still a hard refusal."""
        if self._lab_hits(text):
            raise SecretFound("refused: the ticket contains a value from the lab's .env; nothing was sent")
        counts: Counter[str] = Counter()

        def mask_with(kind: str):
            def repl(match: re.Match[str]) -> str:
                counts[kind] += 1
                start, end = match.span("secret")
                offset = match.start()
                whole = match.group(0)
                return whole[: start - offset] + _MASK.format(kind=kind) + whole[end - offset:]
            return repl

        for kind, pattern in _PATTERNS:
            text = pattern.sub(mask_with(kind), text)
        return MaskedTicket(text=text, findings=tuple(Finding(k, n) for k, n in counts.items()))
