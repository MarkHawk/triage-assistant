"""Read a ticket file: the customer's words, and an optional log excerpt.

    ---
    id: "001"
    channel: email
    subject: Staff Portal goes round in circles
    ---
    The message as the customer wrote it.

    ## Log excerpt
    ```
    lines pasted by the customer
    ```

The header and the log section are both optional. Header fields other than the
subject (such as the sender) are never sent to a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LOG_HEADING = re.compile(r"^##\s+Log excerpt\s*$", re.MULTILINE | re.IGNORECASE)
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Ticket:
    id: str
    subject: str
    body: str
    log_excerpt: str

    @property
    def text(self) -> str:
        """What the customer wrote, as one block."""
        return (f"Subject: {self.subject}\n\n{self.body}" if self.subject else self.body).strip()


def parse_ticket(raw: str, fallback_id: str = "") -> Ticket:
    meta: dict = {}
    match = _FRONT.match(raw)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        raw = raw[match.end():]
    log = ""
    heading = _LOG_HEADING.search(raw)
    if heading:
        section = raw[heading.end():]
        raw = raw[:heading.start()]
        fenced = _FENCE.search(section)
        log = (fenced.group(1) if fenced else section).strip("\n")
    return Ticket(id=str(meta.get("id", fallback_id)), subject=str(meta.get("subject", "")).strip(),
                  body=raw.strip(), log_excerpt=log)


def load_ticket(path: Path) -> Ticket:
    return parse_ticket(path.read_text(encoding="utf-8"), fallback_id=path.stem)
