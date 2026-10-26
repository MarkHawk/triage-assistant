import pytest
import yaml

from evals import real_tickets as rt
from triage.backends.base import Backend, BackendError
from triage.cli import build_retriever
from triage.config import load_config
from triage.guard import Guard
from triage.names import load_names
from triage.pipeline import run_triage
from triage.prompt import CLAUDE_BUDGET, load_system
from triage.scenarios import load_scenarios

GOOD = {"tickets": [{"id": "a1", "text": "It keeps reloading.", "expect": {"runbook": 15}}]}


def write(tmp_path, data):
    f = tmp_path / "t.yaml"
    f.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return f


def test_the_template_loads_and_shows_both_kinds_of_expectation():
    tickets = rt.load_real_tickets(rt.TEMPLATE_FILE)
    assert [t.runbooks for t in tickets] == [(15,), ()]
    assert tickets[0].mode == "diagnose" and tickets[0].mode_was_given
    assert tickets[1].mode == "clarify" and not tickets[1].mode_was_given  # the default rule


def test_runbook_may_be_a_number_a_list_or_null(tmp_path):
    data = {"tickets": [{"id": "a", "text": "x", "expect": {"runbook": 6}}, {"id": "b", "text": "x", "expect": {"runbook": [6, 10]}},
                        {"id": "c", "text": "x", "expect": {"runbook": None, "mode": "diagnose"}}]}
    a, b, c = rt.load_real_tickets(write(tmp_path, data))
    assert a.runbooks == (6,) and b.runbooks == (6, 10) and c.runbooks == () and c.mode == "diagnose"


@pytest.mark.parametrize("data,message", [
    ("not: a list", "top-level 'tickets:'"),
    ("tickets: []", "top-level 'tickets:'"),
    ("tickets: [one]", "must be a mapping"),
    ({"tickets": [{"text": "x", "expect": {"runbook": 1}}]}, "missing id"),
    ({"tickets": [{"id": "a", "expect": {"runbook": 1}}]}, "missing text"),
    ({"tickets": [{"id": "a", "text": "x"}]}, "expect must be a mapping"),
    ({"tickets": [{"id": "a", "text": "x", "expect": {"mode": "diagnose"}}]}, "'runbook' key"),
    ({"tickets": [{"id": "a", "text": "x", "expect": {"runbook": "fifteen"}}]}, "number, a list of numbers, or null"),
    ({"tickets": [{"id": "a", "text": "x", "expect": {"runbook": []}}]}, "number, a list of numbers, or null"),
    ({"tickets": [{"id": "a", "text": "x", "expect": {"runbook": 1, "mode": "maybe"}}]}, "diagnose or clarify"),
    ({"tickets": [{"id": "a", "text": "x", "expect": {"runbook": 1}}, {"id": "a", "text": "y", "expect": {"runbook": 2}}]}, "duplicate id"),
    ("tickets: [unclosed", "not valid YAML"),
])
def test_bad_ticket_files_get_a_clear_error(tmp_path, data, message):
    with pytest.raises(rt.TicketFileError, match=message):
        rt.load_real_tickets(write(tmp_path, data))


def test_a_missing_file_next_to_the_template_points_at_the_template():
    with pytest.raises(rt.TicketFileError, match="Copy real_tickets.template.yaml to"):
        rt.load_real_tickets(rt.TEMPLATE_FILE.parent / "does_not_exist.yaml")


def test_the_real_file_is_not_created_for_you():
    """tests/real_tickets.yaml is yours to write; nothing in the repository supplies it."""
    assert rt.DEFAULT_FILE.name == "real_tickets.yaml" and rt.DEFAULT_FILE != rt.TEMPLATE_FILE


# --- scoring and the report, with a stand-in model ---------------------------------------------

