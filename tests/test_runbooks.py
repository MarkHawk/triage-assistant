import pytest

from triage.config import load_config
from triage.runbooks import load_runbooks, parse_runbook

SAMPLE = """# 07 · Reverse proxy: 504 Gateway Time-out

| | |
|---|---|
| **Blast radius** | Every login. |
| **Typical trigger** | A slow app. |

## Symptom

The browser shows 504.

How customers describe it: *"login hangs and then errors"*.

```
## not a heading
```

## Fix

1. Raise the timeout.

## Questions I'd ask the customer

1. How long does it wait?
"""


def test_parse_sample(tmp_path):
    (tmp_path / "runbooks").mkdir()
    f = tmp_path / "runbooks" / "07-x.md"
    f.write_text(SAMPLE)
    book = parse_runbook(f, tmp_path)
    assert book.number == 7 and book.title.startswith("Reverse proxy")
    assert book.path == "runbooks/07-x.md"
    assert book.meta["Typical trigger"] == "A slow app."
    assert set(book.sections) == {"Symptom", "Fix", "Questions I'd ask the customer"}
    assert "## not a heading" in book.sections["Symptom"]
    assert "login hangs" in book.customer_phrases


def test_excerpt_fits_and_keeps_fences_balanced(tmp_path):
    (tmp_path / "runbooks").mkdir()
    f = tmp_path / "runbooks" / "07-x.md"
    f.write_text(SAMPLE.replace("The browser shows 504.", "```\n" + "line\n" * 400 + "```"))
    book = parse_runbook(f, tmp_path)
    out = book.excerpt(1500)
    assert len(out) <= 1600
    assert out.count("```") % 2 == 0
    assert "Raise the timeout" in out


@pytest.fixture(scope="module")
def lab_runbooks():
    lab = load_config(environ={}).lab_dir
    if not (lab / "runbooks").is_dir():
        pytest.skip("identity-lab not found at LAB_DIR")
    return load_runbooks(lab)


def test_every_real_runbook_has_the_expected_shape(lab_runbooks):
    assert len(lab_runbooks) >= 19
    for book in lab_runbooks.values():
        for name in ("Symptom", "Diagnosis", "Fix", "Questions I'd ask the customer"):
            assert book.sections.get(name), f"{book.path}: no {name}"
        assert book.customer_phrases, f"{book.path}: no 'How customers describe it' line"


@pytest.mark.parametrize("limit", [3000, 6000])
def test_real_excerpts_fit_and_keep_the_fix(lab_runbooks, limit):
    for book in lab_runbooks.values():
        out = book.excerpt(limit)
        assert len(out) <= limit + 200, f"{book.path} excerpt is {len(out)} chars"
        assert out.count("```") % 2 == 0, book.path
        assert "## Fix" in out and "## Symptom" in out
        assert book.path in out
