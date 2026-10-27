"""The cold set must stay exactly as it was given.

Its value is that retrieval was never tuned on it. If the file changes, this fails;
replace the hash only on purpose, and record why.
"""

import hashlib
from pathlib import Path

import yaml

FILE = Path(__file__).resolve().parent / "cold_queries.yaml"
SHA256 = "b0b3e20c5af40e8bed4c6ed11d59bf9da1600b82fcabb25acbebf8d9a61dc9b0"


def test_cold_set_is_unedited():
    assert hashlib.sha256(FILE.read_bytes()).hexdigest() == SHA256


def test_cold_set_is_well_formed():
    rows = yaml.safe_load(FILE.read_text())["cold"]
    assert len(rows) == 24
    assert sum(1 for r in rows if r["expect"]) == 19 and sum(1 for r in rows if not r["expect"]) == 5
    assert all(set(r) <= {"q", "expect", "also_ok", "tests"} for r in rows)


# --- regression floor -----------------------------------------------------------------
# The first (and only independent) run scored 23 of 24. A query counts as right if an
# expected runbook is in the top three (also_ok runbooks count too), or, for queries that
# should match nothing, if nothing strong is returned. Weak matches are allowed there.
# If retrieval is ever tuned against this set it stops being independent evidence: say so
# in the README and stop quoting the score as held-out.
import pytest  # noqa: E402

from triage.cli import build_retriever  # noqa: E402
from triage.config import load_config  # noqa: E402
from helpers import lab_dir

FLOOR = 23


def cold_correct(retriever):
    rows = yaml.safe_load(FILE.read_text())["cold"]
    right, missed = 0, []
    for row in rows:
        result = retriever.search(row["q"])
        got = [m.runbook.number for m in result.matches]
        want = {int(x) for x in row["expect"]} | {int(x) for x in row.get("also_ok", [])}
        ok = (not any(m.strong for m in result.matches)) if not row["expect"] else bool(want & set(got))
        right += ok
        if not ok:
            missed.append(row["q"])
    return right, missed


def test_cold_set_regression_floor():
    lab = lab_dir()
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    right, missed = cold_correct(build_retriever(lab))
    assert right >= FLOOR, f"cold set fell to {right} of 24; missed: {missed}"
