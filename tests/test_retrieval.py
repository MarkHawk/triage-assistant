import re
from pathlib import Path

import pytest
import yaml

from triage.cli import build_retriever
from triage.config import REPO_ROOT, load_config
from triage.retrieval import Retriever, stem, terms, words
from triage.runbooks import parse_runbook

QUERIES = yaml.safe_load((REPO_ROOT / "tests" / "retrieval_queries.yaml").read_text())

# What the plain-language queries scored when this was written. Floors, so a change that makes
# retrieval worse fails; raise them when it improves. See the README for how they were measured.
FLOORS = {"dev": 1.0, "held-out": 1.0, "fresh": 0.9}


def test_stemmer_meets_inflections():
    assert stem("expired") == stem("expire") == stem("expires")
    assert stem("circles") == stem("circle")
    assert words("Redirected 502!") == [stem("redirected"), "502"]
    assert "the" not in terms("the portal")


@pytest.fixture(scope="module")
def retriever():
    lab = load_config(environ={}).lab_dir
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
    lab = load_config(environ={}).lab_dir
    checked = 0
    for scenario in retriever.scenarios.values():
        if not scenario.log_pattern:
            continue
        pattern = re.compile(scenario.log_pattern)
        sample = next((l for l in (lab / scenario.runbook).read_text().splitlines() if pattern.search(l)), None)
        if sample is None:
            continue
        checked += 1
        assert retriever.search("something is wrong", sample).numbers[0] == scenario.runbook_number, scenario.name
    assert checked >= 3


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
