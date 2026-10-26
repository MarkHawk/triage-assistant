import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from triage.backends.base import BackendError, NoRecordedAnswer, PromptTruncated, Refused
from triage.backends.claude import FALLBACK_BETA, ClaudeBackend
from triage.backends.demo import DemoBackend, key_for
from triage.backends.ollama import OllamaBackend
from triage.guard import Guard, SecretFound
from triage.prompt import CLAUDE_BUDGET, Budget, Prompt

SCHEMA = {"type": "object", "properties": {"outcome": {"type": "string"}}}
ANSWER = {"outcome": "needs_more_info"}
JWT = "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJ" + "zdWIiOiJ0ZXN0In0" + "." + "c2lnbmF0dXJl"


def prompt(untrusted=("a ticket", "a log"), key="k"):
    return Prompt(system="system text", user="user text " + " ".join(untrusted), mode="diagnose",
                  untrusted=list(untrusted), trusted=["runbook text"], key_material=key)


# --- Ollama, against a small fake server -------------------------------------------------

class FakeOllama:
    def __init__(self, reply=None, status=200):
        self.requests, self.reply, self.status = [], reply, status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.requests.append((self.path, json.loads(self.rfile.read(length))))
                body = json.dumps(outer.reply if outer.reply is not None else
                                  {"message": {"content": json.dumps(ANSWER)}, "done_reason": "stop", "prompt_eval_count": 900}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    servers = []

    def make(**kw):
        s = FakeOllama(**kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()


def ollama(host, guard=None, num_ctx=8192, **kw):
    return OllamaBackend(guard or Guard(), Budget(prompt_tokens=num_ctx - 1500), host, "qwen-14b", num_ctx, 1500,
                         kw.get("first", 600), kw.get("regular", 300), "30m")


def test_ollama_sends_context_window_answer_cap_and_schema_every_time(fake):
    server = fake()
    backend = ollama(server.host, num_ctx=12288)
    assert backend.complete(prompt(), SCHEMA) == ANSWER
    backend.complete(prompt(), SCHEMA)
    for path, body in server.requests:
        assert path == "/api/chat"
        assert body["options"]["num_ctx"] == 12288 and body["options"]["num_predict"] == 1500
        assert body["stream"] is False and body["format"] == SCHEMA and body["model"] == "qwen-14b"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_ollama_first_request_gets_the_longer_timeout(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(timeout)
        raise OSError("stop here")  # not a URLError: surfaces, but after the timeout was chosen

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    backend = ollama("http://127.0.0.1:1", first=600, regular=300)
    for _ in range(2):
        with pytest.raises(OSError):
            backend.complete(prompt(), SCHEMA)
    assert seen == [600, 600]  # still the first-request timeout until a request has succeeded


def test_ollama_regular_timeout_after_a_success(fake, monkeypatch):
    server = fake()
    backend = ollama(server.host, first=600, regular=300)
    backend.complete(prompt(), SCHEMA)
    import urllib.request
    real = urllib.request.urlopen
    seen = []
    monkeypatch.setattr("urllib.request.urlopen", lambda r, timeout: (seen.append(timeout), real(r, timeout=timeout))[1])
    backend.complete(prompt(), SCHEMA)
    assert seen == [300]


def test_ollama_truncated_prompt_is_an_error_not_a_silent_cut(fake):
    server = fake(reply={"message": {"content": json.dumps(ANSWER)}, "prompt_eval_count": 8192, "done_reason": "stop"})
    with pytest.raises(PromptTruncated) as err:
        ollama(server.host, num_ctx=8192).complete(prompt(), SCHEMA)
    assert "OLLAMA_NUM_CTX" in str(err.value)


def test_ollama_answer_cut_by_the_length_cap_is_an_error(fake):
    server = fake(reply={"message": {"content": "{"}, "prompt_eval_count": 900, "done_reason": "length"})
    with pytest.raises(BackendError, match="cut off"):
        ollama(server.host).complete(prompt(), SCHEMA)


def test_ollama_bad_json_and_http_errors_and_unreachable_host(fake):
    with pytest.raises(BackendError, match="valid JSON"):
        ollama(fake(reply={"message": {"content": "sorry"}, "prompt_eval_count": 5}).host).complete(prompt(), SCHEMA)
    with pytest.raises(BackendError, match="500"):
        ollama(fake(status=500, reply={"error": "model not found"}).host).complete(prompt(), SCHEMA)
    with pytest.raises(BackendError, match="Could not reach"):
        ollama("http://127.0.0.1:1").complete(prompt(), SCHEMA)


def test_a_secret_never_reaches_the_server(fake):
    server = fake()
    with pytest.raises(SecretFound):
        ollama(server.host).complete(prompt(untrusted=("ticket", f"log with {JWT}")), SCHEMA)
    assert server.requests == []


def test_lab_env_value_in_the_runbook_text_is_refused(fake):
    server = fake()
    p = prompt()
    p.trusted = ["runbook mentions lab-secret-value-12345"]
    with pytest.raises(SecretFound):
        ollama(server.host, guard=Guard({"lab-secret-value-12345"})).complete(p, SCHEMA)
    assert server.requests == []


# --- Claude, against a fake client ---------------------------------------------------------

class FakeClient:
    def __init__(self, response):
        self.calls = []
        create = lambda **kw: (self.calls.append(kw), response)[1]
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def reply(text=json.dumps(ANSWER), stop="end_turn", **extra):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", text=""), SimpleNamespace(type="text", text=text)],
                           stop_details=SimpleNamespace(category=extra.get("category")))


def claude(client, **kw):
    return ClaudeBackend(Guard(), CLAUDE_BUDGET, "key", "claude-sonnet-5-5", client=client, **kw)


def test_claude_request_uses_structured_output_not_forced_tool_choice():
    client = FakeClient(reply())
    assert claude(client, refusal_fallback=False).complete(prompt(), SCHEMA) == ANSWER
    kwargs = client.calls[0]
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert kwargs["output_config"]["effort"] == "medium"
    for banned in ("tool_choice", "tools", "temperature", "top_p", "top_k", "thinking", "betas", "fallbacks"):
        assert banned not in kwargs
    assert kwargs["model"] == "claude-sonnet-5-5" and kwargs["max_tokens"] >= 16000


def test_claude_refusal_fallback_is_on_by_default_and_can_be_turned_off():
    on = FakeClient(reply())
    claude(on).complete(prompt(), SCHEMA)
    assert on.calls[0]["betas"] == [FALLBACK_BETA] and on.calls[0]["fallbacks"] == "default"
    off = FakeClient(reply())
    claude(off, refusal_fallback=False).complete(prompt(), SCHEMA)
    assert "fallbacks" not in off.calls[0]


def test_claude_refusal_and_cut_off_answers_are_errors():
    with pytest.raises(Refused, match="cyber"):
        claude(FakeClient(reply(stop="refusal", category="cyber"))).complete(prompt(), SCHEMA)
    with pytest.raises(BackendError, match="cut off"):
        claude(FakeClient(reply(stop="max_tokens"))).complete(prompt(), SCHEMA)
    with pytest.raises(BackendError, match="valid JSON"):
        claude(FakeClient(reply(text="not json"))).complete(prompt(), SCHEMA)


def test_claude_secret_is_refused_before_any_call():
    client = FakeClient(reply())
    with pytest.raises(SecretFound):
        claude(client).complete(prompt(untrusted=(f"cookie: x\nAuthorization: Bearer {'a' * 30}", "")), SCHEMA)
    assert client.calls == []


# --- Demo ----------------------------------------------------------------------------------

def test_demo_records_and_replays_and_keys_ignore_prompt_wording(tmp_path):
    demo = DemoBackend(Guard(), CLAUDE_BUDGET, directory=tmp_path)
    p = prompt(key="ticket\n--\nlogs\n--\n15:s")
    with pytest.raises(NoRecordedAnswer, match="--record"):
        demo.complete(p, SCHEMA)
    path = demo.record(p, ANSWER, "claude", "claude-sonnet-5-5")
    saved = json.loads(path.read_text())
    assert saved["meta"]["backend"] == "claude" and saved["meta"]["model"] == "claude-sonnet-5-5" and saved["answer"] == ANSWER
    reworded = prompt(key=p.key_material)
    reworded.system = "a different system prompt"
    assert key_for(reworded) == key_for(p) and demo.complete(reworded, SCHEMA) == ANSWER
    assert key_for(prompt(key="another ticket")) != key_for(p)


def test_recording_replays_whatever_the_budget_was(tmp_path):
    """A recording made with a small Ollama window must replay under demo mode's larger budget."""
    from triage.prompt import build_prompt
    from triage.retrieval import Match
    from triage.runbooks import Runbook

    book = Runbook(15, "Redirect loop", "runbooks/15-x.md", {}, {"Symptom": "s " * 3000, "Fix": "f"})
    match = Match(book, 1.0, ("matches how customers describe it",))
    long_logs = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    small = build_prompt("sys", "diagnose", "ticket", long_logs, [match], (), "", Budget(prompt_tokens=3000))
    large = build_prompt("sys", "diagnose", "ticket", long_logs, [match], (), "", CLAUDE_BUDGET)
    assert small.user != large.user and key_for(small) == key_for(large)


def test_an_offline_backend_only_replays_what_it_has(tmp_path):
    demo = DemoBackend(Guard(), CLAUDE_BUDGET, directory=tmp_path)
    p = prompt(key="k1")
    assert demo.can_replay(p) is False
    demo.record(p, ANSWER, "ollama", "m")
    assert demo.can_replay(p) is True and ClaudeBackend(Guard(), CLAUDE_BUDGET, "k", "m", client=FakeClient(reply())).can_replay(p) is False
