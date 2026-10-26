"""The end-to-end behaviour: modes, the required clarify cases, and the checks around a model."""

import json

import pytest
import yaml

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
    lab = load_config(environ={}).lab_dir
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
        clarify += 1
        assert_no_cause_asserted(triage(world, row["q"]))
    assert clarify >= 6


def test_the_reply_to_staff_has_no_commands_or_header_names(world):
    reply = triage(world, QUERY_15).answer.customer_reply
    assert "curl" not in reply and "`" not in reply and "X-Forwarded" not in reply
    assert chr(0x2014) not in reply


def test_user_side_hint_gets_a_question_to_check_it(world):
    result = triage(world, "I forgot my password and the reset email never arrives")
    assert_no_cause_asserted(result)
    assert "forgotten or mistyped password" in result.answer.questions_for_customer[0]


# --- strong matches: a cause may be stated -----------------------------------------------------

DIAGNOSIS = {"outcome": "redirect-loop", "confidence": "high",
             "likely_cause": "The proxy stopped sending X-Forwarded-Proto to the edge app, so it keeps redirecting to HTTPS.",
             "evidence": [{"line": LOOP_LOG, "why": "scheme=http with no forwarded protocol"}],
             "fix_steps": ["Repeat the forwarded headers in the location, or remove the extra proxy_set_header."],
             "customer_reply": "We have found the problem and are fixing it.", "questions_for_customer": [], "possibilities": []}


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


def test_secret_in_logs_is_a_hard_refusal_and_nothing_is_sent(world):
    backend = Scripted(world["guard"], [CLARIFY_GOOD])
    with pytest.raises(SecretFound):
        triage(world, QUERY_15, logs=f"INFO something token={JWT}", backend=backend)
    assert backend.prompts == []


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
