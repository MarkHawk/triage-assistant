"""Rules for what an answer may say to the customer and the engineer, and the repairs when a model breaks them.

Each rule has a check that returns violations (a model is sent them and asked again) and a repair that code
applies if the model still breaks the rule on its second attempt, so a broken answer never reaches anyone.

  SECRETS   a customer reply never asks for a password, shared secret, key, token or cookie
  PERSONA   as a vendor, "we will" for our own things ("your API" in the ticket), "please check" or "we recommend"
            for the customer's ("our server" in the ticket)
  WORDING   below high confidence the reply says "most likely" and asks a confirming question
  LABTERM   nothing specific to the lab (scripts, .state, LAB_HOST, docker compose, Keycloak)
  STEPS     fix steps are actions, not questions
  EXAMPLE   nothing is copied from the prompt's worked example

The checks that depend on the diagnosis itself (evidence, confidence caps, contradictions, the clarify rules)
are in answer.py.
"""

from __future__ import annotations

import re

from .answer import _ASSERTS, Answer, Context

SECRETS, PERSONA, WORDING, LABTERM, STEPS, EXAMPLE = "SECRETS:", "PERSONA:", "WORDING:", "LABTERM:", "STEPS:", "EXAMPLE:"
REPAIRABLE = (SECRETS, PERSONA, WORDING, LABTERM, STEPS, EXAMPLE)
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


# --- PERSONA and OWNERSHIP (vendor persona only) --------------------------------------------------
#
# Whose is a thing? In the ticket, "your API" is ours (the vendor's) and "our server" is the customer's. In the
# reply the words flip: "our RADIUS server" is ours, "your controller" is theirs. A plain "the RADIUS server" in
# the reply takes the owner the ticket gave the same thing. A vendor promises work on its own things ("we will
# check") and recommends it on the customer's ("please check", "we recommend").

OURS, THEIRS = "ours", "theirs"
_THING_NOUNS = {
    "server", "servers", "api", "apis", "idp", "page", "app", "apps", "application", "applications", "proxy", "controller",
    "network", "firewall", "certificate", "certificates", "portal", "endpoint", "service", "system", "systems", "laptop", "pc",
    "device", "devices", "integration", "gateway", "site", "website", "email", "side", "end", "router", "switch", "vpn", "dns",
    "environment", "infrastructure", "machine", "cluster", "browser", "console", "balancer"}
_DEFAULT_THEIRS = {"browser", "console", "laptop", "pc", "device", "devices"}  # the customer's unless the ticket says otherwise
_TOKEN = re.compile(r"[A-Za-z][\w'-]*|[^\w\s]")


def _word(token: str) -> str:
    return re.sub(r"'s$", "", token.lower())


def _things(text: str):
    """(index of the determiner, determiner, words) for each thing named after a determiner, such as "your RADIUS server"."""
    tokens = _TOKEN.findall(text)
    found = []
    for i, token in enumerate(tokens):
        if token.lower() not in ("your", "our", "my", "their", "the"):
            continue
        for j in range(i + 1, min(i + 5, len(tokens))):
            if not tokens[j][:1].isalpha():
                break
            if _word(tokens[j]) in _THING_NOUNS:
                name = " ".join(tokens[i + 1:j] + [re.sub(r"'s$", "", tokens[j])])
                found.append((i, token.lower(), tuple(_word(t) for t in tokens[i + 1:j + 1]), name))
                break
    return tokens, found


_GENERIC_HEADS = {"page", "side", "end", "service", "system", "systems", "site", "email"}  # need the full name to match


def ownership(ticket: str) -> dict[tuple[str, ...], str]:
    """What the ticket calls ours ("your ...") and theirs ("our ...", "my ..."), keyed by the words naming it and by
    the last word alone. A key the ticket gives both owners maps to "" (unresolved)."""
    seen: dict[tuple[str, ...], set[str]] = {}
    for _, det, words, _ in _things(ticket)[1]:
        owner = OURS if det == "your" else THEIRS if det in ("our", "my") else None
        if owner:
            for key in {words, words[-1:]}:
                seen.setdefault(key, set()).add(owner)
    return {key: (owners.pop() if len(owners) == 1 else "") for key, owners in seen.items()}


def _owner_in_reply(det: str, words: tuple[str, ...], owners: dict) -> str | None:
    if det == "our":
        return OURS
    if det in ("your", "their"):
        return THEIRS
    owner = owners.get(words)
    if owner is None and words[-1] not in _GENERIC_HEADS:
        owner = owners.get(words[-1:])
    if owner:
        return owner
    return THEIRS if words[-1] in _DEFAULT_THEIRS else None


_ASK_VERB = (r"(?:check|verify|turn|switch|restart|reload|change|update|fix|reconfigure|disable|enable|remove|rotate|renew|correct|"
             r"set|review|confirm|compare|ensure|make sure|look at)")
