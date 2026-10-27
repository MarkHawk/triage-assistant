"""The end-to-end behaviour: modes, the required clarify cases, and the checks around a model."""

import json

import pytest
import yaml
from helpers import lab_dir, runbook_cause

from triage.answer import clarify_violations, Context
from triage.backends.base import Backend, BackendError
from triage.backends.demo import DemoBackend
from triage.cli import build_retriever
from triage.config import REPO_ROOT, load_config
from triage.guard import Guard, SecretFound
from triage.names import load_names
from triage.pipeline import decide_mode, run_triage
from triage.prompt import CLAUDE_BUDGET, load_system
from triage.scenarios import load_scenarios

COLD = yaml.safe_load((REPO_ROOT / "tests" / "cold_queries.yaml").read_text())["cold"]
BY_START = {row["q"][:25]: row["q"] for row in COLD}
COLD_MISS = "Every single person got kicked out of SSO at about nine this morning and nobody touched anything"
PRINTER = "The printer on the second floor shows as offline"
LAPTOP_FAN = "My laptop fan has been really loud since the update"
QUERY_15 = "Edge says the site has redirected me too many times, even in a private window"
LOOP_LOG = "2026-10-22 08:44:57,240 INFO oidc-rp-edge event=force_https path=/ scheme=http x_forwarded_proto=-"


@pytest.fixture(scope="module")
def world():
    lab = lab_dir()
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    company, systems = load_names(lab)
    return {"retriever": build_retriever(lab), "scenarios": load_scenarios(lab), "guard": Guard(),
            "system_text": load_system(company, systems), "lab": lab}


class Scripted(Backend):
    """A model stand-in that returns prepared replies and remembers what it was sent."""
    name, model = "scripted", "test"

    def __init__(self, guard, replies):
        super().__init__(guard, CLAUDE_BUDGET)
        self.replies, self.prompts = list(replies), []

    def _complete(self, prompt, schema):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def triage(world, ticket, logs="", backend=None):
    backend = backend or DemoBackend(world["guard"], CLAUDE_BUDGET)
    return run_triage(ticket, logs, backend=backend, retriever=world["retriever"], scenarios=world["scenarios"],
                      guard=world["guard"], system_text=world["system_text"])


def assert_no_cause_asserted(result):
    a = result.answer
    assert result.mode == "clarify" and a.mode == "clarify"
    assert a.outcome == "needs_more_info" and a.confidence == "low"
    assert a.likely_cause == "" and a.fix_steps == [] and a.evidence == [] and a.runbook is None
    assert a.questions_for_customer, "a clarify answer must ask something"
    assert a.customer_reply and "caused by" not in a.customer_reply.lower() and "the cause" not in a.customer_reply.lower()


# --- the cases you named, on the demo backend ------------------------------------------------

@pytest.mark.parametrize("ticket", [COLD_MISS, PRINTER, LAPTOP_FAN], ids=["cold-miss", "printer", "laptop-fan"])
def test_weak_or_unrelated_tickets_get_a_clarify_answer_with_no_cause(world, ticket):
    result = triage(world, ticket)
    assert_no_cause_asserted(result)
    assert result.answer.source == "offline"


def test_cold_miss_offers_the_weak_runbook_only_as_a_condition(world):
    result = triage(world, COLD_MISS)
    assert result.retrieval.matches and not any(m.strong for m in result.retrieval.matches)
    assert result.answer.possibilities and all(p.if_this for p in result.answer.possibilities)
    text = __import__("triage.answer", fromlist=["render"]).render(result.answer)
    assert "only if the answers confirm them" in text and "### Likely cause" not in text


def test_query_15_weak_but_correct_asks_a_question_that_confirms_the_redirect_loop(world):
    result = triage(world, QUERY_15)
    assert_no_cause_asserted(result)
    assert [m.runbook.number for m in result.retrieval.matches] == [15] and not result.retrieval.matches[0].strong
    assert [p.runbook_number for p in result.answer.possibilities] == [15]
    confirming = [q for q in result.answer.questions_for_customer if "Location" in q and "same URL" in q]
    assert confirming, result.answer.questions_for_customer  # curl -I shows a redirect to the URL just asked for


