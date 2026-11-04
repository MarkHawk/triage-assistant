import re
from pathlib import Path

import pytest
import yaml

from triage.cli import build_retriever
from triage.config import REPO_ROOT, load_config
from triage.retrieval import Retriever, stem, terms, words
from triage.runbooks import load_runbooks, parse_runbook
from helpers import lab_dir

QUERIES = yaml.safe_load((REPO_ROOT / "tests" / "retrieval_queries.yaml").read_text())

# What the plain-language queries scored when this was written. Floors, so a change that makes
# retrieval worse fails; raise them when it improves. See the README for how they were measured.
FLOORS = {"dev": 1.0, "held-out": 1.0, "fresh": 0.9, "mfa-dev": 1.0, "mfa-held-out": 0.7}  # mfa-held-out: 7 of 10 at the end, 6 on the first run


def test_stemmer_meets_inflections():
    assert stem("expired") == stem("expire") == stem("expires")
    assert stem("circles") == stem("circle")
    assert words("Redirected 502!") == [stem("redirected"), "502"]
    assert "the" not in terms("the portal")


@pytest.fixture(scope="module")
def retriever():
    lab = lab_dir()
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    return build_retriever(lab)


def ranked(retriever, q):
    return retriever.search(q["text"]).numbers


def rate(retriever, split, top):
    rows = [q for q in QUERIES if q["split"] == split]
    ok = 0
    for q in rows:
        got = ranked(retriever, q)
        if q["runbook"] is None:
            ok += not got
        else:
            ok += (q["runbook"] in got[:top])
    return ok / len(rows)


@pytest.mark.parametrize("split", sorted(FLOORS))
def test_plain_language_queries_find_the_runbook_in_the_top_three(retriever, split):
    assert rate(retriever, split, 3) >= FLOORS[split]


def test_the_two_wordings_from_the_brief(retriever):
    assert retriever.search("It goes round in circles").numbers[0] == 15
    assert retriever.search("It says something about a redirect").numbers[0] == 3


def test_user_side_causes_return_nothing_but_a_hint(retriever):
    result = retriever.search("I forgot my password, please reset it")
    assert result.matches == () and result.hints
    result = retriever.search("The bookmark in my favourites says page not found")
    assert result.matches == () and result.hints


def test_a_hint_does_not_hide_a_real_fault(retriever):
    result = retriever.search("I forgot my password, then saw the portal says Bad Gateway for everybody")
    assert result.numbers and result.numbers[0] in (6, 10)


def test_unrelated_ticket_returns_nothing(retriever):
    assert retriever.search("The printer on the second floor is out of toner").matches == ()


def test_matches_say_why_and_whether_they_are_strong(retriever):
    best = retriever.search("The expenses page goes round in circles").matches[0]
    assert best.strong and any("customers describe" in r for r in best.reasons)


def test_log_lines_alone_find_the_runbook(retriever):
    logs = "2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-"
    # Not in a scenario's SCENARIO_LOG: this goes through the wording of the logs.
    assert 15 in retriever.search("sign in is broken", logs).numbers


def test_lab_error_text_is_strong_evidence(retriever):
    result = retriever.search("Users see: Could not validate timestamp: expired. Check system clock.")
    assert result.numbers[0] == 2 and result.matches[0].strong


def test_scenario_log_patterns_hit_their_runbook(retriever):
    """Each SCENARIO_LOG pattern, matched against a real sample line from its own runbook."""
    lab = lab_dir()
    checked, wrong = 0, []
    for scenario in retriever.scenarios.values():
        if scenario.log_absent:
            continue  # proven by silence: there is no log line to sample
        pattern = re.compile(scenario.log_pattern)
        sample = next((l for l in (lab / scenario.runbook).read_text().splitlines() if pattern.search(l)), None)
        if sample is None:
            continue  # clock-skew: its runbook shows no line that the lab's pattern matches
        checked += 1
        result = retriever.search("something is wrong", sample)
        if not result.numbers or result.numbers[0] != scenario.runbook_number:
            wrong.append(scenario.name)
    assert checked >= 20
    assert not wrong, wrong