_FIX_VERB = r"(?:turn|switch|restart|reload|change|update|fix|reconfigure|disable|enable|remove|rotate|renew|correct|set)"
_DEFER = re.compile(rf"\b(?:please|kindly|you should|you may want to|you might want to|could you|can you|would you)\s+(?:also\s+)?{_ASK_VERB}\w*"
                    rf"|\bwe (?:recommend|suggest|advise)(?: that you)?\s+(?:also\s+)?{_ASK_VERB}\w*", re.IGNORECASE)
_DEFER_FIX = re.compile(rf"\b(?:please|kindly|you should|you may want to|you might want to|could you|can you|would you)\s+(?:also\s+)?{_FIX_VERB}\w*"
                        rf"|\bwe (?:recommend|suggest|advise)(?: that you)?\s+(?:also\s+)?{_FIX_VERB}\w*", re.IGNORECASE)
_WE_WILL = re.compile(r"\b(?:we will|we'll|we are going to|we're going to)\s+(?:\w+\s+){0,2}?"
                      r"(?:check|fix|look|investigate|correct|review|update|change|turn|switch|restart|reload|repair|verify)\w*", re.IGNORECASE)
_PROMISE = re.compile(
    r"\b(?:we will|we'll|we shall|we are going to|we're going to|we can|our team will|we plan to)\s+(?:then\s+|also\s+|now\s+)?"
    r"(?=(?:fix|change|update|reconfigure|restart|reload|adjust|correct|replace|renew|rotate|configure|apply|remove|add|set|"
    r"check|repair|modify|amend|enable|disable|re-?issue|install|upgrade|patch|reset)\b(?!\s+you\b))", re.IGNORECASE)


def _objects(sentence: str, owners: dict) -> list[tuple[str | None, str]]:
    """(owner, name) for what each "please check" or "we recommend" in the sentence is about: the first thing after
    the verb, and anything joined to it by "and" or "or" ("both the new controller and the RADIUS server")."""
    out = []
    tokens, things = _things(sentence)
    starts = {i: (det, words, name) for i, det, words, name in things}
    for match in _DEFER.finditer(sentence):
        after = len(_TOKEN.findall(sentence[:match.end()]))
        chain = False
        for i in sorted(k for k in starts if k >= after):
            between = [t.lower() for t in tokens[after:i]]
            if chain and not set(between) <= {"and", "or", "both", "either", ","}:
                break
            det, words, name = starts[i]
            out.append((_owner_in_reply(det, words, owners), name))
            chain, after = True, i + len(words) + 1
    return out


def _mentions(sentence: str, owners: dict) -> set[str]:
    return {o for _, det, words, _ in _things(sentence)[1] if (o := _owner_in_reply(det, words, owners))}


def customer_side(ticket: str) -> tuple[str, ...]:
    """The things the ticket calls the customer's own ("our server", "my laptop")."""
    return tuple(dict.fromkeys(name for _, det, _, name in _things(ticket)[1] if det in ("our", "my")))


def ownership_violations(answer: Answer, ctx: Context) -> list[str]:
    if ctx.persona != "vendor":
        return []
    owners = ownership(ctx.ticket)
    ours = {w for w, o in owners.items() if o == OURS}
    theirs = {w for w, o in owners.items() if o == THEIRS}
    out = []
    for sentence in sentences(answer.customer_reply):
        objects = _objects(sentence, owners)
        mine = [name for owner, name in objects if owner == OURS]
        if mine:
            out.append(f'{PERSONA} the reply says "{sentence[:90]}", asking the customer to act on "{mine[0]}", which the ticket '
                       'calls ours ("your ..."). Say what we will check or fix instead')
        elif _DEFER_FIX.search(sentence) and not objects_owned(objects) and ours and not theirs:
            out.append(f'{PERSONA} the reply says "{sentence[:90]}", asking the customer to fix something, but the ticket points '
                       'only at our side. Say what we will do')
        mentioned = _mentions(sentence, owners)
        if _PROMISE.search(sentence) and OURS not in mentioned and (THEIRS in mentioned or (theirs and not ours)):
            out.append(f'{PERSONA} the reply says "{sentence[:90]}", promising work on something the ticket calls the customer\'s '
                       '("our ..."). Write "please check" or "we recommend" instead')
    reply_ours = any(OURS in _mentions(s, owners) for s in sentences(answer.customer_reply))
    if ours and reply_ours and not _WE_WILL.search(answer.customer_reply):
        out.append(f"{PERSONA} the reply names something the ticket calls ours but never says what we will check or fix. "
                   'Add, for example, "we will check the ... on our side"')
    return out


def objects_owned(objects) -> bool:
    return any(owner for owner, _ in objects)


_ING = {"checking": "check", "turning": "turn", "verifying": "verify", "restarting": "restart", "reloading": "reload",
        "changing": "change", "updating": "update", "fixing": "fix", "disabling": "disable", "enabling": "enable",
        "removing": "remove", "rotating": "rotate", "renewing": "renew", "correcting": "correct", "setting": "set",
        "reviewing": "review", "confirming": "confirm", "comparing": "compare", "switching": "switch", "ensuring": "ensure",
        "reconfiguring": "reconfigure", "making": "make", "looking": "look"}