class Scripted(Backend):
    name, model = "scripted", "test-model"

    def __init__(self, guard, replies):
        super().__init__(guard, CLAUDE_BUDGET)
        self.replies = list(replies)

    def _complete(self, prompt, schema):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(scope="module")
def world():
    lab = load_config(environ={}).lab_dir
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    company, systems = load_names(lab)
    return dict(retriever=build_retriever(lab), scenarios=load_scenarios(lab), guard=Guard(), system_text=load_system(company, systems))


def diagnosis(outcome, **kw):
    base = {"outcome": outcome, "confidence": "high", "likely_cause": "The proxy dropped the header.", "evidence": [],
            "fix_steps": ["Restore it."], "customer_reply": "We are on it.", "questions_for_customer": [], "possibilities": []}
    return {**base, **kw}


CLARIFY = {"outcome": "needs_more_info", "confidence": "low", "likely_cause": "", "evidence": [], "fix_steps": [],
           "customer_reply": "Which address do you use?", "questions_for_customer": ["Which address?"],
           "possibilities": [{"runbook_number": 15, "if_this": "the page keeps reloading"}]}

LOOPS = "The Expenses page goes round in circles and the browser says redirected too many times."
PRINTER = "The printer on the second floor shows as offline."


def run(world, tickets, replies):
    backend = Scripted(world["guard"], replies)
    return rt.run_all(tickets, backend=backend, retriever=world["retriever"], scenarios=world["scenarios"],
                      guard=world["guard"], system_text=world["system_text"])


def ticket(tid, text, runbook, mode=None):
    return rt.RealTicket(tid, text, tuple(runbook) if isinstance(runbook, list) else ((runbook,) if runbook else ()),
                         mode or ("diagnose" if runbook else "clarify"), mode is not None)


def test_a_right_diagnosis_scores_right_everywhere(world):
    (row,) = run(world, [ticket("t", LOOPS, 15)], [diagnosis("redirect-loop")])
    assert row.retrieval_right and row.mode == "diagnose" and row.mode_right and row.cause == "right" and not row.error


def test_a_wrong_scenario_is_a_wrong_cause_but_retrieval_can_still_be_right(world):
    (row,) = run(world, [ticket("t", LOOPS, 15)], [diagnosis("clock-skew")])
    assert row.retrieval_right and row.cause == "wrong"


def test_either_scenario_of_a_shared_runbook_counts(world):
    t = ticket("t", "I can sign in but the API says 401 unauthorised when it loads my claims.", 16)
    for outcome in ("api-bearer-audience", "api-bearer-expired"):
        (row,) = run(world, [t], [diagnosis(outcome)])
        assert row.cause == "right", outcome


def test_a_clarify_answer_asserts_no_cause_and_notes_whether_the_expected_runbook_was_listed(world):
    t = ticket("t", "Edge says the site has redirected me too many times, even in a private window", 15, "clarify")
    (row,) = run(world, [t], [CLARIFY])
    assert row.mode == "clarify" and row.mode_right and row.cause == "n/a" and row.possibilities == [15] and row.retrieval_right


def test_expected_mode_defaults_to_the_rule_and_a_mismatch_is_reported(world):
    (row,) = run(world, [ticket("t", "Edge says the site has redirected me too many times, even in a private window", 15)], [CLARIFY])
    assert row.mode == "clarify" and not row.mode_right  # a runbook was expected, so diagnose was expected by default


def test_no_runbook_expected_means_nothing_may_match_strongly(world):
    (row,) = run(world, [ticket("p", PRINTER, None)], [CLARIFY])
    assert row.retrieval_right and row.mode_right and row.cause == "n/a"
    (wrong,) = run(world, [ticket("l", LOOPS, None)], [diagnosis("redirect-loop")])
    assert not wrong.retrieval_right and wrong.cause == "wrong"
    (fine,) = run(world, [ticket("n", LOOPS, None, "diagnose")], [diagnosis("not_a_lab_fault", likely_cause="")])
    assert fine.cause == "right"


