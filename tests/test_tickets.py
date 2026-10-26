from triage.tickets import parse_ticket

FULL = '''---
id: "001"
channel: email
from: Priya Raman, Northgate Supplies
subject: Staff Portal goes round in circles
---
Hello,

the portal just keeps reloading.

## Log excerpt

```
2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/
```
'''


def test_full_ticket():
    t = parse_ticket(FULL)
    assert t.id == "001" and t.subject.startswith("Staff Portal")
    assert t.body == "Hello,\n\nthe portal just keeps reloading."
    assert t.log_excerpt == "2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/"
    assert "Priya" not in t.text and "email" not in t.text  # header fields other than the subject never reach a model


def test_plain_text_ticket_without_header_or_logs():
    t = parse_ticket("It says something about a redirect.", fallback_id="x")
    assert t.id == "x" and t.subject == "" and t.log_excerpt == "" and t.text == "It says something about a redirect."


def test_unfenced_log_section():
    t = parse_ticket("Broken.\n\n## Log excerpt\nline one\nline two\n")
    assert t.log_excerpt == "line one\nline two"
