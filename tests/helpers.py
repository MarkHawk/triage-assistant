"""Shared bits for the tests."""

import os
from functools import lru_cache
from pathlib import Path

from triage.config import load_config
from triage.runbooks import load_runbooks
from triage.scenarios import load_scenarios


def lab_dir() -> Path:
    """Where identity-lab is: LAB_DIR from the environment if set, else .env, else ../identity-lab.
    Nothing else from the environment is used, so a developer's BACKEND or keys cannot change a test."""
    env = {"LAB_DIR": os.environ["LAB_DIR"]} if os.environ.get("LAB_DIR") else {}
    return load_config(environ=env).lab_dir


@lru_cache(maxsize=None)
def runbook_cause(scenario: str) -> str:
    """The diagnosed runbook's own one-line cause, as a well-behaved model would paraphrase it.
    Without the lab it is empty; the tests that use it are skipped then."""
    lab = lab_dir()
    if not (lab / "scenarios").is_dir():
        return ""
    number = load_scenarios(lab)[scenario].runbook_number
    return load_runbooks(lab)[number].cause_statement