def test_a_backend_error_is_one_error_row_and_the_run_carries_on(world):
    rows = run(world, [ticket("a", LOOPS, 15), ticket("b", LOOPS, 15)], [BackendError("the server went away"), diagnosis("redirect-loop")])
    assert rows[0].error == "the server went away" and rows[1].cause == "right"


def report_for(world):
    rows = run(world, [ticket("t01", LOOPS, 15), ticket("t02", PRINTER, None)], [diagnosis("redirect-loop"), CLARIFY])
    meta = dict(date="2026-10-27 09:00 UTC", backend="ollama", model="qwen2.5:14b", num_ctx=12288, file="real_tickets.yaml",
                sha256="ab" * 32, lab_commit="a142c96")
    return rt.render_report(rows, meta)


def test_the_report_has_a_blank_verdict_column_the_summary_and_every_answer(world):
    text = report_for(world)
    assert "Not for tuning" in text and "SHA-256 abababababab" in text and "window 12288" in text
    assert "- Retrieval right: 2 of 2" in text and "- Mode appropriate: 2 of 2" in text and "- Cause right: 1 of 1 diagnosed" in text
    table = [l for l in text.splitlines() if l.startswith("| t0")]
    assert len(table) == 2 and all(l.endswith("| |") for l in table)  # the last cell is empty
    assert rt.VERDICT_HEADER in text and "### t01" in text and "### t02" in text
    assert "Draft reply to the customer" in text and "> The printer on the second floor" in text


def test_summarise_counts_the_verdicts_you_filled_in(world, tmp_path):
    lines = report_for(world).splitlines()
    rows = [i for i, l in enumerate(lines) if l.startswith("| t0")]
    lines[rows[0]] = lines[rows[0]][:-1] + " yes |"
    lines[rows[1]] = lines[rows[1]][:-1] + " With Edits |"
    f = tmp_path / "r.md"
    f.write_text("\n".join(lines))
    out = rt.summarise(f)
    assert "yes: 1, with edits: 1, no: 0, not yet filled in: 0" in out and "usable as sent or with edits: 2 of 2" in out


def test_summarise_reports_blank_and_unrecognised_verdicts(world, tmp_path):
    lines = report_for(world).splitlines()
    rows = [i for i, l in enumerate(lines) if l.startswith("| t0")]
    lines[rows[1]] = lines[rows[1]][:-1] + " maybe |"
    f = tmp_path / "r.md"
    f.write_text("\n".join(lines))
    out = rt.summarise(f)
    assert "not yet filled in: 1" in out and "not understood" in out and "t02: 'maybe'" in out


def test_summarise_refuses_a_file_without_the_results_table(tmp_path):
    f = tmp_path / "e.md"
    f.write_text("# nothing here\n")
    with pytest.raises(rt.TicketFileError, match="no results table"):
        rt.summarise(f)


def test_command_line_on_the_demo_backend_writes_a_report(world, tmp_path, capsys):
    f = write(tmp_path, {"tickets": [{"id": "p", "text": PRINTER, "expect": {"runbook": None}}]})
    assert rt.main(["--file", str(f), "--backend", "demo", "--out", str(tmp_path / "out")]) == 0
    (report,) = (tmp_path / "out").glob("real-*.md")
    assert "Retrieval right: 1 of 1" in report.read_text() and list((tmp_path / "out").glob("real-*.json"))


def test_command_line_errors_are_clear(world, tmp_path, capsys):
    assert rt.main(["--file", str(tmp_path / "nope.yaml"), "--backend", "demo"]) == 2
    assert "does not exist" in capsys.readouterr().err
    f = write(tmp_path, {"tickets": [{"id": "a", "text": "x", "expect": {"runbook": 99}}]})
    assert rt.main(["--file", str(f), "--backend", "demo"]) == 2
    assert "runbook 99 does not exist" in capsys.readouterr().err