def test_the_event_rule_alone_finds_force_https():
    """The 'event=' rule must keep working without the scenario patterns, so it has its own test.

    The retriever here has no scenarios, so only the rule that ties a log line's event= name to
    the runbooks describing it can supply the answer. Removing the rule fails this.
    """
    lab = lab_dir()
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    bare = Retriever(load_runbooks(lab), scenarios={})
    line = "2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-"
    result = bare.search("sign in is broken", line)
    assert result.numbers[:1] == [15]
    assert any("log event 'force_https'" in r for r in result.matches[0].reasons)


def test_nothing_in_the_tickets_or_answers_folders_is_used():
    source = (REPO_ROOT / "triage" / "retrieval.py").read_text()
    assert "answers" not in source and "tickets" not in source


def test_synthetic_corpus_ranks_and_returns_nothing_for_nonsense(tmp_path):
    def book(n, title, symptom):
        (tmp_path / "runbooks").mkdir(exist_ok=True)
        f = tmp_path / "runbooks" / f"{n:02d}-x.md"
        f.write_text(f"# {n:02d} · {title}\n\n## Symptom\n\n{symptom}\n\nHow customers describe it: *\"{symptom}\"*\n\n## Fix\n\nDo it.\n")
        return n, parse_runbook(f, tmp_path)

    books = dict([book(1, "Orange juice fault", "The orange juice machine leaks pulp everywhere"),
                  book(2, "Purple widget fault", "Purple widgets refuse to rotate when heated")])
    empty = tmp_path / "phrases.yaml"
    empty.write_text("[]")
    r = Retriever(books, phrases_path=empty)
    assert r.search("my orange juice machine leaks pulp").numbers == [1]
    assert r.search("purple widgets will not rotate when heated").numbers == [2]
    assert r.search("zebra quantum staircase").matches == ()


@pytest.mark.parametrize("ticket", [
    "Subject: Printer offline\n\nThe printer on the second floor shows as offline.",
    "The scanner on the first floor shows as offline.",
    "The shared drive is offline again.",
])
def test_an_unrelated_offline_ticket_does_not_pull_in_an_mfa_runbook(retriever, ticket):
    # runbook 20 mentions a phone kept "offline"; one rare word is not an MFA fault
    assert not {20, 21, 22, 23} & set(retriever.search(ticket).numbers)


def test_a_phone_kept_offline_still_finds_the_clock_runbook(retriever):
    assert 20 in retriever.search("My authenticator codes stopped working after my phone was offline for weeks.").numbers


def test_mfa_wording_beats_its_look_alikes_in_first_place(retriever):
    first = lambda text: retriever.search(text).numbers[0]
    assert first("The VPN rejects me and never asks for a code.") == 23  # not 05, the RADIUS shared secret
    assert first("The code is always wrong. It works on my colleague's phone but not mine.") == 20  # not 02, SAML clock skew
    assert first("Locked out after trying codes, but it works again after a while.") == 22  # not a plain wrong password
    assert first("Everyone's password is rejected by the VPN, the shared secret was changed") == 5



def test_the_mfa_runbooks_own_questions_reach_clarify(retriever):
    """Clarify takes its questions from the runbooks; nothing about MFA is hard-coded."""
    from triage.clarify import questions_from
    from triage.retrieval import Match

    def asked(number, rank=0):
        return " ".join(questions_from([Match(retriever.runbooks[number], 1.0, ())] if rank == 0 else
                                        [Match(retriever.runbooks[5], 1.0, ()), Match(retriever.runbooks[number], 1.0, ())])).lower()

    assert "asked for a code" in asked(23) and "first time" in asked(21)
    assert "start working again" in asked(22) and "how many times" in asked(22)
    assert "asked for a code" in asked(23, rank=1)  # still there as a second match
    # known gap: runbook 20 puts "how many times have you tried" at question 6, past the four a top match gets
    assert "how many times" not in asked(20)
