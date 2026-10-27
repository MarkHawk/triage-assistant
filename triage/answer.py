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
                "properties": {"runbook_number": {"type": "integer"},
                               "if_this": {"type": "string", "description": "A condition, as a fragment that follows the word If."}}}},
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
    kind: str = "answer"  # "answer", or "service_request" for an account request that is not a fault
    dropped_evidence: list[str] = field(default_factory=list)  # quoted lines that could not be verified

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
    persona: str = "vendor"  # "vendor" or "internal"; see rules.py
    causes: dict[int, str] = field(default_factory=dict)  # runbook -> its own one-sentence root cause (and title)
    documented: dict[int, tuple[str, ...]] = field(default_factory=dict)  # runbook -> its documented error lines
    confirming: str = ""  # a question that would confirm the top runbook, for repairs


def _clean(text: object) -> str:
    return re.sub(r"[ \t]+\n", "\n", str(text or "").replace(_EM_DASH, ", ")).strip()


# Straight and curly quotes, single and double, all count as the same character when quotes are checked.
_QUOTES = str.maketrans({chr(c): "'" for c in (0x22, 0x27, 0x2018, 0x2019, 0x201C, 0x201D)})


def _squash(text: str) -> str:
    """Text as compared when checking a quote: quote marks made alike, runs of whitespace made one space."""
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip()


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
    dropped: list[str] = []
    for item in raw.get("evidence") or []:
        line, why = _clean(item.get("line")), _clean(item.get("why"))
        if not line:
            continue
        if _squash(line) in full_logs:
            evidence.append(Evidence(line, why, "log"))
        elif _squash(line) in full_ticket:
            evidence.append(Evidence(line, why, "ticket"))
        else:
            dropped.append(line)

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
        possibilities=possibilities, warnings=warnings, dropped_evidence=dropped,
    )

    strip_placeholders(answer)
    violations: list[str] = []
    if ctx.mode == "clarify":
        violations = clarify_violations(answer, ctx)
    else:
        violations = contradictions(answer, ctx)
        if answer.possibilities:
            answer.possibilities = []
            answer.warnings.append("Possibilities were dropped: they belong to clarify mode only.")
        _correct_diagnosis(answer, ctx)
        violations += cause_violations(answer, ctx)
    add_secrets_advice(answer, ctx.ticket_was_flagged)
    return answer, violations


_PLACEHOLDER = re.compile(r"\[(?:your|customer|client|user|name|insert|company|engineer|support)[^\]\n]{0,30}\]", re.IGNORECASE)


def strip_placeholders(answer: Answer) -> None:
    """Remove template placeholders such as [Your Name] from the reply, and say so."""
    if _PLACEHOLDER.search(answer.customer_reply):
        cleaned = _PLACEHOLDER.sub("", answer.customer_reply)
        answer.customer_reply = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", cleaned)).strip()
        answer.warnings.append("A placeholder such as [Your Name] was removed from the reply.")


CONTRADICTION = "CONTRADICTION:"
CAUSE = "CAUSE:"
_CAUSE_MAX_DF = 2  # a word counts only if at most this many runbooks' cause statements use it
_CAUSE_MIN_SHARED = 2  # distinctive words the cause line must share with the diagnosed runbook's statement
_CAUSE_NEAR_BEST = 0.75  # and at least this share of what it shares with its best-matching runbook


def _cause_words(text: str) -> set[str]:
    from .retrieval import terms  # here, not at the top: retrieval does not need answer, and answer should not need it

    return {t for t in terms(text) if len(t) > 2}


def cause_agrees(likely_cause: str, number: int | None, causes: dict[int, str]) -> bool:
    """Whether a cause line describes the diagnosed runbook's cause rather than another runbook's.

    Only distinctive words count: those that at most two runbooks' cause statements (with their titles) use, so
    "configuration" or "new" cannot tip it. The line agrees if it shares at least two of them with the diagnosed
    runbook's statement, and at least three quarters as many as with any other runbook's. With no statement to
    compare against it is not checked. tests/test_answer.py checks that each runbook's own statement agrees with
    it and with no other runbook.
    """
    if number is None or number not in causes:
        return True
    vocab = {n: _cause_words(text) for n, text in causes.items()}
    df: dict[str, int] = {}
    for v in vocab.values():
        for w in v:
            df[w] = df.get(w, 0) + 1
    words = {w for w in _cause_words(likely_cause) if df.get(w, 0) and df[w] <= _CAUSE_MAX_DF}
    shared = {n: len(words & v) for n, v in vocab.items()}
    return shared[number] >= _CAUSE_MIN_SHARED and shared[number] >= _CAUSE_NEAR_BEST * max(shared.values())


