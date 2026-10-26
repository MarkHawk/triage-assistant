import pytest

from triage.answer import AnswerError, Context, check, clarify_violations, output_schema, render

SCENARIOS = {"redirect-loop": "runbooks/15-redirect-loop.md", "clock-skew": "runbooks/02-clock-skew.md"}
LOGS = "2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-"
RETRIEVED = {15: ("runbooks/15-redirect-loop.md", "Redirect loop", True), 2: ("runbooks/02-clock-skew.md", "Clock skew", False)}


def ctx(mode="diagnose", **kw):
    base = dict(mode=mode, logs=LOGS, ticket="The portal goes round in circles", scenario_runbooks=SCENARIOS,
                retrieved=RETRIEVED, user_side_hint=False, ticket_was_flagged=False)
    base.update(kw)
    return Context(**base)


def raw(**kw):
    base = {"outcome": "redirect-loop", "confidence": "high", "likely_cause": "The proxy dropped X-Forwarded-Proto.",
            "evidence": [{"line": LOGS, "why": "the app sees plain HTTP"}], "fix_steps": ["Restore the header."],
            "customer_reply": "We are looking into it.", "questions_for_customer": [], "possibilities": []}
    base.update(kw)
    return base


def test_schema_lists_every_scenario_and_the_two_special_outcomes():
    schema = output_schema(list(SCENARIOS))
    assert schema["properties"]["outcome"]["enum"] == ["clock-skew", "redirect-loop", "not_a_lab_fault", "needs_more_info"]
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False


def test_good_diagnosis_passes_and_derives_the_runbook():
    answer, violations = check(raw(), ctx())
    assert not violations and answer.runbook == "runbooks/15-redirect-loop.md"
    assert answer.confidence == "high" and answer.evidence[0].source == "log"


def test_model_cannot_choose_the_runbook_path():
    answer, _ = check(raw(runbook="runbooks/99-made-up.md"), ctx())
    assert answer.runbook == "runbooks/15-redirect-loop.md"


def test_invented_evidence_is_dropped_and_confidence_capped():
    answer, _ = check(raw(evidence=[{"line": "event=made_up_line", "why": "x"}]), ctx())
    assert answer.evidence == [] and answer.confidence == "medium"
    assert any("dropped" in w for w in answer.warnings)


def test_evidence_may_quote_the_ticket():
    answer, _ = check(raw(evidence=[{"line": "goes round in circles", "why": "customer wording"}]), ctx())
    assert answer.evidence[0].source == "ticket"


def test_whitespace_differences_in_a_quoted_line_are_tolerated():
    spaced = LOGS.replace(" ", "  ")
    answer, _ = check(raw(evidence=[{"line": spaced, "why": "x"}]), ctx())
    assert len(answer.evidence) == 1


def test_weak_runbook_caps_high_confidence():
    answer, _ = check(raw(outcome="clock-skew"), ctx())
    assert answer.confidence == "medium"


def test_runbook_that_was_not_retrieved_is_low_confidence():
    answer, _ = check(raw(outcome="redirect-loop"), ctx(retrieved={}))
    assert answer.confidence == "low" and answer.warnings


@pytest.mark.parametrize("bad", [{"outcome": "invented"}, {"confidence": "certain"}])
def test_unusable_output_raises(bad):
    with pytest.raises(AnswerError):
        check(raw(**bad), ctx())
    with pytest.raises(AnswerError):
        check("not a dict", ctx())


def test_em_dashes_are_removed():
    answer, _ = check(raw(customer_reply="We are on it " + chr(0x2014) + " thank you."), ctx())
    assert chr(0x2014) not in answer.customer_reply


def test_reply_gets_secrets_advice_when_the_ticket_was_flagged():
    answer, _ = check(raw(), ctx(ticket_was_flagged=True))
    assert "do not send passwords" in answer.customer_reply.lower()
    again, _ = check(raw(customer_reply="Please never send a password or token."), ctx(ticket_was_flagged=True))
    assert again.customer_reply.count("password") == 1  # not repeated if it already says so
    mention, _ = check(raw(customer_reply="Is the cookie banner showing?"), ctx(ticket_was_flagged=True))
    assert "do not send passwords" in mention.customer_reply.lower()  # merely mentioning a cookie is not advice


