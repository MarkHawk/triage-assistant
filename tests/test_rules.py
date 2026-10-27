"""The post-review rules: what an answer may say to the customer, each tested on both sides."""

import pytest

from triage import rules
from triage.answer import Answer, Context
from triage.rules import content_violations, repair

PATH = "runbooks/05-radius-shared-secret-mismatch.md"
RETRIEVED = {5: (PATH, "RADIUS shared secret mismatch", True)}
HEDGED = "The most likely cause is a mismatched secret. Has the controller been replaced or reset recently?"


def ctx(**kw):
    base = dict(mode="diagnose", logs="", ticket="t", scenario_runbooks={"radius-shared-secret-mismatch": PATH}, retrieved=RETRIEVED,
                persona="vendor", customer_side=(), confirming="Which controller is failing?")
    base.update(kw)
    return Context(**base)


def answer(**kw):
    base = dict(mode="diagnose", outcome="radius-shared-secret-mismatch", confidence="medium", likely_cause="A secret differs.",
                evidence=[], runbook=PATH, fix_steps=["Compare the secret on both sides."], customer_reply=HEDGED,
                questions_for_customer=["Which controller is failing?"], possibilities=[])
    base.update(kw)
    return Answer(**base)


def kinds(violations):
    return sorted({v.split(":")[0] for v in violations})


# --- SECRETS ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Please send us the shared secret.",
    "What is the current shared secret?",
    "Please confirm the current shared secret and we will proceed.",
    "Could you paste your password here?",
    "Tell us your token.",
    "Let us know the API key.",
    "Please provide the private key.",
    "What's your cookie value?",
])
def test_asking_for_a_secret_is_caught(sentence):
    assert rules.secret_requests(sentence) == [sentence]


@pytest.mark.parametrize("sentence", [
    "Please do not send passwords, tokens or cookies.",
    "You never need to share your password with us.",
    "Has the shared secret been rotated on either side?",
    "Please check that the shared secret matches on both sides.",
    "Compare the secret on the controller with the one on the server.",
    "Please confirm whether the secret was changed on Monday.",
    "Which controller is failing?",
])
def test_talking_about_a_secret_without_asking_for_it_is_fine(sentence):
    assert rules.secret_requests(sentence) == []


def test_the_t03_reply_is_a_violation_and_is_repaired_by_removing_only_the_asking_sentence():
    reply = "We believe the secret differs. We will update the shared secret. Please confirm the current shared secret and we will proceed."
    a = answer(customer_reply=reply + " " + HEDGED)
    assert "SECRETS" in kinds(content_violations(a, ctx()))
    repair(a, ctx())
    assert "confirm the current shared secret" not in a.customer_reply and "We believe the secret differs." in a.customer_reply
    assert rules.secret_requests(a.customer_reply) == []
    assert any("asked for a secret" in w for w in a.warnings)


def test_questions_that_ask_for_a_secret_are_removed_too():
    a = answer(questions_for_customer=["What is the shared secret?", "Which controller is failing?"])
    assert "SECRETS" in kinds(content_violations(a, ctx()))
    repair(a, ctx())
    assert a.questions_for_customer == ["Which controller is failing?"]


# --- PERSONA ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("ticket,found", [
    ("We replaced our old wireless controller on Monday.", True),
    ("Our app server cannot reach it.", True),
    ("Nothing has changed on our side.", True),
    ("The network team changed our proxy.", True),
    ("Postman from my laptop also gets 400.", True),
    ("Our single-page app shows Failed to fetch.", True),
    ("Nobody can open the Northgate Staff Portal.", False),
    ("The sign-in page says invalid request.", False),
])
def test_customer_side_components_are_recognised_in_the_ticket(ticket, found):
    assert bool(rules.customer_side(ticket)) is found


VENDOR_CTX = dict(customer_side=("our old wireless controller",))


def test_a_vendor_may_not_promise_to_change_something_on_the_customers_side():
    a = answer(customer_reply="We will update the shared secret on the controller. " + HEDGED)
    assert "PERSONA" in kinds(content_violations(a, ctx(**VENDOR_CTX)))
    repair(a, ctx(**VENDOR_CTX))
    assert "We recommend that you update the shared secret on the controller." in a.customer_reply
    assert "We will update" not in a.customer_reply and rules._PROMISE.search(a.customer_reply) is None


@pytest.mark.parametrize("sentence", ["We will keep you updated.", "We will update you tomorrow.", "We will look into the sign-in logs.",
                                      "Please check the secret on your controller.", "We recommend checking the secret."])
def test_ordinary_promises_and_recommendations_are_fine(sentence):
    assert "PERSONA" not in kinds(content_violations(answer(customer_reply=sentence + " " + HEDGED), ctx(**VENDOR_CTX)))


def test_the_persona_rule_applies_only_to_a_vendor_and_only_when_the_ticket_mentions_their_estate():
    a = answer(customer_reply="We will update the shared secret. " + HEDGED)
    assert "PERSONA" not in kinds(content_violations(a, ctx(persona="internal", **VENDOR_CTX)))
    assert "PERSONA" not in kinds(content_violations(a, ctx(customer_side=())))


# --- WORDING ---------------------------------------------------------------------------------------

