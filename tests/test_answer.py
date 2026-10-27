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
    assert answer.dropped_evidence == ["event=made_up_line"]


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


def test_dropped_evidence_is_shown_to_the_engineer_with_its_count_and_text():
    answer, _ = check(raw(evidence=[{"line": LOGS, "why": "ok"}, {"line": "event=made_up_line", "why": "x"},
                                    {"line": "another invented line " + "z" * 400, "why": "y"}]), ctx())
    text = render(answer)
    section = text.split("### For the engineer")[1]
    assert "2 quoted lines were not found word for word" in section and "Treat them as unverified" in section
    assert "`event=made_up_line`" in section and "z" * 100 in section and "..." in section  # long ones are cut
    one, _ = check(raw(evidence=[{"line": LOGS, "why": "ok"}, {"line": "event=made_up_line", "why": "x"}]), ctx())
    assert "1 quoted line was not found" in render(one) and "Treat it as unverified" in render(one)
    clean, _ = check(raw(), ctx())
    assert "For the engineer" not in render(clean)


CONTRADICTORY = raw(outcome="not_a_lab_fault")


def test_not_a_lab_fault_with_a_cause_and_evidence_for_a_strong_runbook_is_a_contradiction():
    _, violations = check(CONTRADICTORY, ctx())
    assert len(violations) == 1 and violations[0].startswith("CONTRADICTION:") and "runbook 15" in violations[0]


def test_the_contradiction_needs_no_cause_and_no_evidence_only_a_strong_match_and_no_user_side_hint():
    """With no logs there is no evidence to find; t12 of the real-ticket set was missed for that reason."""
    for change in ({"evidence": []}, {"likely_cause": ""}, {"evidence": [], "likely_cause": ""}):
        _, violations = check({**CONTRADICTORY, **change}, ctx())
        assert len(violations) == 1 and violations[0].startswith("CONTRADICTION:"), change


def test_naming_a_scenario_is_not_a_contradiction():
    assert check({**CONTRADICTORY, "outcome": "redirect-loop"}, ctx())[1] == []


def test_the_contradiction_check_stands_down_when_no_runbook_matched_strongly_or_a_user_side_hint_applies():
    weak = {15: ("runbooks/15-redirect-loop.md", "Redirect loop", False)}
    assert check(CONTRADICTORY, ctx(retrieved=weak))[1] == []
    assert check(CONTRADICTORY, ctx(user_side_hint=True))[1] == []


# --- high confidence without log lines: three conditions, all needed -------------------------------------

DOC = "Could not validate timestamp: expired. Check system clock."
CLOCK = {"clock-skew": "runbooks/02-clock-skew.md", "redirect-loop": "runbooks/15-redirect-loop.md"}


def high(**kw):
    base = dict(mode="diagnose", logs="", ticket=f"The portal shows: {DOC} It started today.", scenario_runbooks=CLOCK,
                retrieved={2: ("runbooks/02-clock-skew.md", "Clock skew", True), 15: ("runbooks/15-redirect-loop.md", "Redirect loop", True)},
                documented={2: (DOC,), 15: ("This page isn't working here",)})
    base.update(kw)
    return Context(**base)


def test_high_confidence_without_logs_is_kept_when_all_three_conditions_hold():
    answer, _ = check(raw(outcome="clock-skew", evidence=[], confidence="high"), high())
    assert answer.confidence == "high" and any("documented error text word for word" in w for w in answer.warnings)


@pytest.mark.parametrize("name,ctx_change,raw_change", [
    ("the runbook did not match strongly", dict(retrieved={2: ("runbooks/02-clock-skew.md", "Clock skew", False)}), {}),
    ("the ticket does not quote the documented text", dict(ticket="The portal says something about the time."), {}),
    ("the ticket quotes only part of it", dict(ticket="It says: Could not validate timestamp"), {}),
    ("the scenario belongs to a different runbook", {}, dict(outcome="redirect-loop")),
    ("the runbook has no documented text that is unique to it", dict(documented={2: ()}), {}),
])
def test_without_all_three_conditions_it_stays_capped_at_medium(name, ctx_change, raw_change):
    answer, _ = check(raw(**{"outcome": "clock-skew", "evidence": [], "confidence": "high", **raw_change}), high(**ctx_change))
    assert answer.confidence in ("medium", "low"), name
    assert answer.confidence != "high" and not any("documented error text" in w for w in answer.warnings), name


def test_the_quote_may_differ_in_case_and_spacing_but_not_in_words():
    answer, _ = check(raw(outcome="clock-skew", evidence=[], confidence="high"),
                      high(ticket="could not VALIDATE   timestamp: expired.\nCheck system clock."))
    assert answer.confidence == "high"


def test_verified_log_evidence_still_gives_high_without_any_documented_text():
    answer, _ = check(raw(), ctx())
    assert answer.confidence == "high"