CLARIFY_OK = {"outcome": "needs_more_info", "confidence": "low", "likely_cause": "", "evidence": [], "fix_steps": [],
              "customer_reply": "Could you tell us which address you use?", "questions_for_customer": ["Which address?"],
              "possibilities": [{"runbook_number": 15, "if_this": "the page never loads at all"}]}


def test_clarify_answer_that_follows_the_rules_has_no_violations():
    answer, violations = check(CLARIFY_OK, ctx("clarify"))
    assert violations == [] and answer.possibilities[0].runbook_number == 15


@pytest.mark.parametrize("change,expected", [
    ({"likely_cause": "The proxy is misconfigured."}, "likely_cause"),
    ({"fix_steps": ["Restart nginx."]}, "fix_steps"),
    ({"confidence": "medium"}, "confidence"),
    ({"outcome": "redirect-loop"}, "outcome"),
    ({"questions_for_customer": []}, "question"),
    ({"customer_reply": "The cause is the proxy."}, "cause"),
    ({"customer_reply": "This was caused by a certificate."}, "cause"),
    ({"customer_reply": "It is definitely the proxy."}, "cause"),
])
def test_clarify_rule_breaks_are_caught(change, expected):
    _, violations = check({**CLARIFY_OK, **change}, ctx("clarify"))
    assert any(expected in v for v in violations), violations


def test_not_a_lab_fault_is_allowed_in_clarify_mode_only_with_a_user_side_hint():
    r = {**CLARIFY_OK, "outcome": "not_a_lab_fault"}
    assert check(r, ctx("clarify"))[1]
    assert check(r, ctx("clarify", user_side_hint=True))[1] == []


def test_possibility_for_an_unretrieved_runbook_is_dropped():
    r = {**CLARIFY_OK, "possibilities": [{"runbook_number": 7, "if_this": "x"}]}
    answer, _ = check(r, ctx("clarify"))
    assert answer.possibilities == [] and answer.warnings


def test_render_shows_conditions_and_no_cause_in_clarify_mode():
    answer, _ = check(CLARIFY_OK, ctx("clarify"))
    text = render(answer, ["WARNING: something"])
    assert "No cause is asserted" in text and "If the page never loads at all" in text
    assert "### Likely cause" not in text and "### Fix steps" not in text and text.startswith("> WARNING")


def test_render_diagnosis_has_cause_evidence_runbook_and_steps():
    text = render(check(raw(), ctx())[0])
    for part in ("### Likely cause", "### Evidence", "### Runbook", "### Fix steps", "### Draft reply"):
        assert part in text


def test_placeholders_in_the_reply_are_removed_and_reported():
    reply = "We are on it.\n\nBest regards,\n[Your Name]"
    for mode, base in (("diagnose", raw(customer_reply=reply)), ("clarify", {**CLARIFY_OK, "customer_reply": reply})):
        answer, violations = check(base, ctx(mode))
        assert "[Your Name]" not in answer.customer_reply and answer.customer_reply.endswith("Best regards,")
        assert any("placeholder" in w for w in answer.warnings) and not any("placeholder" in v for v in violations)
    kept, _ = check(raw(customer_reply="Your token shows as [MASKED:jwt]."), ctx())
    assert "[MASKED:jwt]" in kept.customer_reply  # the guard's own markers are not placeholders


def test_possibilities_are_dropped_in_diagnose_mode():
    answer, _ = check(raw(possibilities=[{"runbook_number": 15, "if_this": "the page loops"}]), ctx())
    assert answer.possibilities == [] and any("clarify mode only" in w for w in answer.warnings)


def test_a_possibility_written_as_a_finding_is_named_in_the_violation():
    bad = {**CLARIFY_OK, "possibilities": [{"runbook_number": 15, "if_this": "The issue is related to a redirect loop."}]}
    _, violations = check(bad, ctx("clarify"))
    assert len(violations) == 1
    assert "runbook 15" in violations[0] and "The issue is" in violations[0] and 'after the word "If"' in violations[0]
