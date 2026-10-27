"""Rules for what an answer may say to the customer and the engineer, and the repairs when a model breaks them.

Each rule has a check that returns violations (a model is sent them and asked again) and a repair that code
applies if the model still breaks the rule on its second attempt, so a broken answer never reaches anyone.

  SECRETS   a customer reply never asks for a password, shared secret, key, token or cookie
  PERSONA   as a vendor, nothing is promised on the customer's own side ("we will" becomes "we recommend")
  WORDING   below high confidence the reply says "most likely" and asks a confirming question
  LABTERM   nothing specific to the lab (scripts, .state, LAB_HOST, docker compose, Keycloak)
  STEPS     fix steps are actions, not questions

The checks that depend on the diagnosis itself (evidence, confidence caps, contradictions, the clarify rules)
are in answer.py.
"""

from __future__ import annotations

import re

from .answer import _ASSERTS, Answer, Context

SECRETS, PERSONA, WORDING, LABTERM, STEPS = "SECRETS:", "PERSONA:", "WORDING:", "LABTERM:", "STEPS:"
REPAIRABLE = (SECRETS, PERSONA, WORDING, LABTERM, STEPS)
PERSONAS = ("vendor", "internal")

_SENTENCE = re.compile(r"(?<=[.?!])\s+|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


# --- SECRETS ---------------------------------------------------------------------------------------

_NOUN = (r"(?:passwords?|passcodes?|passphrases?|pass phrases?|shared[- ]secrets?|client[- ]secrets?|secret keys?|secrets?|"
         r"private keys?|api keys?|access tokens?|refresh tokens?|id tokens?|tokens?|cookies?|credentials)")
_VERB = r"(?:send|share|provide|give|paste|post|e-?mail|supply|reveal|disclose|attach|forward|copy|tell us|let us know|read out)"
_ASKS = re.compile(
    rf"\b{_VERB}\b[^.?!\n]{{0,70}}\b{_NOUN}\b"                       # "please send us the shared secret"
    rf"|\bwhat(?:'s| is| are| was| were)\b[^.?!\n]{{0,40}}\b{_NOUN}\b"  # "what is the current shared secret?"
    rf"|\bconfirm\s+(?:the|your|its|their)\s+(?:\w+\s+)?{_NOUN}\b",     # "please confirm the current shared secret"
    re.IGNORECASE)
_NEGATED = re.compile(r"\b(?:do not|don't|dont|never|please avoid|avoid|no need to|must not|should not|shouldn't|without)\b", re.IGNORECASE)


def secret_requests(text: str) -> list[str]:
    """Sentences that ask the reader to hand over a secret. "Please do not send passwords" is not one."""
    return [s for s in sentences(text) if _ASKS.search(s) and not _NEGATED.search(s)]


# --- PERSONA ---------------------------------------------------------------------------------------

_CUSTOMER_SIDE = re.compile(
    r"\b(?:our|my|their|the customer'?s|your own)\s+(?:[\w-]+\s+){0,2}?(?:servers?|proxy|proxies|firewalls?|networks?|"
    r"load balancers?|gateways?|routers?|switch(?:es)?|controllers?|machines?|laptops?|pcs?|devices?|integrations?|"
    r"environments?|infrastructure|systems?|apps?|applications?|side|end|vpn|dns)\b|\b(?:on|at) our (?:side|end)\b",
    re.IGNORECASE)
_PROMISE = re.compile(
    r"\b(?:we will|we'll|we shall|we are going to|we're going to|we can|our team will|we plan to)\s+(?:then\s+|also\s+|now\s+)?"
    r"(?=(?:fix|change|update|reconfigure|restart|reload|adjust|correct|replace|renew|rotate|configure|apply|remove|add|set|"
    r"check|repair|modify|amend|enable|disable|re-?issue|install|upgrade|patch|reset)\b(?!\s+you\b))", re.IGNORECASE)


def customer_side(ticket: str) -> tuple[str, ...]:
    """Parts of the customer's own estate that the ticket mentions ("our server", "on our side")."""
    return tuple(dict.fromkeys(m.group(0).lower() for m in _CUSTOMER_SIDE.finditer(ticket)))


# --- WORDING ---------------------------------------------------------------------------------------

_SOFTEN = [(re.compile(r"\bthe\s+(?:root\s+)?cause\s+(?:is|was)\b", re.I), "the most likely cause is"),
           (re.compile(r"\broot\s+cause\b", re.I), "most likely cause"),
           (re.compile(r"\b(is|was|are|were)\s+caused\s+by\b", re.I), r"\1 most likely caused by"),
           (re.compile(r"\bthe\s+(?:problem|issue|fault)\s+(?:is|was)\b", re.I), "the most likely cause is"),
           (re.compile(r"\b(?:definitely|certainly)\b", re.I), "most likely"),
           (re.compile(r"\bwe\s+have\s+(?:found|identified)\s+the\b", re.I), "we think we have found the")]
_GENERIC_CONFIRM = "Could you tell us whether anything changed on your side shortly before this started?"


# --- LABTERM ---------------------------------------------------------------------------------------

_COMMANDISH = re.compile(r"scripts/\S*|\.state/\S*|\bdocker[ -]compose\b|\b(?:fix|break|status|setup|test)\.sh\b", re.IGNORECASE)
_NAMEISH = [(re.compile(r"\b([Tt]he\s+)Keycloak's\b"), r"\1identity provider's"),
            (re.compile(r"\bKeycloak's\b"), "the identity provider's"),
            (re.compile(r"\b([Tt]he\s+)Keycloak\b"), r"\1identity provider"),
            (re.compile(r"\bKeycloak\b"), "the identity provider"),
            (re.compile(r"\bLAB_HOST\b"), "the host name"),
            (re.compile(r"\S*idlab\S*", re.I), "the host name")]
_LAB_TERM = re.compile(_COMMANDISH.pattern + r"|\bLAB_HOST\b|\bKeycloak\b|\bidlab\b", re.IGNORECASE)


def _genericise(text: str) -> str:
    was_capital = text[:1].isupper()
    for pattern, plain in _NAMEISH:
        text = pattern.sub(plain, text)
    return text[:1].upper() + text[1:] if was_capital else text


def _map_sentences(text: str, fn) -> str:
    """Apply fn to each sentence (None drops it), keeping the line structure."""
    lines = []
    for line in text.split("\n"):
        kept = [out for out in (fn(s) for s in sentences(line)) if out]
        lines.append(" ".join(kept) if line.strip() else "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# --- STEPS -----------------------------------------------------------------------------------------

_INTERROGATIVE = re.compile(r"^(?:what|which|who|whom|whose|when|where|why|how|does|do|did|is|are|was|were|can|could|should|would|"
                            r"will|has|have|had)\b", re.IGNORECASE)


def is_question(step: str) -> bool:
    step = step.strip()
    if re.match(r"^(?:do not|don't)\b", step, re.IGNORECASE):
        return False
    return step.endswith("?") or (bool(_INTERROGATIVE.match(step)) and "?" in step)


# --- the checks ------------------------------------------------------------------------------------

def _customer_facing(answer: Answer) -> list[tuple[str, str]]:
    return [("the customer reply", answer.customer_reply),
            *((f"question {i}", q) for i, q in enumerate(answer.questions_for_customer, 1))]


def content_violations(answer: Answer, ctx: Context) -> list[str]:
    """Rule breaks in the content of an answer. Empty means it complies."""
    if answer.kind == "service_request":
        return []
    out: list[str] = []
    for where, text in _customer_facing(answer):
        for bad in secret_requests(text):
            out.append(f'{SECRETS} {where} says "{bad[:90]}", which asks for a secret. Never ask for a password, shared secret, '
                       "key, token or cookie: ask them to check or compare it, or to confirm they have changed it")
    if ctx.persona == "vendor" and ctx.customer_side:
        for sentence in sentences(answer.customer_reply):
            if _PROMISE.search(sentence):
                out.append(f'{PERSONA} the ticket describes {ctx.customer_side[0]!r} as the customer\'s own, but the reply says '
                           f'"{sentence[:90]}". Do not promise to change or check anything on their side: write "please check" '
                           'or "we recommend"')
    if _needs_hedging(answer, ctx):
        problems = []
        if _ASSERTS.search(answer.customer_reply):
            problems.append('it states the cause as fact ("the cause is", "the issue is", "caused by")')
        if "most likely" not in answer.customer_reply.lower():
            problems.append('it does not say "most likely"')
        if "?" not in answer.customer_reply:
            problems.append("it asks no confirming question")
        if problems:
            out.append(f'{WORDING} confidence is {answer.confidence}, so the reply must say "the most likely cause is ..." and end '
                       "with one question that would confirm it, but " + "; ".join(problems) + '. Only high confidence may say "the cause is"')
    for where, text in [("the customer reply", answer.customer_reply), *((f"fix step {i}", s) for i, s in enumerate(answer.fix_steps, 1)),
                        *((f"question {i}", q) for i, q in enumerate(answer.questions_for_customer, 1))]:
        term = _LAB_TERM.search(text)
        if term:
            out.append(f'{LABTERM} {where} mentions "{term.group(0)}", which is specific to the lab. Use generic wording '
                       '(for example "the identity provider", "the host name") and do not mention scripts, folders or docker commands')
    for i, step in enumerate(answer.fix_steps, 1):
        if is_question(step):
            out.append(f'{STEPS} fix step {i} is a question ("{step[:80]}"). Fix steps are actions the engineer takes; '
                       "put questions in questions_for_customer")
    return out


def _needs_hedging(answer: Answer, ctx: Context) -> bool:
    """A diagnosis below high confidence must be worded as a most likely cause."""
    return ctx.mode == "diagnose" and answer.confidence != "high" and answer.outcome not in ("needs_more_info", "not_a_lab_fault")


# --- the repairs -----------------------------------------------------------------------------------

def repair(answer: Answer, ctx: Context) -> list[str]:
    """Make the answer comply, by code. Returns what was changed; each change is also added to the warnings."""
    if answer.kind == "service_request":
        return []
    changes: list[str] = []

    def note(text: str) -> None:
        changes.append(text)
        answer.warnings.append("Corrected by code: " + text)

    # SECRETS: take the asking sentences out
    for field_name in ("customer_reply", "questions_for_customer"):
        value = getattr(answer, field_name)
        if field_name == "customer_reply":
            bad = secret_requests(value)
            if bad:
                answer.customer_reply = _map_sentences(value, lambda sentence: None if sentence in bad else sentence)
                note(f"removed {len(bad)} sentence(s) from the reply that asked for a secret")
        else:
            kept = [q for q in value if not secret_requests(q)]
            if len(kept) != len(value):
                answer.questions_for_customer = kept
                note(f"removed {len(value) - len(kept)} question(s) that asked for a secret")

    # PERSONA: "we will update" becomes "we recommend that you update"
    if ctx.persona == "vendor" and ctx.customer_side and _PROMISE.search(answer.customer_reply):
        def soften(match: re.Match[str]) -> str:
            return "We recommend that you " if match.group(0)[:1].isupper() else "we recommend that you "
        answer.customer_reply = _PROMISE.sub(soften, answer.customer_reply)
        note('changed "we will ..." to "we recommend that you ..." for something on the customer\'s side')

    # STEPS: a question is moved to the customer questions
    actions, moved = [], []
    for step in answer.fix_steps:
        (moved if is_question(step) else actions).append(step)
    if moved:
        answer.fix_steps = actions
        answer.questions_for_customer += [q for q in moved if q not in answer.questions_for_customer]
        note(f"moved {len(moved)} fix step(s) that were questions to the customer questions")

    # LABTERM: drop commands and paths, rename the rest
    def clean(text: str) -> str | None:
        return None if _COMMANDISH.search(text) else _genericise(text)

    if _LAB_TERM.search(answer.customer_reply):
        answer.customer_reply = _map_sentences(answer.customer_reply, clean)
        note("removed lab-specific wording from the reply")
    if any(_LAB_TERM.search(q) for q in answer.questions_for_customer):
        answer.questions_for_customer = [q for q in map(clean, answer.questions_for_customer) if q]
        note("removed lab-specific wording from the customer questions")
    if any(_LAB_TERM.search(s) for s in answer.fix_steps):
        answer.fix_steps = [s for s in map(clean, answer.fix_steps) if s] or ["Follow the Fix section of the matching runbook."]
        note("removed lab-specific commands and names from the fix steps")

    # WORDING: soften certainty, then make sure there is a confirming question
    if _needs_hedging(answer, ctx):
        text = answer.customer_reply
        for pattern, plain in _SOFTEN:
            text = pattern.sub(plain, text)
        if "most likely" not in text.lower():
            text = (text.rstrip() + " This is the most likely explanation so far, and we would like to confirm it.").strip()
        if "?" not in text:
            question = next((q for q in answer.questions_for_customer if "`" not in q), "") or ctx.confirming or _GENERIC_CONFIRM
            text = text.rstrip() + " To confirm: " + question.strip().rstrip(".") + ("" if question.strip().endswith("?") else "?")
        if text != answer.customer_reply:
            answer.customer_reply = text
            note("reworded the reply as a most likely cause and added a confirming question")
    return changes
