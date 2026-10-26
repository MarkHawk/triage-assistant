"""The committed demo recordings must keep replaying for the committed fixtures, with no network."""

from pathlib import Path

import pytest

from triage.backends.demo import DemoBackend
from triage.cli import build_retriever
from triage.config import REPO_ROOT, load_config
from triage.guard import Guard
from triage.names import load_names
from triage.pipeline import run_triage
from triage.prompt import CLAUDE_BUDGET, load_system
from triage.scenarios import load_scenarios
from triage.tickets import load_ticket

FIXTURES = REPO_ROOT / "demo" / "fixtures"


@pytest.fixture(scope="module")
def world():
    lab = load_config(environ={}).lab_dir
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    company, systems = load_names(lab)
    return dict(retriever=build_retriever(lab), scenarios=load_scenarios(lab), system_text=load_system(company, systems),
                guard=Guard())


def replay(world, ticket_file, logs_file=None):
    ticket = load_ticket(FIXTURES / ticket_file)
    logs = (FIXTURES / logs_file).read_text() if logs_file else ""
    return run_triage(ticket.text, logs, backend=DemoBackend(world["guard"], CLAUDE_BUDGET), retriever=world["retriever"],
                      scenarios=world["scenarios"], guard=world["guard"], system_text=world["system_text"])


def test_redirect_loop_diagnosis_replays_from_the_recording(world):
    result = replay(world, "redirect-loop/ticket.md", "redirect-loop/logs.txt")
    assert result.mode == "diagnose" and result.answer.source == "model" and result.attempts == 1
    assert result.answer.outcome == "redirect-loop" and result.answer.runbook.endswith("15-redirect-loop.md")
    assert result.answer.evidence and all(e.source == "log" for e in result.answer.evidence), result.answer.warnings


def test_printer_clarify_answer_replays_and_asserts_nothing(world):
    result = replay(world, "printer-offline.md")
    assert result.mode == "clarify" and result.answer.source == "model"
    assert result.answer.outcome == "needs_more_info" and not result.answer.asserts_cause and result.answer.fix_steps == []


def test_recordings_say_which_model_made_them():
    """No address is stored: the metadata is the date, the backend and the model name only.
    (tests/test_repo_clean.py scans these files for IP addresses like every other tracked file.)"""
    import json

    files = list((REPO_ROOT / "demo" / "answers").glob("*.json"))
    assert files
    for path in files:
        meta = json.loads(path.read_text())["meta"]
        assert set(meta) == {"recorded", "backend", "model"} and meta["backend"] in ("claude", "ollama") and meta["model"]