def test_every_cold_query_that_is_clarify_mode_asserts_nothing(world):
    clarify = 0
    for row in COLD:
        if decide_mode(world["retriever"].search(row["q"])) == "diagnose":
            continue  # a strong match needs a model (or a recording); this test is about the rest
        if triage(world, row["q"]).mode == "request":
            continue  # an account request is answered by code, with no questions (tested separately)
        clarify += 1
        assert_no_cause_asserted(triage(world, row["q"]))
    assert clarify >= 6


def test_the_reply_to_staff_has_no_commands_or_header_names(world):
    reply = triage(world, QUERY_15).answer.customer_reply
    assert "curl" not in reply and "`" not in reply and "X-Forwarded" not in reply
    assert chr(0x2014) not in reply


def test_a_password_reset_request_is_a_service_request_not_a_diagnosis(world):
    result = triage(world, "I forgot my password and the reset email never arrives")
    assert result.mode == "request" and result.answer.kind == "service_request" and result.answer.source == "offline"
    assert result.answer.outcome == "not_a_lab_fault" and not result.answer.asserts_cause
    assert result.answer.questions_for_customer == [] and "account administrator" in result.answer.customer_reply


# --- strong matches: a cause may be stated -----------------------------------------------------

DIAGNOSIS = {"outcome": "redirect-loop", "confidence": "high",
             "likely_cause": runbook_cause("redirect-loop"),
             "evidence": [{"line": LOOP_LOG, "why": "scheme=http with no forwarded protocol"}],
             "fix_steps": ["Repeat the forwarded headers in the location, or remove the extra proxy_set_header."],
             "customer_reply": "The most likely cause is a change to the proxy headers. Did anything change on the proxy recently?", "questions_for_customer": [], "possibilities": []}


def test_strong_match_runs_in_diagnose_mode_and_may_state_a_cause(world):
    backend = Scripted(world["guard"], [DIAGNOSIS])
    result = triage(world, "The portal goes round in circles", LOOP_LOG, backend)
    assert result.mode == "diagnose"
    assert result.answer.outcome == "redirect-loop" and result.answer.asserts_cause
    assert result.answer.evidence[0].line == LOOP_LOG and result.answer.confidence == "high"
    assert result.answer.runbook.endswith("15-redirect-loop.md")
    sent = backend.prompts[0].user
    assert "Mode: DIAGNOSE" in sent and 'match="strong"' in sent and LOOP_LOG in sent


def test_a_model_that_invents_evidence_is_corrected_not_trusted(world):
    lie = {**DIAGNOSIS, "evidence": [{"line": "event=never_logged", "why": "x"}]}
    result = triage(world, "The portal goes round in circles", LOOP_LOG, Scripted(world["guard"], [lie]))
    assert result.answer.evidence == [] and result.answer.confidence == "medium" and result.answer.warnings


# --- clarify mode with a real model: retry, then fall back to code ------------------------------

CLARIFY_BAD = {"outcome": "redirect-loop", "confidence": "high", "likely_cause": "It is the proxy.", "evidence": [],
               "fix_steps": ["Restart it."], "customer_reply": "The cause is the proxy.", "questions_for_customer": [],
               "possibilities": []}
CLARIFY_GOOD = {"outcome": "needs_more_info", "confidence": "low", "likely_cause": "", "evidence": [], "fix_steps": [],
                "customer_reply": "Which address do you use, please?", "questions_for_customer": ["Which address do you use?"],
                "possibilities": [{"runbook_number": 15, "if_this": "the page keeps reloading itself"}]}


def test_clarify_prompt_forbids_a_cause_and_a_good_reply_is_accepted(world):
    backend = Scripted(world["guard"], [CLARIFY_GOOD])
    result = triage(world, QUERY_15, backend=backend)
    assert "Mode: CLARIFY" in backend.prompts[0].user and "Do NOT state or imply a cause" in backend.prompts[0].user
    assert result.answer.source == "model" and result.answer.questions_for_customer == ["Which address do you use?"]