def _to_we_will(sentence: str) -> str:
    """ "Please check X" / "We recommend turning X off" / "Could you restart X?" becomes "We will check X" and so on."""
    patterns = [
        (re.compile(r"\b(?:please|kindly)\s+(?:also\s+)?(\w+)", re.I), lambda m: "we will " + m.group(1)),
        (re.compile(r"\bwe (?:recommend|suggest|advise) that you\s+", re.I), lambda m: "we will "),
        (re.compile(r"\bwe (?:recommend|suggest|advise)\s+(\w+ing)\b", re.I), lambda m: "we will " + _ING.get(m.group(1).lower(), "check")),
        (re.compile(r"\b(?:you should|you may want to|you might want to)\s+", re.I), lambda m: "we will "),
        (re.compile(r"\b(?:could|can|would) you(?: please)?\s+", re.I), lambda m: "we will "),
    ]
    for pattern, repl in patterns:
        if pattern.search(sentence):
            out = pattern.sub(repl, sentence, count=1)
            out = re.sub(r"\band (\w+ing)\b", lambda m: "and " + _ING.get(m.group(1).lower(), m.group(1)), out)
            out = out[:1].upper() + out[1:]
            return out[:-1] + "." if out.endswith("?") else out
    return "We will check this on our side."


def ownership_repair(answer: Answer, ctx: Context, note) -> None:
    if ctx.persona != "vendor":
        return
    owners = ownership(ctx.ticket)
    ours = {w for w, o in owners.items() if o == OURS}
    theirs = {w for w, o in owners.items() if o == THEIRS}
    changed = False

    def fix(sentence: str) -> str:
        nonlocal changed
        objects = _objects(sentence, owners)
        mine = [name for owner, name in objects if owner == OURS]
        yours = [name for owner, name in objects if owner == THEIRS]
        if mine and yours:
            changed = True
            return f"We will check the {mine[0]} on our side, and we recommend that you check the {yours[0]} on yours."
        if mine or (_DEFER_FIX.search(sentence) and not objects_owned(objects) and ours and not theirs):
            changed = True
            return _to_we_will(sentence)
        mentioned = _mentions(sentence, owners)
        if _PROMISE.search(sentence) and OURS not in mentioned and (THEIRS in mentioned or (theirs and not ours)):
            changed = True
            return _PROMISE.sub(lambda m: "We recommend that you " if m.group(0)[:1].isupper() else "we recommend that you ", sentence)
        return sentence

    answer.customer_reply = _map_sentences(answer.customer_reply, fix)
    if changed:
        note("changed who does what in the reply: we check or fix our own things, and recommend on the customer's")
    reply_ours = [name for s in sentences(answer.customer_reply) for _, det, words, name in _things(s)[1]
                  if _owner_in_reply(det, words, owners) == OURS]
    if ours and reply_ours and not _WE_WILL.search(answer.customer_reply):
        answer.customer_reply = (answer.customer_reply.rstrip() + f" We will check the {reply_ours[0]} on our side and let you know what we find.")
        note("added what we will check on our side")


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


# --- EXAMPLE ---------------------------------------------------------------------------------------

def _example_runs() -> set[tuple[str, ...]]:
    """Six-word runs of the example that name its subject. Generic runs ("the most likely cause is that") are not
    copying: every correctly hedged reply contains them."""
    from .answer import _shingles
    from .prompt import DIAGNOSE_REPLY_EXAMPLE

    return {run for run in _shingles(DIAGNOSE_REPLY_EXAMPLE) if any(w.startswith(("calendar", "mailbox")) for w in run)}


def copies_example(text: str) -> bool:
    """True if the text contains a six-word run from the worked example's content."""
    from .answer import _shingles

    return bool(_shingles(text) & _example_runs())


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
    out += ownership_violations(answer, ctx)
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
    for where, text in [("likely_cause", answer.likely_cause), ("the customer reply", answer.customer_reply),
                        *((f"question {i}", q) for i, q in enumerate(answer.questions_for_customer, 1)),
                        *((f"fix step {i}", s) for i, s in enumerate(answer.fix_steps, 1))]:
        if copies_example(text):
            out.append(f"{EXAMPLE} {where} copies the worked example about a shared calendar. Describe this ticket and its "
                       "runbook in your own words")
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

    # EXAMPLE: anything copied from the worked example goes
    if copies_example(answer.customer_reply):
        answer.customer_reply = _map_sentences(answer.customer_reply, lambda sentence: None if copies_example(sentence) else sentence)
        note("removed sentences copied from the prompt's worked example")
    for name in ("questions_for_customer", "fix_steps"):
        kept = [x for x in getattr(answer, name) if not copies_example(x)]
        if len(kept) != len(getattr(answer, name)):
            setattr(answer, name, kept)
            note(f"removed {name.replace('_', ' ')} copied from the prompt's worked example")
    if copies_example(answer.likely_cause):
        answer.likely_cause = ""
        note("cleared a cause line copied from the prompt's worked example")

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

    # PERSONA: we check or fix our own things, and recommend on the customer's
    ownership_repair(answer, ctx, note)

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