def test_the_customers_own_words_do_not_count_as_evidence_for_high_confidence():
    """Run 2 of the real-ticket set found tickets reaching high by quoting themselves."""
    c = ctx(logs="", ticket="The portal goes round in circles. Chrome says it redirected me too many times.")
    quoting_the_ticket = raw(evidence=[{"line": "Chrome says it redirected me too many times.", "why": "the customer's words"}])
    answer, _ = check(quoting_the_ticket, c)
    assert answer.evidence[0].source == "ticket" and answer.confidence == "medium"
    assert any("No quoted log line could be verified" in w for w in answer.warnings)
    with_log, _ = check(raw(), ctx())  # a verified log line still gives high
    assert with_log.evidence[0].source == "log" and with_log.confidence == "high"


def test_a_ticket_quote_plus_the_documented_text_rule_can_still_give_high():
    answer, _ = check(raw(outcome="clock-skew", evidence=[{"line": DOC, "why": "customer quotes the error"}], confidence="high"), high())
    assert answer.confidence == "high" and answer.evidence[0].source == "ticket"


# --- quotes and whitespace in quoted evidence --------------------------------------------------------

@pytest.mark.parametrize("quoted", [
    "They get a page saying 'We are sorry... Invalid parameter: redirect_uri'.",          # double became single
    "They get a page saying “We are sorry... Invalid parameter: redirect_uri”.",  # curly double
    "They get a page saying ‘We are sorry... Invalid parameter: redirect_uri’.",  # curly single
    "They get   a page\nsaying \"We are sorry...   Invalid parameter: redirect_uri\".",     # runs of whitespace
])
def test_quote_marks_and_whitespace_do_not_stop_a_quote_verifying(quoted):
    ticket = 'They get a page saying "We are sorry... Invalid parameter: redirect_uri".'
    answer, _ = check(raw(evidence=[{"line": quoted, "why": "x"}]), ctx(ticket=ticket, logs=""))
    assert [e.source for e in answer.evidence] == ["ticket"] and answer.dropped_evidence == []


def test_different_words_still_fail_to_verify():
    answer, _ = check(raw(evidence=[{"line": "They get a page saying 'Invalid parameter: client_id'.", "why": "x"}]),
                      ctx(ticket='They get a page saying "Invalid parameter: redirect_uri".', logs=""))
    assert answer.evidence == [] and len(answer.dropped_evidence) == 1


# --- the cause line must describe the diagnosed runbook --------------------------------------------------

from triage.answer import cause_agrees  # noqa: E402


@pytest.fixture(scope="module")
def causes():
    from triage.config import load_config
    from triage.runbooks import load_runbooks

    lab = load_config(environ={}).lab_dir
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    return {n: b.cause_statement + "\n" + b.title for n, b in load_runbooks(lab).items()}


def test_each_runbooks_own_cause_agrees_with_it_and_with_no_other(causes):
    for n, text in causes.items():
        statement = text.split("\n")[0]
        assert cause_agrees(statement, n, causes), n
        accepted_elsewhere = [m for m in causes if m != n and cause_agrees(statement, m, causes)]
        assert accepted_elsewhere == [], (n, accepted_elsewhere)


def test_the_t02_cause_line_is_caught_and_a_fair_paraphrase_is_not(causes):
    leaked = "The sign-in service no longer trusts the certificate your server presents."
    assert not cause_agrees(leaked, 3, causes)
    assert cause_agrees("The HR portal's redirect URI in the OpenID Connect configuration does not match the new URL.", 3, causes)
    assert cause_agrees("The application's address moved, but the IdP client still only allows the old redirect URI.", 3, causes)
    assert not cause_agrees("", 3, causes) and not cause_agrees("Something is misconfigured.", 3, causes)


def test_a_wrong_cause_line_is_a_violation_and_cannot_carry_high_confidence(causes):
    c = ctx(causes=causes, scenario_runbooks={"redirect-uri-mismatch": "runbooks/03-redirect-uri-mismatch.md"},
            retrieved={3: ("runbooks/03-redirect-uri-mismatch.md", "Redirect URI mismatch", True)})
    bad = raw(outcome="redirect-uri-mismatch", likely_cause="The sign-in service no longer trusts the certificate your server presents.",
              evidence=[], confidence="high")
    answer, violations = check(bad, c)
    assert any(v.startswith("CAUSE:") and "runbook 03" in v for v in violations)
    assert answer.confidence == "medium"
    from triage.answer import repair_cause

    repair_cause(answer, c)
    assert answer.likely_cause == causes[3].split("\n")[0] and answer.confidence == "medium"
    assert any("runbook's own cause statement" in w for w in answer.warnings)


def test_a_right_cause_line_passes_and_keeps_high_confidence(causes):
    c = high(causes=causes)
    answer, violations = check(raw(outcome="clock-skew", evidence=[], confidence="high", likely_cause=causes[2].split("\n")[0]), c)
    assert violations == [] and answer.confidence == "high"
    wrong, v2 = check(raw(outcome="clock-skew", evidence=[], confidence="high", likely_cause=causes[13].split("\n")[0]), c)
    assert wrong.confidence == "medium" and any(v.startswith("CAUSE:") for v in v2)  # documented text alone is not enough
