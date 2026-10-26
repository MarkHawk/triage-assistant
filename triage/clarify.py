"""Clarify-mode answers built by code, with no model.

Used by the demo backend, and as the fallback if a real model keeps breaking the
clarify rules. Because code writes it, it cannot assert a cause: it asks the
questions from the weak runbooks and offers those runbooks only as conditions.
"""

from __future__ import annotations

import re

from .answer import Answer, Possibility
from .retrieval import Match

_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*\S)\s*$", re.MULTILINE)
_QUOTE = re.compile(r"\*[\"“](.+?)[\"”]\*")
MAX_QUESTIONS = 6

_GENERIC = [
    "What exactly do you see on the screen? Please copy the wording of any error message, or send a screenshot.",
    "Which application or web address is this, and does it fail for everyone or only for some people?",
    "When did it start, and has anything changed on your side since it last worked (a move, an upgrade, a new network or device)?",
]
_USER_SIDE = ("Please check whether this might be on your side (for example a forgotten or mistyped password, or an "
              "old bookmark), and tell us what happens when you try again from a fresh browser window.")


def questions_from(matches: list[Match]) -> list[str]:
    """The top match's own questions first, then a couple from each of the others."""
    picked: list[str] = []
    for index, match in enumerate(matches):
        found = _NUMBERED.findall(match.runbook.sections.get("Questions I'd ask the customer", ""))
        for question in found[: (4 if index == 0 else 2)]:
            if question not in picked:
                picked.append(question)
    return picked


def condition_for(match: Match) -> str:
    quote = _QUOTE.search(match.runbook.customer_phrases)
    if quote:
        return f"what they are seeing matches “{quote.group(1).strip().rstrip('.,')}”"
    return f"the symptoms match “{match.runbook.title}”"


def build_clarify_answer(matches: list[Match], hints: tuple[str, ...], ticket_was_flagged: bool = False) -> Answer:
    questions = questions_from(matches)
    if hints:
        questions.insert(0, _USER_SIDE)
    if not questions:
        questions = list(_GENERIC)
    questions = questions[:MAX_QUESTIONS]
    possibilities = [Possibility(m.runbook.number, m.runbook.path, m.runbook.title, condition_for(m)) for m in matches]
    reply = ["Thank you for getting in touch. We do not yet have enough detail to say what is happening, "
             "so we would like to ask a few questions:", ""]
    plain = [q for q in questions if "`" not in q][:4]  # no commands or header names in a message to staff
    reply += [f"- {q}" for q in (plain or _GENERIC)]
    reply += ["", "A screenshot or the exact wording of any message is plenty. Once we have your answers we will "
              "look into it straight away."]
    return Answer(
        mode="clarify", outcome="needs_more_info", confidence="low", likely_cause="", evidence=[],
        runbook=None, fix_steps=[], customer_reply="\n".join(reply), questions_for_customer=questions,
        possibilities=possibilities,
        warnings=["Built by code without a model: no runbook is a strong match, so nothing is asserted."],
        source="offline",
    )