def cause_violations(answer: Answer, ctx: Context) -> list[str]:
    """The cause line must describe the diagnosed runbook. A cause line that does not cannot carry high confidence."""
    number = _runbook_number(answer.runbook)
    if answer.outcome not in ctx.scenario_runbooks or number not in ctx.causes:
        return []
    if cause_agrees(answer.likely_cause, number, ctx.causes):
        return []
    if answer.confidence == "high":
        answer.confidence = "medium"
        answer.warnings.append("The cause line does not match the diagnosed runbook's cause; confidence capped at medium.")
    return [f"{CAUSE} likely_cause says \"{answer.likely_cause[:100]}\", which is not the cause runbook {number:02d} describes. "
            f"Describe that runbook's cause in your own words: {ctx.causes[number].split(chr(10))[0][:200]}"]


def repair_cause(answer: Answer, ctx: Context) -> None:
    """After two attempts, use the runbook's own cause statement and cap confidence at medium."""
    number = _runbook_number(answer.runbook)
    if number in ctx.causes and not cause_agrees(answer.likely_cause, number, ctx.causes):
        answer.likely_cause = ctx.causes[number].split("\n")[0]
        if answer.confidence == "high":
            answer.confidence = "medium"
        answer.warnings.append("Corrected by code: the cause line did not match the diagnosed runbook, so the runbook's own cause "
                               "statement is shown instead; confidence capped at medium.")


def contradictions(answer: Answer, ctx: Context) -> list[str]:
    """An answer of "not a lab fault" when a runbook matched strongly and nothing points to the user's side.

    Earlier a model did this nine times in nine, with high confidence, while its own cause and evidence
    described the fault. It is flagged even without evidence, because with no logs there is none to check.
    """
    strong = [n for n, (_, _, is_strong) in ctx.retrieved.items() if is_strong]
    if answer.outcome == "not_a_lab_fault" and strong and not ctx.user_side_hint:
        return [f"{CONTRADICTION} the outcome is not_a_lab_fault, but runbook {strong[0]:02d} matched this ticket strongly "
                "and nothing in it points to the user's side. If that runbook fits, set the outcome to its scenario name. "
                "If this really is not a lab fault, say why"]
    return []


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
    places = [("customer_reply", answer.customer_reply), *(("a question", q) for q in answer.questions_for_customer),
              *((f"the possibility for runbook {p.runbook_number}", p.if_this) for p in answer.possibilities)]
    for where, text in places:
        hit = _ASSERTS.search(text)
        if hit:
            snippet = text[max(0, hit.start() - 20): hit.end() + 40].replace("\n", " ")
            problems.append(f'{where} says "{snippet}", which states a cause. A possibility must be a condition after the word '
                            '"If", such as "the page shows a 401 error"')
    return problems


def documented_text_quoted(answer: Answer, ctx: Context) -> str | None:
    """The runbook's documented error line the ticket quotes word for word, if all three conditions hold:
    the runbook matched strongly, the ticket contains one of its documented lines, and the diagnosed
    scenario belongs to that runbook."""
    number = _runbook_number(answer.runbook)
    info = ctx.retrieved.get(number)
    if info is None or not info[2]:
        return None
    ticket = _squash(ctx.ticket).lower()
    return next((line for line in ctx.documented.get(number, ()) if _squash(line).lower() in ticket), None)


def _correct_diagnosis(answer: Answer, ctx: Context) -> None:
    # Only quoted log lines lift the cap. The customer's own words are not evidence that the diagnosis is right.
    verified = any(e.source == "log" for e in answer.evidence)
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
        quoted = documented_text_quoted(answer, ctx)
        if quoted:
            answer.warnings.append(f"High confidence without log lines: the ticket quotes the runbook's documented error text "
                                   f"word for word ({quoted[:80]!r}).")
        else:
            answer.confidence = "medium"
            answer.warnings.append("No quoted log line could be verified; confidence capped at medium.")


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
    if answer.kind == "service_request":
        title = "Service request, not a fault"
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
    if answer.warnings or answer.dropped_evidence:
        out.append("")
        out.append("### For the engineer (not for the customer)")
        out.extend(f"- {w}" for w in dict.fromkeys(answer.warnings))
        if answer.dropped_evidence:
            n = len(answer.dropped_evidence)
            out.append(f"- {n} quoted line{'s were' if n != 1 else ' was'} not found word for word in the supplied text, "
                       f"so {'they were' if n != 1 else 'it was'} dropped from the evidence above. Treat {'them' if n != 1 else 'it'} as unverified:")
            out.extend(f"  - `{line[:300]}{'...' if len(line) > 300 else ''}`" for line in answer.dropped_evidence)
    return "\n".join(out).rstrip() + "\n"