def test_a_clarify_reply_that_asserts_a_cause_is_retried_once_with_the_reasons(world):
    backend = Scripted(world["guard"], [CLARIFY_BAD, CLARIFY_GOOD])
    result = triage(world, QUERY_15, backend=backend)
    assert len(backend.prompts) == 2
    assert "broke these rules" in backend.prompts[1].user and "likely_cause must be empty" in backend.prompts[1].user
    assert result.answer.source == "model" and not result.answer.asserts_cause


def test_a_model_that_keeps_asserting_is_replaced_by_the_code_built_answer(world):
    result = triage(world, QUERY_15, backend=Scripted(world["guard"], [CLARIFY_BAD, CLARIFY_BAD]))
    assert_no_cause_asserted(result)
    assert result.answer.source == "offline" and "broke the clarify rules twice" in result.answer.warnings[0]


def test_unusable_diagnosis_twice_is_an_error(world):
    bad = {**DIAGNOSIS, "outcome": "invented"}
    with pytest.raises(BackendError):
        triage(world, "The portal goes round in circles", LOOP_LOG, Scripted(world["guard"], [bad, bad]))


# --- secrets ---------------------------------------------------------------------------------

JWT = "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJ" + "zdWIiOiJ0ZXN0In0" + "." + "c2lnbmF0dXJl"


def test_secret_in_a_ticket_is_masked_flagged_and_triage_continues(world):
    backend = Scripted(world["guard"], [CLARIFY_GOOD])
    result = triage(world, f"Edge says redirected too many times. My token is {JWT}", backend=backend)
    assert any("WARNING" in f and "masked" in f for f in result.flags)
    assert JWT not in backend.prompts[0].user and "[MASKED:jwt]" in backend.prompts[0].user
    assert "Note from the system" in backend.prompts[0].user
    assert "do not send passwords" in result.answer.customer_reply.lower()


def test_offline_answer_to_a_ticket_with_a_secret_also_advises(world):
    result = triage(world, f"{PRINTER}. Here is my cookie: Cookie: KEYCLOAK_SESSION=abcdef123456")
    assert any("WARNING" in f for f in result.flags) and "do not send passwords" in result.answer.customer_reply.lower()


def test_secret_in_logs_is_masked_flagged_and_triage_continues_by_default(world):
    backend = Scripted(world["guard"], [CLARIFY_GOOD])
    logs = f"{LOOP_LOG}\n2026-10-22 08:44:58,000 INFO app token={JWT}"
    result = triage(world, QUERY_15, logs=logs, backend=backend)
    assert any("log lines contained secret values" in f for f in result.flags)
    sent = backend.prompts[0].user
    assert JWT not in sent and "[MASKED:" in sent and LOOP_LOG in sent
    assert "advise them" not in sent  # the customer-reply advice is only for secrets in the ticket
    assert "do not send passwords" not in result.answer.customer_reply.lower()


def test_evidence_is_checked_against_the_masked_logs_the_model_saw(world):
    # token=<jwt> is masked as a jwt first; either way the model only ever saw a masked line
    logs = f"{LOOP_LOG}\n2026-10-22 08:44:58,000 INFO app token={JWT}"
    quoted = {**DIAGNOSIS, "evidence": [{"line": LOOP_LOG, "why": "x"}]}
    result = triage(world, "The portal goes round in circles", logs, Scripted(world["guard"], [quoted]))
    assert result.answer.evidence and JWT not in result.answer.evidence[0].line
    leaked = {**DIAGNOSIS, "evidence": [{"line": f"2026-10-22 08:44:58,000 INFO app token={JWT}", "why": "x"}]}
    result = triage(world, "The portal goes round in circles", logs, Scripted(world["guard"], [leaked]))
    assert result.answer.evidence == []  # the unmasked line is not in what the model was given, so it is dropped


def test_secret_in_logs_can_still_be_a_hard_refusal(world):
    backend = Scripted(world["guard"], [CLARIFY_GOOD])
    with pytest.raises(SecretFound):
        run_triage(QUERY_15, f"INFO something token={JWT}", backend=backend, retriever=world["retriever"],
                   scenarios=world["scenarios"], guard=world["guard"], system_text=world["system_text"], log_secrets="refuse")
    assert backend.prompts == []


