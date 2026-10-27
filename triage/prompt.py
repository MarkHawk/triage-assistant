"""Build the prompt, within a token budget.

The budget matters most for a local model with a fixed context window. Sizes are
estimated from characters, deliberately on the high side, and a real backend also
checks what it was told the model actually read, so nothing is cut silently.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT
from .logs import cap_logs, tidy_logs
from .names import System
from .retrieval import Match

# Characters per token, each a little below what was measured on Qwen 2.5 14B (Ollama, 2026-10):
# prose 4.2 or more, runbook extracts 3.44 at worst (of all 19), log lines 1.56. Log lines are far
# the most expensive because timestamps and ids tokenise nearly one character at a time.
# tests/test_logs_and_prompt.py fails if these rise above the measurements.
PROSE_CHARS_PER_TOKEN = 3.0
RUNBOOK_CHARS_PER_TOKEN = 3.0
LOG_CHARS_PER_TOKEN = 1.4
MEASURED = {"prose": 4.29, "runbook": 3.44, "log": 1.56}
OVERHEAD_TOKENS = 250  # the chat template and message framing

_TAGS = re.compile(r"</?(ticket|logs|runbooks?|instructions|hints|system_note)\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class Budget:
    """Token budget for the whole prompt, and ceilings that apply however large it is."""
    prompt_tokens: int
    max_ticket_chars: int = 6000
    max_log_chars: int = 12000
    ticket_share: float = 0.20
    log_share: float = 0.30
    excerpt_share: float = 0.30  # one runbook extract may use up to this share of the window

    @property
    def max_excerpt_chars(self) -> int:
        """Ceiling for one runbook extract. It grows with the window, so a bigger window shows more of each
        runbook (they run up to about 10,000 characters), and never drops below 6000."""
        return max(6000, int(self.prompt_tokens * self.excerpt_share * RUNBOOK_CHARS_PER_TOKEN))


CLAUDE_BUDGET = Budget(prompt_tokens=60000)


@dataclass
class Prompt:
    system: str
    user: str
    mode: str
    untrusted: list[str]  # text that came from people or logs: every guard check applies
    trusted: list[str]  # the system prompt and runbook extracts: only the lab .env check applies
    key_material: str  # what identifies this request for the demo backend
    notes: list[str] = field(default_factory=list)  # things the engineer should know (truncation)
    parts_tokens: int = 0  # estimated tokens

    @property
    def estimated_tokens(self) -> int:
        return self.parts_tokens

    def user_trusted_check(self) -> str:
        """The user message with the untrusted parts removed, so the lab-.env check also sees the scaffolding."""
        text = self.user
        for part in self.untrusted:
            if part:
                text = text.replace(part, "")
        return text


def tokens(text: str, kind: str = "prose") -> int:
    """Estimated tokens for text of a kind: "prose", "runbook" or "log"."""
    ratio = {"prose": PROSE_CHARS_PER_TOKEN, "runbook": RUNBOOK_CHARS_PER_TOKEN, "log": LOG_CHARS_PER_TOKEN}[kind]
    return math.ceil(len(text) / ratio)


def neutralise(text: str) -> str:
    """Stop untrusted text closing or opening the tags that frame it."""
    return _TAGS.sub("[tag removed]", text)


PERSONA_TEXT = {
    "vendor": ("You work for the vendor of the sign-in service. The customer runs their own servers, proxies, networks and "
               "applications. For anything the ticket calls theirs (our server, our side, our proxy, our network) never say that "
               "we will change, restart, check or fix it: write \"please check\" or \"we recommend\". \"We will\" is fine "
               "for our own sign-in service."),
    "internal": ("You work for the customer's own IT team, so you can change any of these systems. \"We will\" is fine for "
                 "work we can do ourselves."),
}


def load_system(company: str, systems: dict[str, System], persona: str = "vendor", path: Path | None = None) -> str:
    template = (path or REPO_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")
    template = template.replace("{{persona}}", PERSONA_TEXT[persona])
    lines = [f"- {s.display}: {s.protocol or 'supporting service'} ({', '.join(s.staff_say[:3])})" for s in systems.values()]
    return template.replace("{{company}}", company).replace("{{systems}}", "\n".join(lines)).strip()


# Deliberately from outside the lab (a shared calendar), so that if a model copies it, the copy cannot pass for a
# diagnosis and rules.py catches it. An earlier example about certificates turned up word for word as a cause line.
DIAGNOSE_REPLY_EXAMPLE = ("Thank you for the details. The most likely cause is that the shared calendar's permissions were reset "
                          "during last week's mailbox move. Please check who the calendar is shared with in your mailbox settings. "
                          "Did the calendar stop appearing straight after the move?")

_DIAGNOSE = """Mode: DIAGNOSE. At least one runbook below is a strong match.
- You may state the likely cause, in likely_cause, with the log lines that prove it in evidence.
- Pick the outcome from the strong matches. Weak matches are only possibilities: do not base the answer on one.
- Fill fix_steps with short plain actions based on the runbook's Fix section (never questions).
- Unless your confidence is high, the reply says "the most likely cause is ..." and ends with one confirming question, taken from the runbook's customer questions. Without a quoted log line your confidence is not high. The shape of such a reply, for an unrelated ticket about a calendar (copy its shape, never its words): "{reply_example}"
- Never ask for a password, shared secret, key, token or cookie.
- Do not mention lab scripts, folders, LAB_HOST, docker or Keycloak anywhere.
- possibilities must be an empty list: they are only for clarify mode.
- customer_reply: tell the customer, in plain words, what we think is wrong and what we are doing about it. Ask them to do something only if the runbook needs it."""

CLARIFY_EXAMPLE = {
    "outcome": "needs_more_info", "confidence": "low", "likely_cause": "", "evidence": [], "fix_steps": [],
    "customer_reply": ("Thank you for letting us know. To work out what is going wrong we need a little more detail. "
                       "Could you tell us what you see on the screen when it fails, whether it happens for everyone or only "
                       "for you, and how long it takes before the error appears? Northgate Supplies IT Support"),
    "questions_for_customer": ["What exactly do you see when it fails? Please copy any error message.",
                               "Does it happen for everyone, or only for some people?",
                               "How long after you press Log in does the error appear?"],
    "possibilities": [{"runbook_number": 7, "if_this": "the error is a gateway time-out that appears several seconds after the password"}],
}

_CLARIFY = """Mode: CLARIFY. No runbook below is a strong match, so nothing is established.
- Do NOT state or imply a cause. likely_cause must be an empty string and fix_steps an empty list.
- outcome must be "needs_more_info"{hint_clause}. confidence must be "low".
- questions_for_customer: 3 to 6 questions that would tell the runbooks below apart or rule them out, drawn from their symptoms and "Questions I'd ask the customer".
- possibilities: only for a runbook that could plausibly apply. Give runbook_number and if_this, a condition the customer's answers would have to confirm, written as a fragment that follows the word "If", for example "the page shows a 401 error". Never write a possibility as a finding ("The issue is ..."). If the ticket does not look like a sign-in, network login or web access problem (a printer, say), return an empty possibilities list and make the first question ask what they are trying to do.
- customer_reply: thank them, ask the questions in plain words, say nothing about a cause.

