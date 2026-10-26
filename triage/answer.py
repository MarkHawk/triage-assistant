"""The answer: its schema, the checks on what a model returns, and how it is shown.

A model's output is never trusted as it comes. It is checked and corrected here:
evidence lines must appear word for word in what was supplied, the runbook is
derived from the outcome rather than taken from the model, confidence is capped
when the evidence is thin, and in clarify mode no cause may be asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SPECIAL_OUTCOMES = ("not_a_lab_fault", "needs_more_info")
CONFIDENCE = ("high", "medium", "low")
MODES = ("diagnose", "clarify")

# Phrases that state a cause. Banned in clarify mode, where nothing is established yet.
_ASSERTS = re.compile(
    r"\b(the\s+(root\s+)?cause\s+(is|was)|root\s+cause|(is|was|are|were)\s+caused\s+by|"
    r"the\s+(problem|issue|fault)\s+(is|was)|definitely|certainly|we\s+have\s+found\s+the)\b",
    re.IGNORECASE,
)
_ALREADY_ADVISED = re.compile(r"\b(do\s+not|don't|never|please\s+avoid)\b[^.]*\b(send|share|paste|post)\b[^.]*\b(password|token|cookie|secret)",
                              re.IGNORECASE)
_EM_DASH = chr(0x2014)  # written as a number so this file holds no em dash itself
_SECRETS_ADVICE = ("Please do not send passwords, tokens, cookies or sign-in responses in a ticket. "
                   "If you have shared one, change the password or sign out everywhere.")


def output_schema(scenario_names: list[str]) -> dict:
    """JSON Schema for what the model must return. Every field is required."""
    text_list = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "confidence", "likely_cause", "evidence", "fix_steps",
                     "customer_reply", "questions_for_customer", "possibilities"],
        "properties": {
            "outcome": {"type": "string", "enum": sorted(scenario_names) + list(SPECIAL_OUTCOMES)},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "likely_cause": {"type": "string", "description": "Empty unless a cause is established."},
            "evidence": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["line", "why"],
                "properties": {"line": {"type": "string"}, "why": {"type": "string"}}}},
            "fix_steps": text_list,
            "customer_reply": {"type": "string"},
            "questions_for_customer": text_list,
            "possibilities": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["runbook_number", "if_this"],
                "properties": {"runbook_number": {"type": "integer"}, "if_this": {"type": "string"}}}},
        },
    }


@dataclass(frozen=True)
class Evidence:
    line: str
    why: str
    source: str  # "log" or "ticket"


@dataclass(frozen=True)
class Possibility:
    runbook_number: int
    runbook: str  # path in the lab
    title: str
    if_this: str


@dataclass
class Answer:
    mode: str
    outcome: str
    confidence: str
    likely_cause: str
    evidence: list[Evidence]
    runbook: str | None
    fix_steps: list[str]
    customer_reply: str
    questions_for_customer: list[str]
    possibilities: list[Possibility]
    warnings: list[str] = field(default_factory=list)
    source: str = "model"  # "model" or "offline" (built by code, no model)

    @property
    def asserts_cause(self) -> bool:
        return bool(self.likely_cause.strip())


@dataclass(frozen=True)
class Context:
    """What the checks need to know about the request."""
    mode: str
    logs: str  # the full log text that was supplied
    ticket: str  # the masked ticket text
    scenario_runbooks: dict[str, str]  # scenario name -> runbook path
    retrieved: dict[int, tuple[str, str, bool]]  # runbook number -> (path, title, strong)
    user_side_hint: bool = False
    ticket_was_flagged: bool = False


def _clean(text: object) -> str:
    return re.sub(r"[ \t]+\n", "\n", str(text or "").replace(_EM_DASH, ", ")).strip()


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strings(value: object) -> list[str]:
    return [s for s in (_clean(v) for v in (value if isinstance(value, list) else [])) if s]


class AnswerError(ValueError):
    """The model's output could not be turned into an answer at all."""


def check(raw: dict, ctx: Context) -> tuple[Answer, list[str]]:
    """Build an Answer from raw model output. Returns it and any clarify-mode violations.

    Violations are rule breaks a retry could fix; the caller decides what to do. Quiet
    corrections (dropped evidence, derived runbook, capped confidence) become warnings.
    """
    if not isinstance(raw, dict):
        raise AnswerError("the model did not return a JSON object")
    outcome = _clean(raw.get("outcome"))
    allowed = set(ctx.scenario_runbooks) | set(SPECIAL_OUTCOMES)
    if outcome not in allowed:
        raise AnswerError(f"unknown outcome {outcome!r}")
    confidence = _clean(raw.get("confidence")).lower()
    if confidence not in CONFIDENCE:
        raise AnswerError(f"unknown confidence {confidence!r}")

    warnings: list[str] = []
    full_logs, full_ticket = _squash(ctx.logs), _squash(ctx.ticket)
    evidence: list[Evidence] = []
    for item in raw.get("evidence") or []:
        line, why = _clean(item.get("line")), _clean(item.get("why"))
        if not line:
            continue
        if _squash(line) in full_logs:
            evidence.append(Evidence(line, why, "log"))
        elif _squash(line) in full_ticket:
            evidence.append(Evidence(line, why, "ticket"))
        else:
            warnings.append("A quoted line was dropped because it does not appear in the supplied text.")

    possibilities: list[Possibility] = []
    for item in raw.get("possibilities") or []:
        number = item.get("runbook_number") if isinstance(item, dict) else None
        if number in ctx.retrieved and _clean(item.get("if_this")):
            path, title, _ = ctx.retrieved[number]
            possibilities.append(Possibility(number, path, title, _clean(item["if_this"])))
        else:
            warnings.append("A possibility was dropped because it names a runbook that was not supplied.")

    answer = Answer(
        mode=ctx.mode, outcome=outcome, confidence=confidence,
        likely_cause=_clean(raw.get("likely_cause")), evidence=evidence,
        runbook=ctx.scenario_runbooks.get(outcome), fix_steps=_strings(raw.get("fix_steps")),
        customer_reply=_clean(raw.get("customer_reply")),
        questions_for_customer=_strings(raw.get("questions_for_customer")),
        possibilities=possibilities, warnings=warnings,
    )

    violations: list[str] = []
    if ctx.mode == "clarify":
        violations = clarify_violations(answer, ctx)
    else:
        _correct_diagnosis(answer, ctx)
    add_secrets_advice(answer, ctx.ticket_was_flagged)
    return answer, violations