@pytest.mark.parametrize("mode", ["mask", "refuse"])
def test_lab_env_value_in_logs_is_a_hard_refusal_in_both_modes(world, mode):
    guard = Guard({"lab-secret-value-12345"})
    backend = Scripted(guard, [CLARIFY_GOOD])
    with pytest.raises(SecretFound):
        run_triage(QUERY_15, "INFO leaked lab-secret-value-12345", backend=backend, retriever=world["retriever"],
                   scenarios=world["scenarios"], guard=guard, system_text=world["system_text"], log_secrets=mode)
    assert backend.prompts == []


def test_attempts_and_fallback_are_reported(world):
    first = triage(world, QUERY_15, backend=Scripted(world["guard"], [CLARIFY_GOOD]))
    assert first.attempts == 1 and not first.code_fallback
    retried = triage(world, QUERY_15, backend=Scripted(world["guard"], [CLARIFY_BAD, CLARIFY_GOOD]))
    assert retried.attempts == 2 and not retried.code_fallback
    fell = triage(world, QUERY_15, backend=Scripted(world["guard"], [CLARIFY_BAD, CLARIFY_BAD]))
    assert fell.attempts == 2 and fell.code_fallback


def test_lab_env_value_in_a_ticket_is_a_hard_refusal(world):
    guard = Guard({"lab-secret-value-12345"})
    backend = Scripted(guard, [CLARIFY_GOOD])
    with pytest.raises(SecretFound):
        run_triage("my password is lab-secret-value-12345", "", backend=backend, retriever=world["retriever"],
                   scenarios=world["scenarios"], guard=guard, system_text=world["system_text"])
    assert backend.prompts == []


def test_demo_with_nothing_recorded_for_a_strong_match_says_how_to_record(world):
    with pytest.raises(BackendError, match="--record"):
        triage(world, "Users see: Could not validate timestamp: expired. Check system clock.")


def test_mode_is_decided_from_strength_not_from_having_matches(world):
    assert decide_mode(world["retriever"].search(QUERY_15)) == "clarify"
    assert decide_mode(world["retriever"].search("Users see: Could not validate timestamp: expired. Check system clock.")) == "diagnose"
    assert decide_mode(world["retriever"].search(PRINTER)) == "clarify"


def test_clarify_mode_replays_a_recorded_model_answer_when_demo_has_one(world, tmp_path):
    demo = DemoBackend(world["guard"], CLAUDE_BUDGET, directory=tmp_path)
    first = triage(world, QUERY_15, backend=demo)
    assert first.answer.source == "offline"  # nothing recorded yet: built by code
    recorded = {**CLARIFY_GOOD, "customer_reply": "Which address do you use? We will look into it."}
    live = Scripted(world["guard"], [recorded])
    result = triage(world, QUERY_15, backend=live)
    demo.record(result.prompt, result.raw, "scripted", "test")
    replayed = triage(world, QUERY_15, backend=demo)
    assert replayed.answer.source == "model" and replayed.answer.customer_reply.startswith("Which address do you use?")
    assert replayed.answer.questions_for_customer == ["Which address do you use?"]


def test_a_quote_from_the_tidied_logs_the_model_saw_is_accepted(world):
    docker_line = ("oidc-rp-edge-1  | 2026-10-26T22:12:44.141514796Z 2026-10-26 22:12:44,141 INFO oidc-rp "
                   "event=force_https path=/ scheme=http x_forwarded_proto=-")
    seen_by_the_model = "oidc-rp-edge-1  | 2026-10-26 22:12:44,141 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-"
    quoted = {**DIAGNOSIS, "evidence": [{"line": seen_by_the_model, "why": "the app sees plain HTTP"}]}
    result = triage(world, "The portal goes round in circles", docker_line, Scripted(world["guard"], [quoted]))
    assert [e.line for e in result.answer.evidence] == [seen_by_the_model]
    invented = {**DIAGNOSIS, "evidence": [{"line": "oidc-rp-edge-1  | 2026-10-26 22:12:44,141 INFO oidc-rp event=nothing", "why": "x"}]}
    assert triage(world, "The portal goes round in circles", docker_line, Scripted(world["guard"], [invented])).answer.evidence == []


