"""From a ticket to an answer.

    mask the ticket -> refuse secrets in logs -> retrieve runbooks -> choose a mode
    -> build the prompt within budget -> ask the backend -> check what came back

Modes:
  diagnose  at least one strong runbook match: a cause may be stated, with evidence.
  clarify   only weak matches, or none: no cause is asserted. Questions only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import answer as ans
from . import rules, service_requests
from .backends.base import Backend, BackendError
from .clarify import build_clarify_answer, questions_from
from .guard import Guard
from .logs import tidy_logs
from .prompt import Prompt, build_prompt
from .retrieval import Result, Retriever
from .scenarios import Scenario

MAX_ATTEMPTS = 2  # one retry when a clarify-mode answer breaks the rules


@dataclass
class Triage:
    answer: ans.Answer
    mode: str
    retrieval: Result
    flags: list[str]  # shown to the engineer above the answer
    prompt: Prompt | None = None
    raw: dict | None = None  # what the model returned, for --record
    attempts: int = 0  # requests made to the backend; 2 means the first answer was sent back
    code_fallback: bool = False  # the model kept breaking the clarify rules and code built the answer
    stats: dict = field(default_factory=dict)  # what the backend reported about its last request


def decide_mode(result: Result) -> str:
    return "diagnose" if any(m.strong for m in result.matches) else "clarify"


DEFAULT_OWNED = ("sign-in service", "identity provider", "API")


def run_triage(ticket_text: str, logs_text: str, *, backend: Backend, retriever: Retriever,
               scenarios: dict[str, Scenario], guard: Guard, system_text: str, log_secrets: str = "mask",
               persona: str = "vendor", owned: tuple[str, ...] = DEFAULT_OWNED, signoff: str = "", company: str = "") -> Triage:
    """Triage one ticket. Every reply ends with the same sign-off: `signoff`, or the persona's default."""
    result = _triage(ticket_text, logs_text, backend=backend, retriever=retriever, scenarios=scenarios, guard=guard,
                     system_text=system_text, log_secrets=log_secrets, persona=persona, owned=owned)
    rules.apply_signoff(result.answer, signoff or ("Sign-in service support" if persona == "vendor" else "IT Support"), company)
    return result


