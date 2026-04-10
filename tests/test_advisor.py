"""Unit tests for advisor.py."""

import sys
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# _load_system_prompt
# ---------------------------------------------------------------------------


def test_load_system_prompt_from_file(tmp_path):
    import advisor

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Test system prompt")
    with mock.patch.object(advisor, "_PROMPT_PATH", prompt_file):
        result = advisor._load_system_prompt()
    assert result == "Test system prompt"


def test_load_system_prompt_fallback(tmp_path):
    import advisor

    missing = tmp_path / "does_not_exist.txt"
    with mock.patch.object(advisor, "_PROMPT_PATH", missing):
        result = advisor._load_system_prompt()
    assert result == advisor._FALLBACK_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# _call_llm
# ---------------------------------------------------------------------------


def _mock_response(content: str = "Buy AAPL", tokens: dict | None = None) -> mock.MagicMock:
    usage = tokens or {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }
    return resp


def test_call_llm_no_base_url(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", None)
    result = advisor._call_llm("sys", "user")
    assert "ADVISOR_BASE_URL" in result


def test_call_llm_no_api_key_cloud(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", None)
    result = advisor._call_llm("sys", "user")
    assert "ADVISOR_API_KEY" in result


def test_call_llm_ollama_no_key_ok(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", None)
    monkeypatch.setattr(advisor.settings, "advisor_model", "gemma4:e4b")

    with mock.patch.object(advisor.requests, "post", return_value=_mock_response("Ollama reply")):
        result = advisor._call_llm("sys", "user")
    assert result == "Ollama reply"


def test_call_llm_success(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")
    monkeypatch.setattr(advisor.settings, "advisor_model", "claude-opus-4-6")

    mock_resp = _mock_response("Sell MSFT")
    with mock.patch.object(advisor.requests, "post", return_value=mock_resp) as mock_post:
        result = advisor._call_llm("system prompt", "user message")

    assert result == "Sell MSFT"
    # Verify the request was made correctly
    call_kwargs = mock_post.call_args
    assert "Authorization" in call_kwargs.kwargs["headers"]
    payload = call_kwargs.kwargs["json"]
    assert payload["model"] == "claude-opus-4-6"
    assert payload["messages"][0]["content"] == "system prompt"
    assert payload["messages"][1]["content"] == "user message"


def test_call_llm_with_api_key_header(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-openai")

    with mock.patch.object(advisor.requests, "post", return_value=_mock_response()) as mock_post:
        advisor._call_llm("sys", "usr")

    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-openai"


def test_call_llm_no_usage_in_response(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")

    resp = mock.MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": "reply"}}]}
    with mock.patch.object(advisor.requests, "post", return_value=resp):
        result = advisor._call_llm("sys", "usr")
    assert result == "reply"


def test_call_llm_api_error(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")

    mock_resp = mock.MagicMock()
    mock_resp.raise_for_status.side_effect = advisor.requests.HTTPError("429")
    with (
        mock.patch.object(advisor.requests, "post", return_value=mock_resp),
        pytest.raises(advisor.requests.HTTPError),
    ):
        advisor._call_llm("sys", "usr")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def test_monthly_rebalance(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")

    with mock.patch.object(advisor, "_call_llm", return_value="Rebalance: hold") as mock_call:
        result = advisor.monthly_rebalance()

    assert result == "Rebalance: hold"
    user_msg = mock_call.call_args[0][1]
    assert "context data" in user_msg
    assert "Monthly rebalance" in user_msg


def test_analyze_alert(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")

    with mock.patch.object(advisor, "_call_llm", return_value="Sell now") as mock_call:
        result = advisor.analyze_alert("AAPL dropped 15%")

    assert result == "Sell now"
    user_msg = mock_call.call_args[0][1]
    assert "AAPL dropped 15%" in user_msg
    assert "context data" in user_msg


def test_analyze_opportunity(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")

    with mock.patch.object(advisor, "_call_llm", return_value="Buy PLTR") as mock_call:
        result = advisor.analyze_opportunity("PLTR")

    assert result == "Buy PLTR"
    user_msg = mock_call.call_args[0][1]
    assert "PLTR" in user_msg
    assert "context data" in user_msg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_rebalance(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "rebalance"])
    monkeypatch.setattr(advisor, "monthly_rebalance", lambda: "Rebalance output")
    advisor.main()
    assert "Rebalance output" in capsys.readouterr().out


def test_main_alert(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "alert", "AAPL dropped 15%"])
    monkeypatch.setattr(advisor, "analyze_alert", lambda d: f"Alert: {d}")
    advisor.main()
    assert "Alert: AAPL dropped 15%" in capsys.readouterr().out


def test_main_analyze(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "analyze", "PLTR"])
    monkeypatch.setattr(advisor, "analyze_opportunity", lambda t: f"Analyze: {t}")
    advisor.main()
    assert "Analyze: PLTR" in capsys.readouterr().out


def test_main_no_args(monkeypatch):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor"])
    with pytest.raises(SystemExit, match="1"):
        advisor.main()


def test_main_unknown_command(monkeypatch):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "unknown"])
    with pytest.raises(SystemExit, match="1"):
        advisor.main()


def test_main_alert_missing_details(monkeypatch):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "alert"])
    with pytest.raises(SystemExit, match="1"):
        advisor.main()


def test_main_analyze_missing_ticker(monkeypatch):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "analyze"])
    with pytest.raises(SystemExit, match="1"):
        advisor.main()