CONTRADICTION = {**DIAGNOSIS, "outcome": "not_a_lab_fault"}


def test_a_self_contradicting_answer_is_sent_back_once_and_the_fixed_one_is_used(world):
    backend = Scripted(world["guard"], [CONTRADICTION, DIAGNOSIS])
    result = triage(world, "The portal goes round in circles", LOOP_LOG, backend)
    assert result.attempts == 2 and result.answer.outcome == "redirect-loop" and result.answer.confidence == "high"
    assert "CONTRADICTION" in backend.prompts[1].user and "runbook 15" in backend.prompts[1].user
    assert not any("not a lab fault" in f for f in result.flags)


def test_a_contradiction_that_survives_the_retry_is_kept_but_capped_and_flagged(world):
    backend = Scripted(world["guard"], [CONTRADICTION, CONTRADICTION])
    result = triage(world, "The portal goes round in circles", LOOP_LOG, backend)
    assert result.attempts == 2 and not result.code_fallback
    assert result.answer.outcome == "not_a_lab_fault" and result.answer.confidence == "low"
    assert any("says not a lab fault" in f and "capped at low" in f for f in result.flags)
    assert result.answer.warnings[0].startswith("The answer contradicts itself")
    from triage.answer import render
    assert render(result.answer, result.flags).startswith("> WARNING: this answer says not a lab fault")


# --- the post-review rules, end to end ---------------------------------------------------------------

WIFI = ("We replaced our old wireless controller on Monday. Staff can't join the staff SSID, the controller just says the RADIUS "
        "server timed out. Guest wifi is fine.")
WIFI_BAD = {**DIAGNOSIS, "outcome": "radius-shared-secret-mismatch", "evidence": [], "confidence": "medium",
            "likely_cause": runbook_cause("radius-shared-secret-mismatch"),
            "customer_reply": "We will update the shared secret. Please confirm the current shared secret and we will proceed.",
            "fix_steps": ["Which NAS is failing?", "Run scripts/fix.sh radius-shared-secret-mismatch"]}
WIFI_GOOD = {**WIFI_BAD, "customer_reply": "The most likely cause is that the shared secret on your controller no longer matches. "
             "Please check it on both sides. Was the controller reset or restored from a backup?",
             "fix_steps": ["Compare the shared secret on both sides."]}


def test_a_reply_that_asks_for_a_secret_is_sent_back_with_the_reasons_and_the_good_one_is_used(world):
    backend = Scripted(world["guard"], [WIFI_BAD, WIFI_GOOD])
    result = triage(world, WIFI, backend=backend)
    assert result.attempts == 2 and result.answer.warnings == [] and not any("corrected" in f for f in result.flags)
    fed_back = backend.prompts[1].user
    for reason in ("asks for a secret", "the ticket calls the customer's", "fix step 1 is a question", "specific to the lab"):
        assert reason in fed_back, reason


def test_if_the_model_breaks_the_reply_rules_twice_code_corrects_it_and_says_so(world):
    from triage import rules
    from triage.answer import Context

    result = triage(world, WIFI, backend=Scripted(world["guard"], [WIFI_BAD, WIFI_BAD]))
    a = result.answer
    assert result.attempts == 2 and not result.code_fallback
    assert rules.secret_requests(a.customer_reply) == [] and "We will update" not in a.customer_reply
    assert "We recommend that you update the shared secret" in a.customer_reply
    assert a.fix_steps == ["Follow the Fix section of the matching runbook."] and "Which NAS is failing?" in a.questions_for_customer
    assert a.customer_reply.lower().count("most likely") >= 1 and "?" in a.customer_reply
    assert any(f.startswith("WARNING: the model broke the reply rules twice") for f in result.flags)
    assert any(w.startswith("Corrected by code") for w in a.warnings)


def test_the_corrected_answer_passes_every_rule(world):
    from triage import rules
    from triage.answer import Context

    result = triage(world, WIFI, backend=Scripted(world["guard"], [WIFI_BAD, WIFI_BAD]))
    ctx = Context(mode="diagnose", logs="", ticket=WIFI, scenario_runbooks={}, retrieved={}, persona="vendor")
    assert rules.content_violations(result.answer, ctx) == []


