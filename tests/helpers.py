"""Shared bits for tests that stand in for a model."""

from functools import lru_cache

from triage.config import load_config
from triage.runbooks import load_runbooks
from triage.scenarios import load_scenarios


@lru_cache(maxsize=None)
def runbook_cause(scenario: str) -> str:
    """The diagnosed runbook's own one-line cause, as a well-behaved model would paraphrase it."""
    lab = load_config(environ={}).lab_dir
    number = load_scenarios(lab)[scenario].runbook_number
    return load_runbooks(lab)[number].cause_statement
