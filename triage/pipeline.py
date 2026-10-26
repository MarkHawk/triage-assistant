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
from .backends.base import Backend, BackendError
from .clarify import build_clarify_answer
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


def run_triage(ticket_text: str, logs_text: str, *, backend: Backend, retriever: Retriever,
               scenarios: dict[str, Scenario], guard: Guard, system_text: str, log_secrets: str = "mask") -> Triage:
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
    scenario_runbooks = {name: s.runbook for name, s in scenarios.items()}
    retrieved = {m.runbook.number: (m.runbook.path, m.runbook.title, m.strong) for m in result.matches}
    # A quote may come from the logs as the model saw them (tidied) or as supplied.
    ctx = ans.Context(mode=mode, logs=logs_text + "\n" + tidy_logs(logs_text), ticket=masked.text, scenario_runbooks=scenario_runbooks,
                      retrieved=retrieved, user_side_hint=bool(result.hints), ticket_was_flagged=masked.flagged)

    prompt = build_prompt(system_text, mode, masked.text, logs_text, list(result.matches), result.hints,
                          masked.model_note, backend.budget)
    if mode == "clarify" and backend.offline and not backend.can_replay(prompt):
        offline = build_clarify_answer(list(result.matches), result.hints)
        ans.add_secrets_advice(offline, masked.flagged)
        return Triage(offline, mode, result, flags)
    schema = ans.output_schema(list(scenario_runbooks))
    flags += prompt.notes

    feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        asked = prompt if not feedback else _with_feedback(prompt, feedback)
        raw = backend.complete(asked, schema)
        attempts = attempt + 1
        try:
            answer, violations = ans.check(raw, ctx)
        except ans.AnswerError as exc:
            violations, answer = [f"the reply was unusable: {exc}"], None
        if not violations:
            return Triage(answer, mode, result, flags, prompt, raw, attempts, False, dict(backend.last_stats))
        feedback = "Your previous reply broke these rules, so it was discarded: " + "; ".join(violations) + ". Reply again."
    if (mode == "diagnose" and answer is not None
            and all(v.startswith(ans.CONTRADICTION) for v in violations)):  # retried once; keep it, but say so
        answer.confidence = "low"
        answer.warnings.insert(0, "The answer contradicts itself and the model did not resolve it when asked: "
                                  + violations[0].removeprefix(ans.CONTRADICTION).strip())
        flags.append("WARNING: this answer contradicts itself (the outcome says not a lab fault, but it gives a cause and "
                     "evidence for a runbook that matched strongly). Confidence was capped at low. Read it with care.")
        return Triage(answer, mode, result, flags, prompt, raw, attempts, False, dict(backend.last_stats))
    if mode == "clarify":  # the model keeps asserting; code builds a safe answer instead
        fallback = build_clarify_answer(list(result.matches), result.hints)
        ans.add_secrets_advice(fallback, masked.flagged)
        fallback.warnings.insert(0, "The model's answer broke the clarify rules twice, so this one was built by code: "
                                    + "; ".join(violations))
        return Triage(fallback, mode, result, flags, prompt, None, attempts, True, dict(backend.last_stats))
    raise BackendError("The model's answer could not be used: " + "; ".join(violations))


def _with_feedback(prompt: Prompt, feedback: str) -> Prompt:
    return Prompt(system=prompt.system, user=prompt.user + "\n\n" + feedback, mode=prompt.mode,
                  untrusted=prompt.untrusted, trusted=prompt.trusted, key_material=prompt.key_material,
                  notes=prompt.notes, parts_tokens=prompt.parts_tokens)
