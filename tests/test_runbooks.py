import pytest

from triage.config import load_config
from triage.runbooks import load_runbooks, parse_runbook
from helpers import lab_dir

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
    lab = lab_dir()
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


def test_documented_lines_keep_only_what_tells_one_runbook_from_another(tmp_path):
    from triage.runbooks import documented_lines

    (tmp_path / "runbooks").mkdir()

    def book(n, symptom):
        f = tmp_path / "runbooks" / f"{n:02d}-x.md"
        f.write_text(f"# {n:02d} · T\n\n## Symptom\n\n{symptom}\n\n## Fix\n\nf\n")
        return n, parse_runbook(f, tmp_path)

    books = dict([book(1, "```\nCould not validate timestamp: expired.\n502 Bad Gateway page here\nshort\n$ curl -I https://example.test/x\n```"),
                  book(2, "```\nA different distinctive error line\n502 Bad Gateway page here\n```")])
    lines = documented_lines(books)
    assert lines[1] == ("Could not validate timestamp: expired.",)  # unique, long enough, not a command
    assert lines[2] == ("A different distinctive error line",)  # the line both runbooks show is dropped from both


def test_a_line_found_inside_another_runbooks_longer_line_does_not_count(tmp_path):
    from triage.runbooks import documented_lines

    (tmp_path / "runbooks").mkdir()

    def book(n, symptom):
        f = tmp_path / "runbooks" / f"{n:02d}-x.md"
        f.write_text(f"# {n:02d} · T\n\n## Symptom\n\n{symptom}\n\n## Fix\n\nf\n")
        return n, parse_runbook(f, tmp_path)

    books = dict([book(6, "```\n<h1>502 Bad Gateway</h1>\n```"), book(10, "```\n502 Bad Gateway\n```")])
    assert documented_lines(books)[10] == ()  # "502 Bad Gateway" is part of runbook 06's page as well


def test_the_real_runbooks_document_error_text_but_not_the_ambiguous_lines(lab_runbooks):
    from triage.runbooks import documented_lines

    lines = documented_lines(lab_runbooks)
    assert "Could not validate timestamp: expired. Check system clock." in lines[2]
    assert sum(1 for v in lines.values() if v) >= 15
    shared = {l.lower() for v in lines.values() for l in v}
    assert "502 bad gateway" not in shared  # runbooks 06 and 10 both show it, so it names neither