An example of a compliant reply, for a different, vague ticket. Follow its shape, not its content:
{example}"""


def build_prompt(system_text: str, mode: str, ticket_text: str, logs_text: str, matches: list[Match],
                 hints: tuple[str, ...], ticket_note: str, budget: Budget) -> Prompt:
    """Assemble the request, trimming the ticket, logs and runbook extracts to the budget."""
    shown = list(matches)  # strong first (retrieval order); each is labelled
    hint_clause = ' (or "not_a_lab_fault" only if the hint below clearly applies)' if hints else ""
    instructions = (_DIAGNOSE.replace("{reply_example}", DIAGNOSE_REPLY_EXAMPLE) if mode == "diagnose" else
                    _CLARIFY.replace("{hint_clause}", hint_clause).replace("{example}", json.dumps(CLARIFY_EXAMPLE, indent=1)))
    scaffold = "\n".join([instructions, "<ticket>", "</ticket>", "<logs>", "</logs>", "<runbooks>", "</runbooks>"])
    fixed = tokens(system_text) + tokens(scaffold) + tokens(ticket_note) + OVERHEAD_TOKENS
    remaining = budget.prompt_tokens - fixed
    if remaining < 800:
        raise ValueError("the system prompt alone leaves no room in the prompt budget; raise OLLAMA_NUM_CTX")

    notes: list[str] = []
    ticket = neutralise(ticket_text.strip())
    ticket_chars = min(budget.max_ticket_chars, int(remaining * budget.ticket_share * PROSE_CHARS_PER_TOKEN))
    if len(ticket) > ticket_chars:
        ticket = ticket[:ticket_chars].rsplit(" ", 1)[0] + " [... ticket cut to fit the prompt budget]"
        notes.append(f"The ticket was longer than the prompt budget and was cut to {len(ticket)} characters.")

    log_chars = min(budget.max_log_chars, int(remaining * budget.log_share * LOG_CHARS_PER_TOKEN))
    logs, omitted = cap_logs(tidy_logs(neutralise(logs_text)), log_chars) if logs_text.strip() else ("", 0)
    if omitted:
        notes.append(f"{omitted} log lines were left out to fit the prompt budget; error-like lines were kept first.")

    left_tokens = remaining - tokens(ticket) - tokens(logs, "log")
    each = max(int(left_tokens * RUNBOOK_CHARS_PER_TOKEN / max(len(shown), 1)), 0)
    each = min(each, budget.max_excerpt_chars)
    blocks: list[str] = []
    for m in shown:
        label = "strong" if m.strong else "weak"
        body = m.runbook.excerpt(each)
        if len(body) < len(m.runbook.excerpt(10**6)):
            notes.append(f"Runbook {m.runbook.number:02d} was shortened to {len(body)} characters to fit.")
        blocks.append(f'<runbook number="{m.runbook.number}" match="{label}" file="{m.runbook.path}">\n{body}\n</runbook>')
    runbooks_text = "\n".join(blocks) if blocks else "(no runbook matched)"

    hint_text = ("\n<hints>\n" + "\n".join(f"- {h}" for h in hints) + "\n</hints>") if hints else ""
    note_text = f"\n<system_note>\n{ticket_note}\n</system_note>" if ticket_note else ""
    log_block = logs if logs else "(no log lines were supplied)"
    user = (f"{instructions}\n\n<ticket>\n{ticket}\n</ticket>{note_text}\n<logs>\n{log_block}\n</logs>{hint_text}\n"
            f"<runbooks>\n{runbooks_text}\n</runbooks>")

    key = "\n--\n".join([ticket_text.strip(), logs_text.strip(), ",".join(f"{m.runbook.number}:{'s' if m.strong else 'w'}" for m in matches)])
    estimate = (tokens(system_text) + tokens(instructions + hint_text + note_text) + tokens(ticket)
                + tokens(log_block, "log") + tokens(runbooks_text, "runbook") + OVERHEAD_TOKENS + 40)
    return Prompt(system=system_text, user=user, mode=mode, untrusted=[ticket, logs],
                  trusted=[system_text, runbooks_text], key_material=key, notes=notes, parts_tokens=estimate)
