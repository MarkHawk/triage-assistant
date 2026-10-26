import pytest

from triage.cli import main

JWT = "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJ" + "zdWIiOiJ0ZXN0In0" + "." + "c2lnbmF0dXJl"


@pytest.fixture(autouse=True)
def demo_backend(monkeypatch):
    monkeypatch.setenv("BACKEND", "demo")


def test_ticket_text_prints_a_clarify_answer(capsys):
    assert main(["ticket", "--text", "The printer on the second floor shows as offline"]) == 0
    out = capsys.readouterr().out
    assert "## Needs more information" in out and "No cause is asserted" in out and "### Draft reply" in out


def test_ticket_file_with_header_and_log_section(tmp_path, capsys):
    f = tmp_path / "001.md"
    f.write_text("---\nid: '001'\nfrom: Somebody Fictional\nsubject: Question about the site\n---\nEdge says the site has redirected me too many times, even in a private window.\n")
    assert main(["ticket", str(f)]) == 0
    assert "runbook 15" in capsys.readouterr().out


def test_secret_in_logs_exits_3_and_never_prints_it(tmp_path, capsys):
    logs = tmp_path / "logs.txt"
    logs.write_text(f"INFO login ok token={JWT}\n")
    assert main(["ticket", "--text", "It loops", "--logs", str(logs)]) == 3
    captured = capsys.readouterr()
    assert "refused" in captured.err and JWT not in captured.out + captured.err and "nothing was sent" in captured.err


def test_demo_without_a_recording_exits_4_with_advice(capsys):
    assert main(["ticket", "--text", "Users see: Could not validate timestamp: expired. Check system clock."]) == 4
    assert "--record" in capsys.readouterr().err


def test_unconfigured_backend_is_a_clear_error(capsys, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "")
    assert main(["ticket", "--backend", "ollama", "--text", "x"]) == 2
    assert "OLLAMA_HOST" in capsys.readouterr().err


def test_ticket_in_a_message_with_a_pasted_token_is_flagged(capsys):
    assert main(["ticket", "--text", f"Printer offline. My token is {JWT}"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("> WARNING") and JWT not in out
