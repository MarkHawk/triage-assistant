import pytest

from triage.config import ConfigError, load_config


def cfg(tmp_path, text="", environ=None):
    env = tmp_path / ".env"
    env.write_text(text)
    return load_config(env_file=env, environ=environ or {})


def test_defaults_are_safe(tmp_path):
    c = cfg(tmp_path)
    assert c.backend == "demo"
    assert c.claude_model == "claude-sonnet-5-5"
    assert c.ollama_num_ctx == 12288
    assert c.ollama_first_timeout_s > c.ollama_timeout_s
    assert c.lab_dir.is_absolute()


def test_prompt_budget_leaves_room_for_the_answer(tmp_path):
    c = cfg(tmp_path, "OLLAMA_NUM_CTX=8192\nOLLAMA_RESERVE_OUTPUT_TOKENS=1500\n")
    assert c.ollama_prompt_budget_tokens == 6692


def test_environment_beats_file(tmp_path):
    assert cfg(tmp_path, "BACKEND=ollama\n", {"BACKEND": "demo"}).backend == "demo"


def test_comments_and_quotes(tmp_path):
    c = cfg(tmp_path, '# note\nOLLAMA_MODEL="qwen-14b"\n')
    assert c.ollama_model == "qwen-14b"


@pytest.mark.parametrize("text", [
    "BACKEND=gpt\n",
    "OLLAMA_NUM_CTX=lots\n",
    "OLLAMA_NUM_CTX=512\n",
    "OLLAMA_NUM_CTX=4096\nOLLAMA_RESERVE_OUTPUT_TOKENS=4096\n",
])
def test_bad_values_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        cfg(tmp_path, text)


def test_backend_needs_its_settings(tmp_path):
    with pytest.raises(ConfigError):
        cfg(tmp_path, "BACKEND=claude\n").require_backend_settings()
    with pytest.raises(ConfigError):
        cfg(tmp_path, "BACKEND=ollama\n").require_backend_settings()
    cfg(tmp_path, "BACKEND=ollama\nOLLAMA_HOST=http://box.lan:11434/\nOLLAMA_MODEL=m\n").require_backend_settings()


def test_ollama_host_trailing_slash_removed(tmp_path):
    assert cfg(tmp_path, "OLLAMA_HOST=http://box.lan:11434/\n").ollama_host == "http://box.lan:11434"


def test_log_secrets_defaults_to_mask_and_can_be_refuse(tmp_path):
    assert cfg(tmp_path).log_secrets == "mask"
    assert cfg(tmp_path, "LOG_SECRETS=refuse\n").log_secrets == "refuse"
    with pytest.raises(ConfigError):
        cfg(tmp_path, "LOG_SECRETS=ignore\n")
