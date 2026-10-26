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
    assert len(rows) == 23
    assert sum(1 for r in rows if r["expect"]) == 18 and sum(1 for r in rows if not r["expect"]) == 5
    assert all(set(r) <= {"q", "expect", "also_ok", "tests"} for r in rows)