def test_a_medium_confidence_reply_must_say_most_likely_and_ask_a_confirming_question():
    for reply, problem in [("The cause is a mismatched secret.", "states the cause as fact"),
                           ("We think the secret differs. Has it changed?", 'does not say "most likely"'),
                           ("The most likely cause is a mismatched secret.", "no confirming question")]:
        v = content_violations(answer(customer_reply=reply), ctx())
        assert kinds(v) == ["WORDING"] and problem in v[0], reply
    assert content_violations(answer(), ctx()) == []  # the hedged reply is fine


def test_only_high_confidence_may_say_the_cause_is():
    a = answer(confidence="high", customer_reply="The cause is a mismatched shared secret.")
    assert content_violations(a, ctx()) == []
    assert "WORDING" in kinds(content_violations(answer(confidence="low", customer_reply="The cause is a mismatched secret."), ctx()))


@pytest.mark.parametrize("change", [{"outcome": "needs_more_info", "runbook": None},
                                    {"outcome": "not_a_lab_fault", "runbook": None}])
def test_the_wording_rule_does_not_apply_to_questions_or_to_not_a_lab_fault(change):
    a = answer(customer_reply="The issue is on your side.", **change)
    assert "WORDING" not in kinds(content_violations(a, ctx()))
    assert "WORDING" not in kinds(content_violations(answer(customer_reply="The cause is X."), ctx(mode="clarify")))


def test_repair_rewords_the_certainty_and_adds_a_confirming_question():
    a = answer(customer_reply="The cause is a mismatched secret. We are fixing it.", questions_for_customer=[])
    repair(a, ctx())
    assert "the most likely cause is a mismatched secret" in a.customer_reply.lower() and a.customer_reply.rstrip().endswith("?")
    assert "To confirm: Which controller is failing?" in a.customer_reply
    assert content_violations(a, ctx()) == []


def test_repair_prefers_the_models_own_plain_question_then_the_runbooks_then_a_generic_one():
    a = answer(customer_reply="We are on it.", questions_for_customer=["Run `curl -I`?", "Was the controller replaced?"])
    repair(a, ctx())
    assert "To confirm: Was the controller replaced?" in a.customer_reply
    b = answer(customer_reply="We are on it.", questions_for_customer=[])
    repair(b, ctx(confirming=""))
    assert "anything changed on your side" in b.customer_reply and b.customer_reply.rstrip().endswith("?")


# --- LABTERM ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,term", [("Run scripts/fix.sh redirect-loop.", "scripts/"), ("Look in .state/proxy/kc.conf.", ".state/"),
                                       ("Set LAB_HOST correctly.", "LAB_HOST"), ("Use docker compose logs proxy.", "docker compose"),
                                       ("Restart Keycloak.", "Keycloak"), ("Open https://idlab.home:8187/.", "idlab")])
def test_lab_specific_wording_is_caught_in_replies_steps_and_questions(text, term):
    for field in ("customer_reply", "fix_steps", "questions_for_customer"):
        value = HEDGED + " " + text if field == "customer_reply" else [text]
        v = content_violations(answer(**{field: value}), ctx())
        assert "LABTERM" in kinds(v) and term in v[0], field


def test_repair_makes_the_wording_generic_and_drops_lab_commands():
    a = answer(customer_reply="Keycloak is fine. Run scripts/fix.sh now. " + HEDGED,
               fix_steps=["Restart Keycloak.", "Run scripts/fix.sh redirect-loop", "Edit .state/proxy/x.conf"],
               questions_for_customer=["Is LAB_HOST right?", "Check docker compose logs proxy"])
    repair(a, ctx())
    assert a.customer_reply.startswith("The identity provider is fine.") and "scripts/" not in a.customer_reply
    assert a.fix_steps == ["Restart the identity provider."]
    assert a.questions_for_customer == ["Is the host name right?"]
    assert content_violations(a, ctx()) == []


def test_if_every_step_was_a_lab_command_a_generic_step_remains():
    a = answer(fix_steps=["Run scripts/fix.sh x", "docker compose restart proxy"])
    repair(a, ctx())
    assert a.fix_steps == ["Follow the Fix section of the matching runbook."]


# --- STEPS -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("step,is_q", [("Which NAS is failing?", True), ("Does the log show an error?", True), ("Check the log?", True),
                                       ("Do not restart the server", False), ("Check the shared secret on both sides.", False),
                                       ("Have the customer restart the controller.", False)])
def test_a_question_is_not_an_action(step, is_q):
    assert rules.is_question(step) is is_q


def test_question_steps_are_caught_and_moved_to_the_customer_questions():
    a = answer(fix_steps=["Which NAS is failing?", "Compare the secret on both sides."], questions_for_customer=[])
    assert "STEPS" in kinds(content_violations(a, ctx()))
    repair(a, ctx())
    assert a.fix_steps == ["Compare the secret on both sides."] and a.questions_for_customer == ["Which NAS is failing?"]


def test_a_service_request_answer_is_exempt():
    a = answer(kind="service_request", customer_reply="Keycloak. Send us your password.", fix_steps=["Which?"])
    assert content_violations(a, ctx()) == [] and repair(a, ctx()) == []
