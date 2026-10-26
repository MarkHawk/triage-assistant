import pytest

from triage.guard import Guard, SecretFound, load_lab_secrets

# Fake values, built at run time so no token-shaped text sits in the repo.
JWT = "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJ" + "zdWIiOiJ0ZXN0In0" + "." + "c2lnbmF0dXJl"
LAB_SECRET = "lab-" + "secret-" + "value-12345"
PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIBVgIBADANBgkqhkiG9w0BAQEFAASC\n-----END " + "PRIVATE KEY-----"

CLEAN_LOG_LINES = [
    "2026-10-22 08:44:57,240 INFO oidc-rp event=force_https path=/ scheme=http x_forwarded_proto=-",
    '172.30.182.1 "GET / HTTP/1.1" 302 235 host=idlab.home:8187 upstream_status=302 request_time=0.001',
    '"GET /auth/callback?code=***&state=***&session_state=*** HTTP/1.1" 502 157',
    'WWW-Authenticate: Bearer realm="idlab-api", error="invalid_token", error_description="The access token expired at 10:00"',
    "event=api_request_ok status=200 state=healthy code=200",
    "Set-Cookie: 1143",
    "nas: no reply from radius:1812 after 3 attempts (6s): RADIUS server not responding",
]

SECRET_SAMPLES = {
    "jwt": f"the token is {JWT} thanks",
    "private-key": f"my key:\n{PEM}\n",
    "bearer-token": "Authorization: Bearer abcdefghijklmnop0123456789",
    "cookie": "Cookie: KEYCLOAK_SESSION=abc123abc123; other=1",
    "login-value": "GET /auth/callback?code=abcd1234efgh&state=xyz987654321 HTTP/1.1",
    "saml-message": "SAMLResponse here: PHNhbWxwOlJlc3BvbnNl" + "A" * 300,
    "secret-value": 'password: Hunter2Hunter2 and "client_secret": "abc12345"',
}


@pytest.fixture
def guard():
    return Guard({LAB_SECRET})


@pytest.mark.parametrize("line", CLEAN_LOG_LINES)
def test_clean_log_lines_pass(guard, line):
    guard.check_log(line)


@pytest.mark.parametrize("kind", sorted(SECRET_SAMPLES))
def test_log_secrets_are_refused_hard(guard, kind):
    with pytest.raises(SecretFound) as err:
        guard.check_log(SECRET_SAMPLES[kind])
    assert kind in str(err.value)


def test_refusal_never_repeats_the_secret(guard):
    with pytest.raises(SecretFound) as err:
        guard.check_log(f"line {JWT} and {LAB_SECRET}")
    message = str(err.value)
    assert JWT not in message and LAB_SECRET not in message and "eyJ" not in message


@pytest.mark.parametrize("kind", sorted(SECRET_SAMPLES))
def test_ticket_secrets_are_masked_flagged_and_triage_continues(guard, kind):
    ticket = guard.mask_ticket("Hi, it fails. " + SECRET_SAMPLES[kind] + " Please help.")
    assert ticket.flagged
    assert kind in {f.kind for f in ticket.findings}
    assert "[MASKED:" in ticket.text
    assert "Hi, it fails." in ticket.text and "Please help." in ticket.text
    guard.check_log(ticket.text)  # what is left is safe to send
    assert "WARNING" in ticket.engineer_flag and kind in ticket.engineer_flag
    assert "advise" in ticket.model_note


def test_masking_keeps_names_and_hides_values(guard):
    ticket = guard.mask_ticket("GET /auth/callback?code=abcd1234efgh&state=xyz987654321 HTTP/1.1")
    assert ticket.text == "GET /auth/callback?code=[MASKED:login-value]&state=[MASKED:login-value] HTTP/1.1"


def test_clean_ticket_is_untouched(guard):
    text = "Hello, the portal goes round in circles since this morning. Chrome says redirected too many times."
    ticket = guard.mask_ticket(text)
    assert ticket.text == text and not ticket.flagged
    assert ticket.engineer_flag == "" and ticket.model_note == ""


def test_masking_twice_changes_nothing(guard):
    once = guard.mask_ticket(SECRET_SAMPLES["secret-value"] + " " + JWT)
    twice = guard.mask_ticket(once.text)
    assert twice.text == once.text and not twice.flagged


def test_lab_env_value_in_ticket_is_still_a_hard_refusal(guard):
    with pytest.raises(SecretFound) as err:
        guard.mask_ticket(f"my password is {LAB_SECRET}")
    assert LAB_SECRET not in str(err.value)


def test_lab_env_value_in_log_is_refused(guard):
    with pytest.raises(SecretFound):
        guard.check_log(f"event=x detail={LAB_SECRET}")


def test_outbound_gate(guard):
    guard.check_outbound(untrusted=["a clean log line"], trusted=["Example: code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"])
    with pytest.raises(SecretFound):
        guard.check_outbound(untrusted=[JWT])
    with pytest.raises(SecretFound):
        guard.check_outbound(untrusted=["ok"], trusted=[f"runbook text {LAB_SECRET}"])


def test_lab_secrets_come_from_secret_looking_keys_only(tmp_path):
    (tmp_path / ".env").write_text(
        "LAB_HOST=idlab.home\nKC_PORT=8180\nKC_ADMIN_USER=administrator\n"
        "DEMO_PASSWORD=" + LAB_SECRET + "\nRADIUS_SECRET=short\nSP_SIGNING_CERT=" + "A" * 40 + "\n")
    assert load_lab_secrets(tmp_path) == {LAB_SECRET}


def test_no_lab_env_means_no_lab_secrets(tmp_path):
    assert load_lab_secrets(tmp_path) == frozenset()
