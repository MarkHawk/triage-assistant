import pytest

from triage import service_requests as sr
from triage.rules import content_violations
from triage.answer import Context


@pytest.mark.parametrize("ticket,kind", [
    ("I typed my password wrong too many times and now I'm locked out. Can you unlock me please?", "unlock"),
    ("Please unlock Sam's account.", "unlock"),
    ("My account is locked.", "unlock"),
    ("I forgot my password, can you reset it?", "reset-password"),
    ("Can you do a password reset for the whole finance team?", "reset-password"),
    ("Can you add a new user called Priya to the finance group?", "new-account"),
    ("We have a new starter on Monday who needs an account.", "new-account"),
    ("Please put Sam into the approvers group.", "access-change"),
    ("Could you give Alex access to the reports?", "access-change"),
    ("A leaver needs their access removed.", "access-change"),
])
def test_account_requests_are_recognised(ticket, kind):
    assert sr.detect(ticket).kind == kind


@pytest.mark.parametrize("ticket", [
    "The Expenses page goes round in circles and the browser says too many redirects.",
    "SSO isn't working. Please fix ASAP.",
    "Staff can't join the wifi, the controller says the RADIUS server timed out.",
    "The printer on the second floor is offline.",
    "Our integration returns 400 Bad Request.",
])
def test_faults_and_vague_tickets_are_not_service_requests(ticket):
    assert sr.detect(ticket) is None


@pytest.mark.parametrize("persona", ["vendor", "internal"])
def test_the_answer_is_short_points_to_the_administrator_and_asks_nothing(persona):
    a = sr.build_answer(sr.detect("I'm locked out, please unlock me."), persona)
    assert a.kind == "service_request" and a.mode == "request" and a.source == "offline"
    assert a.outcome == "not_a_lab_fault" and not a.asserts_cause and a.evidence == [] and a.runbook is None
    assert a.questions_for_customer == [] and "?" not in a.customer_reply
    assert "administrator" in a.customer_reply and "unlock the account" in a.customer_reply
    assert len(a.customer_reply.split()) < 70
    assert content_violations(a, Context(mode="clarify", logs="", ticket="", scenario_runbooks={}, retrieved={})) == []


def test_the_persona_changes_the_wording():
    req = sr.detect("Please reset my password.")
    assert "your account administrator" in sr.build_answer(req, "vendor").customer_reply
    assert "The account administrators" in sr.build_answer(req, "internal").customer_reply