def _triage(ticket_text: str, logs_text: str, *, backend: Backend, retriever: Retriever,
            scenarios: dict[str, Scenario], guard: Guard, system_text: str, log_secrets: str,
            persona: str, owned: tuple[str, ...]) -> Triage:
    """Secrets in the ticket, and by default in the logs, are masked and flagged. With log_secrets="refuse" a secret
    in the logs raises SecretFound. A lab .env value anywhere raises SecretFound. BackendError if the model
    could not be used."""
    masked = guard.mask_ticket(ticket_text)
    flags = [masked.engineer_flag] if masked.flagged else []
    if log_secrets == "refuse":
        guard.check_log(logs_text, source="the log lines")
    else:
        masked_logs = guard.mask_logs(logs_text)
        logs_text = masked_logs.text
        if masked_logs.flagged:
            flags.append(masked_logs.engineer_flag)

    result = retriever.search(masked.text, logs_text)
    mode = decide_mode(result)
    if mode == "clarify":  # nothing matched strongly: an account request is answered by code, with no model
        request = service_requests.detect(masked.text)
        if request:
            answer = service_requests.build_answer(request, persona)
            ans.add_secrets_advice(answer, masked.flagged)
            return Triage(answer, "request", result, flags)
    scenario_runbooks = {name: s.runbook for name, s in scenarios.items()}
    retrieved = {m.runbook.number: (m.runbook.path, m.runbook.title, m.strong) for m in result.matches}
    strong = next((m for m in result.matches if m.strong), None)
    confirming = next((q for q in questions_from([strong]) if "`" not in q), "") if strong else ""
    # A quote may come from the logs as the model saw them (tidied) or as supplied.
    ctx = ans.Context(mode=mode, logs=logs_text + "\n" + tidy_logs(logs_text), ticket=masked.text, scenario_runbooks=scenario_runbooks,
                      retrieved=retrieved, user_side_hint=bool(result.hints), ticket_was_flagged=masked.flagged,
                      persona=persona, causes={n: b.cause_statement + "\n" + b.title for n, b in retriever.runbooks.items()},
                      reference=retriever.reference, owned=rules.owned_names(owned) if persona == "vendor" else (),
                      documented=retriever.documented, confirming=confirming)

    prompt = build_prompt(system_text, mode, masked.text, logs_text, list(result.matches), result.hints,
                          masked.model_note, backend.budget)
    if mode == "clarify" and backend.offline and not backend.can_replay(prompt):
        return Triage(_code_built(result, masked.flagged, ctx), mode, result, flags)
    schema = ans.output_schema(list(scenario_runbooks))
    flags += prompt.notes

    feedback = ""
    answer, violations, raw, attempts = None, [], None, 0
    for attempt in range(MAX_ATTEMPTS):
        asked = prompt if not feedback else _with_feedback(prompt, feedback)
        raw = backend.complete(asked, schema)
        attempts = attempt + 1
        try:
            answer, violations = ans.check(raw, ctx)
            violations = violations + rules.content_violations(answer, ctx)
        except ans.AnswerError as exc:
            violations, answer = [f"the reply was unusable: {exc}"], None
        if not violations:
            return Triage(answer, mode, result, flags, prompt, raw, attempts, False, dict(backend.last_stats))
        feedback = "Your previous reply broke these rules, so it was discarded: " + "; ".join(violations) + ". Reply again."

    # Out of attempts. Rule breaks that code can put right are put right; anything else is not kept.
    fixable = (*rules.REPAIRABLE, ans.CONTRADICTION, ans.CAUSE)
    unfixable = [v for v in violations if not v.startswith(fixable)]
    if answer is not None and not unfixable:
        if any(v.startswith(ans.CONTRADICTION) for v in violations):
            answer.confidence = "low"
            answer.warnings.insert(0, "The answer contradicts itself and the model did not resolve it when asked: "
                                      + next(v for v in violations if v.startswith(ans.CONTRADICTION)).removeprefix(ans.CONTRADICTION).strip())
            flags.append("WARNING: this answer says not a lab fault although a runbook matched strongly and nothing points "
                         "to the user's side. Confidence was capped at low. Read it with care.")
        changes = rules.repair(answer, ctx)
        if any(v.startswith(ans.CAUSE) for v in violations) or (mode == "diagnose" and not answer.likely_cause):
            ans.repair_cause(answer, ctx)
            flags.append("WARNING: the model's cause line did not match the runbook it diagnosed, even when asked again, so the "
                         "runbook's own cause statement is shown and confidence is capped at medium.")
        if changes:
            flags.append("WARNING: the model broke the reply rules twice, so code corrected the answer (" + "; ".join(changes)
                         + "). Read it before sending.")
        return Triage(answer, mode, result, flags, prompt, raw, attempts, False, dict(backend.last_stats))
    if mode == "clarify":  # the model keeps breaking the clarify rules; code builds a safe answer instead
        fallback = _code_built(result, masked.flagged, ctx)
        fallback.warnings.insert(0, "The model's answer broke the clarify rules twice, so this one was built by code: "
                                    + "; ".join(violations))
        return Triage(fallback, mode, result, flags, prompt, None, attempts, True, dict(backend.last_stats))
    raise BackendError("The model's answer could not be used: " + "; ".join(violations))


def _code_built(result: Result, ticket_was_flagged: bool, ctx: ans.Context) -> ans.Answer:
    built = build_clarify_answer(list(result.matches), result.hints)
    ans.add_secrets_advice(built, ticket_was_flagged)
    rules.repair(built, ctx)  # the runbooks' own questions can carry lab wording
    return built


def _with_feedback(prompt: Prompt, feedback: str) -> Prompt:
    return Prompt(system=prompt.system, user=prompt.user + "\n\n" + feedback, mode=prompt.mode,
                  untrusted=prompt.untrusted, trusted=prompt.trusted, key_material=prompt.key_material,
                  notes=prompt.notes, parts_tokens=prompt.parts_tokens)