def clarify_violations(answer: Answer, ctx: Context) -> list[str]:
    """Rules for an answer that may not assert a cause. Empty list means it complies."""
    problems: list[str] = []
    if answer.outcome == "needs_more_info":
        pass
    elif answer.outcome == "not_a_lab_fault" and ctx.user_side_hint:
        pass
    else:
        problems.append(f"the outcome must be needs_more_info, not {answer.outcome}")
    if answer.asserts_cause:
        problems.append("likely_cause must be empty: no cause is established")
    if answer.fix_steps:
        problems.append("fix_steps must be empty: there is no cause to fix")
    if answer.confidence != "low":
        problems.append("confidence must be low")
    if not answer.questions_for_customer:
        problems.append("ask at least one clarifying question")
    texts = [answer.customer_reply, *answer.questions_for_customer, *(p.if_this for p in answer.possibilities)]
    if any(_ASSERTS.search(t) for t in texts):
        problems.append("do not state or imply a cause; mention runbooks only as conditional possibilities")
    return problems


def _correct_diagnosis(answer: Answer, ctx: Context) -> None:
    verified = bool(answer.evidence)
    if answer.outcome in ctx.scenario_runbooks:
        number = _runbook_number(answer.runbook)
        info = ctx.retrieved.get(number)
        if info is None:
            answer.confidence = "low"
            answer.warnings.append("The outcome is a runbook that was not among those retrieved; confidence set to low.")
        elif not info[2] and answer.confidence == "high":
            answer.confidence = "medium"
            answer.warnings.append("The matching runbook was only a weak match; confidence capped at medium.")
    if not verified and answer.confidence == "high":
        answer.confidence = "medium"
        answer.warnings.append("No quoted evidence could be verified; confidence capped at medium.")


def _runbook_number(path: str | None) -> int | None:
    match = re.search(r"/(\d+)-", path or "")
    return int(match.group(1)) if match else None


def add_secrets_advice(answer: Answer, ticket_was_flagged: bool) -> None:
    """If the customer pasted secrets, make sure the reply tells them not to."""
    if ticket_was_flagged and not _ALREADY_ADVISED.search(answer.customer_reply):
        answer.customer_reply = (answer.customer_reply + "\n\n" + _SECRETS_ADVICE).strip()


def render(answer: Answer, flags: list[str] | None = None) -> str:
    """The answer as Markdown for the engineer."""
    out: list[str] = []
    for flag in flags or []:
        out.append(f"> {flag}")
    if out:
        out.append("")
    title = {"needs_more_info": "Needs more information", "not_a_lab_fault": "Not a lab fault"}.get(
        answer.outcome, f"Likely scenario: {answer.outcome}")
    out.append(f"## {title}")
    out.append(f"Confidence: **{answer.confidence}**" + ("  (built by code, no model was used)" if answer.source == "offline" else ""))
    if answer.mode == "clarify":
        out.append("")
        out.append("No cause is asserted: no runbook is a strong match, so the answer below is questions, not a diagnosis.")
    out.append("")
    if answer.asserts_cause:
        out.append("### Likely cause")
        out.append(answer.likely_cause)
        out.append("")
    if answer.evidence:
        out.append("### Evidence")
        for item in answer.evidence:
            out.append(f"- `{item.line}`" + (f" ({item.source})" if item.source != "log" else "") + (f"\n  {item.why}" if item.why else ""))
        out.append("")
    if answer.runbook:
        out.append(f"### Runbook\n{answer.runbook}\n")
    if answer.possibilities:
        out.append("### Possibilities (only if the answers confirm them)")
        for p in answer.possibilities:
            out.append(f"- If {p.if_this}: runbook {p.runbook_number:02d}, {p.title} ({p.runbook}).")
        out.append("")
    if answer.fix_steps:
        out.append("### Fix steps")
        out.extend(f"{i}. {step}" for i, step in enumerate(answer.fix_steps, 1))
        out.append("")
    if answer.questions_for_customer:
        out.append("### Questions for the customer")
        out.extend(f"{i}. {q}" for i, q in enumerate(answer.questions_for_customer, 1))
        out.append("")
    out.append("### Draft reply to the customer")
    out.append(answer.customer_reply)
    if answer.warnings:
        out.append("")
        out.append("### Notes")
        out.extend(f"- {w}" for w in dict.fromkeys(answer.warnings))
    return "\n".join(out).rstrip() + "\n"