def test_an_internal_persona_may_promise_work_on_the_customers_side(world):
    reply = {**WIFI_GOOD, "customer_reply": "We will update the shared secret on the controller. Was the controller replaced recently?"}
    # most likely is still required below high confidence, so word it that way; the persona is the point here
    reply["customer_reply"] = "The most likely cause is the shared secret. We will update it on the controller. Was it replaced recently?"
    vendor = triage(world, WIFI, backend=Scripted(world["guard"], [reply, reply]))
    assert "We will update" not in vendor.answer.customer_reply and vendor.attempts == 2
    internal = run_triage(WIFI, "", backend=Scripted(world["guard"], [reply]), retriever=world["retriever"], scenarios=world["scenarios"],
                          guard=world["guard"], system_text=world["system_text"], persona="internal")
    assert internal.attempts == 1 and "We will update it on the controller" in internal.answer.customer_reply


def test_an_account_request_is_answered_by_code_and_the_model_is_never_asked(world):
    backend = Scripted(world["guard"], [])
    result = triage(world, "I typed my password wrong too many times and now I'm locked out. Can you unlock me please?", backend=backend)
    assert backend.prompts == [] and result.mode == "request" and result.answer.kind == "service_request"
    assert "unlock the account" in result.answer.customer_reply and result.answer.questions_for_customer == []


def test_a_request_word_does_not_hide_a_real_fault_that_matched_strongly(world):
    ticket = "Since the proxy change everyone gets 502 Bad Gateway at sign-in, and one person is locked out as well."
    backend = Scripted(world["guard"], [{**DIAGNOSIS, "outcome": "proxy-bad-gateway", "evidence": [], "confidence": "medium",
                                         "likely_cause": runbook_cause("proxy-bad-gateway")}] * 2)
    result = triage(world, ticket, backend=backend)
    assert result.mode == "diagnose" and len(backend.prompts) >= 1


CLOCK_ERROR = "Users see: Could not validate timestamp: expired. Check system clock."
CLOCK_REPLY = {**DIAGNOSIS, "outcome": "clock-skew", "evidence": [], "confidence": "high", "likely_cause": runbook_cause("clock-skew"),
               "customer_reply": "The cause is a clock difference on the application server. We recommend checking its time."}


def test_high_confidence_without_logs_when_the_ticket_quotes_the_documented_error_text(world):
    result = triage(world, CLOCK_ERROR, backend=Scripted(world["guard"], [CLOCK_REPLY]))
    assert result.mode == "diagnose" and result.attempts == 1 and result.answer.confidence == "high"
    assert any("documented error text word for word" in w for w in result.answer.warnings)


def test_without_the_documented_text_the_same_reply_is_capped_and_must_be_hedged(world):
    ticket = "The clock on our application server is wrong and the portal says the response is too old."
    result = triage(world, ticket, backend=Scripted(world["guard"], [CLOCK_REPLY, CLOCK_REPLY]))
    assert result.answer.confidence == "medium"
    assert "the cause is" not in result.answer.customer_reply.lower() and "most likely" in result.answer.customer_reply.lower()
    assert result.attempts == 2  # "The cause is ..." is not allowed below high confidence


def test_a_cause_line_copied_from_the_example_ends_up_as_the_runbooks_own_cause(world):
    from triage.prompt import DIAGNOSE_REPLY_EXAMPLE
    from triage import rules

    leaked = {**DIAGNOSIS, "likely_cause": rules.sentences(DIAGNOSE_REPLY_EXAMPLE)[1]}
    backend = Scripted(world["guard"], [leaked, leaked])
    result = triage(world, "The portal goes round in circles", LOOP_LOG, backend)
    assert "copies the worked example" in backend.prompts[1].user
    assert result.answer.likely_cause == runbook_cause("redirect-loop") and result.answer.confidence == "medium"
    assert any("cause statement is shown" in f for f in result.flags)


def test_the_prompt_says_runbook_text_is_never_evidence(world):
    assert ("Runbook text (sample logs, error pages, symptom descriptions) is reference material, never evidence; evidence must "
            "come from the ticket or the supplied logs.") in world["system_text"]
