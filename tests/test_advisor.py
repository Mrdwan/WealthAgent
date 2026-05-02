"""Unit tests for advisor.py."""

import json
import sys
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_rebalance_dict(**overrides) -> dict:
    """Return a minimal valid MonthlyRebalance dict for testing."""
    base = {
        "summary": "buy AAPL €500",
        "report": "## Monthly Rebalance\nAll good.",
        "iwda_top_n": [{"rank": 1, "ticker": "AAPL", "name": "Apple Inc", "weight_pct": 5.0}],
        "portfolio_vs_index": [
            {
                "ticker": "AAPL",
                "portfolio_pct": 5.0,
                "index_pct": 5.0,
                "gap_pct": 0.0,
                "action": "HOLD",
            }
        ],
        "stock_allocation": [
            {"ticker": "AAPL", "amount_eur": 500.0, "rationale": "Underweight vs index"}
        ],
        "buffer_recommendation": {
            "amount_eur": 200.0,
            "target": "MSFT",
            "rationale": "Second most underweight",
        },
        "legacy_holdings": [],
        "sell_recommendations": [],
        "tracking_error": {
            "portfolio_return_pct": 2.5,
            "iwda_return_pct": 2.0,
            "tracking_error_pp": 0.5,
            "explanation": "Slight outperformance.",
        },
        "tax_summary": {
            "realized_gains_ytd_eur": 0.0,
            "exemption_used_eur": 0.0,
            "exemption_remaining_eur": 1270.0,
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _load_system_prompt
# ---------------------------------------------------------------------------


def test_prompt_path_structure():
    """_PROMPT_PATH must be a sibling config/ directory next to advisor.py itself.

    In the container advisor.py sits at /app/advisor.py and config/ at
    /app/config/, so the path must be computed with one parent level, not two.
    """
    from pathlib import Path

    import advisor

    expected = Path(advisor.__file__).resolve().parent / "config" / "investment_prompt.md"
    assert expected == advisor._PROMPT_PATH


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
# _call_llm_structured
# ---------------------------------------------------------------------------


def test_call_llm_structured_no_base_url(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", None)
    result = advisor._call_llm_structured("sys", "user", {})
    assert result is None


def test_call_llm_structured_no_api_key(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", None)
    result = advisor._call_llm_structured("sys", "user", {})
    assert result is None


def test_call_llm_structured_request_exception(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")
    monkeypatch.setattr(advisor.settings, "advisor_model", "claude-opus-4-6")

    with mock.patch.object(
        advisor.requests,
        "post",
        side_effect=advisor.requests.ConnectionError("connection failed"),
    ):
        result = advisor._call_llm_structured("sys", "user", {})
    assert result is None


def test_call_llm_structured_invalid_json(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")
    monkeypatch.setattr(advisor.settings, "advisor_model", "claude-opus-4-6")

    resp = mock.MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": "not json {"}}]}
    with mock.patch.object(advisor.requests, "post", return_value=resp):
        result = advisor._call_llm_structured("sys", "user", {})
    assert result is None


def test_call_llm_structured_success(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")
    monkeypatch.setattr(advisor.settings, "advisor_model", "claude-opus-4-6")

    data = {"summary": "ok", "report": "full"}
    resp = mock.MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(data)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    with mock.patch.object(advisor.requests, "post", return_value=resp) as mock_post:
        result = advisor._call_llm_structured("sys", "user", {"type": "object"})

    assert result == data
    payload = mock_post.call_args.kwargs["json"]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "MonthlyRebalance"
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_call_llm_structured_http_error(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "https://api.anthropic.com/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", "sk-test")

    mock_resp = mock.MagicMock()
    mock_resp.raise_for_status.side_effect = advisor.requests.HTTPError("422")
    with mock.patch.object(advisor.requests, "post", return_value=mock_resp):
        result = advisor._call_llm_structured("sys", "user", {})
    assert result is None


def test_call_llm_structured_ollama_no_key(monkeypatch):
    import advisor

    monkeypatch.setattr(advisor.settings, "advisor_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(advisor.settings, "advisor_api_key", None)
    monkeypatch.setattr(advisor.settings, "advisor_model", "gemma4:e4b")

    data = {"key": "val"}
    resp = mock.MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(data)}}],
    }
    with mock.patch.object(advisor.requests, "post", return_value=resp):
        result = advisor._call_llm_structured("sys", "user", {})
    assert result == data


# ---------------------------------------------------------------------------
# _inline_refs
# ---------------------------------------------------------------------------


def test_inline_refs_no_refs():
    import advisor

    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    result = advisor._inline_refs(schema)
    assert result == schema


def test_inline_refs_resolves_ref():
    import advisor

    schema = {
        "type": "object",
        "$defs": {"MyType": {"type": "integer"}},
        "properties": {"x": {"$ref": "#/$defs/MyType"}},
    }
    result = advisor._inline_refs(schema)
    assert result["properties"]["x"] == {"type": "integer"}
    assert "$defs" not in result


def test_inline_refs_nested():
    import advisor

    schema = {
        "$defs": {
            "Inner": {"type": "string"},
            "Outer": {"type": "object", "properties": {"val": {"$ref": "#/$defs/Inner"}}},
        },
        "properties": {"outer": {"$ref": "#/$defs/Outer"}},
    }
    result = advisor._inline_refs(schema)
    assert result["properties"]["outer"]["properties"]["val"] == {"type": "string"}
    assert "$defs" not in result


def test_inline_refs_strips_defs_from_top_level():
    import advisor

    schema = {
        "$defs": {"Foo": {"type": "number"}},
        "type": "object",
        "properties": {"n": {"$ref": "#/$defs/Foo"}},
    }
    result = advisor._inline_refs(schema)
    assert "$defs" not in result


def test_inline_refs_list_items():
    import advisor

    schema = {
        "$defs": {"Item": {"type": "boolean"}},
        "type": "array",
        "items": {"$ref": "#/$defs/Item"},
    }
    result = advisor._inline_refs(schema)
    assert result["items"] == {"type": "boolean"}
    assert "$defs" not in result


def test_inline_refs_non_dict_result():
    """When _resolve returns a non-dict (e.g. top-level $ref to a list), skip pop."""
    import advisor

    # Top-level is a list — _resolve returns a list, not a dict.
    # The isinstance(result, dict) guard must handle this without error.
    result = advisor._inline_refs(["item1", "item2"])
    assert result == ["item1", "item2"]


# ---------------------------------------------------------------------------
# _parse_advisor_response
# ---------------------------------------------------------------------------


def test_parse_advisor_response_valid_json():
    import advisor

    raw = json.dumps({"summary": "sell MSFT, buy TSLA", "report": "## Full report\n..."})
    result = advisor._parse_advisor_response(raw)
    assert result.summary == "sell MSFT, buy TSLA"
    assert result.report == "## Full report\n..."


def test_parse_advisor_response_strips_markdown_fences():
    import advisor

    raw = '```json\n{"summary": "hold AAPL", "report": "Full analysis."}\n```'
    result = advisor._parse_advisor_response(raw)
    assert result.summary == "hold AAPL"
    assert result.report == "Full analysis."


def test_parse_advisor_response_strips_plain_fences():
    import advisor

    raw = '```\n{"summary": "buy PLTR", "report": "Details."}\n```'
    result = advisor._parse_advisor_response(raw)
    assert result.summary == "buy PLTR"


def test_parse_advisor_response_invalid_json_fallback():
    import advisor

    raw = "This is not JSON at all."
    result = advisor._parse_advisor_response(raw)
    assert result.summary == ""
    assert result.report == raw


def test_parse_advisor_response_missing_key_fallback():
    import advisor

    raw = json.dumps({"summary": "sell MSFT"})  # missing "report"
    result = advisor._parse_advisor_response(raw)
    assert result.summary == ""
    assert result.report == raw


# ---------------------------------------------------------------------------
# monthly_rebalance
# ---------------------------------------------------------------------------


def test_monthly_rebalance_success(monkeypatch):
    """Valid LLM response is parsed into MonthlyRebalance and validated."""
    import advisor

    llm_data = _make_minimal_rebalance_dict()

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")
    monkeypatch.setattr(advisor, "_call_llm_structured", lambda sys, usr, schema: llm_data)
    monkeypatch.setattr(advisor.advisor_validator, "validate", lambda r: [])

    result = advisor.monthly_rebalance()

    assert isinstance(result, advisor.MonthlyRebalance)
    assert result.summary == "buy AAPL €500"
    assert result.report == "## Monthly Rebalance\nAll good."
    assert len(result.iwda_top_n) == 1
    assert result.iwda_top_n[0].ticker == "AAPL"


def test_monthly_rebalance_with_validation_errors(monkeypatch):
    """Validation errors are appended to the summary."""
    import advisor

    llm_data = _make_minimal_rebalance_dict()

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")
    monkeypatch.setattr(advisor, "_call_llm_structured", lambda sys, usr, schema: llm_data)
    monkeypatch.setattr(
        advisor.advisor_validator,
        "validate",
        lambda r: ["stock allocation €1100 exceeds cap €1050"],
    )

    result = advisor.monthly_rebalance()

    assert "⚠️ validation flagged:" in result.summary
    assert "stock allocation" in result.summary


def test_monthly_rebalance_llm_returns_none(monkeypatch):
    """When LLM call returns None, a minimal fallback MonthlyRebalance is returned."""
    import advisor

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")
    monkeypatch.setattr(advisor, "_call_llm_structured", lambda sys, usr, schema: None)

    result = advisor.monthly_rebalance()

    assert isinstance(result, advisor.MonthlyRebalance)
    assert "unavailable" in result.summary
    assert result.iwda_top_n == []


def test_monthly_rebalance_invalid_schema(monkeypatch):
    """When LLM returns data that fails Pydantic validation, fallback is returned."""
    import advisor

    # Missing required fields
    bad_data = {"summary": "ok", "report": "report"}

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")
    monkeypatch.setattr(advisor, "_call_llm_structured", lambda sys, usr, schema: bad_data)

    result = advisor.monthly_rebalance()

    assert isinstance(result, advisor.MonthlyRebalance)
    assert "schema validation" in result.summary


def test_monthly_rebalance_calls_validator(monkeypatch):
    """monthly_rebalance calls advisor_validator.validate with the parsed model."""
    import advisor

    llm_data = _make_minimal_rebalance_dict()
    captured = {}

    def fake_validate(r):
        captured["rebalance"] = r
        return []

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")
    monkeypatch.setattr(advisor, "_call_llm_structured", lambda sys, usr, schema: llm_data)
    monkeypatch.setattr(advisor.advisor_validator, "validate", fake_validate)

    result = advisor.monthly_rebalance()
    assert isinstance(captured["rebalance"], advisor.MonthlyRebalance)
    assert result.summary == "buy AAPL €500"


def test_monthly_rebalance_user_message_contains_context(monkeypatch):
    """monthly_rebalance passes portfolio context in the user message."""
    import advisor

    captured_msgs: list[tuple[str, str, dict]] = []

    def fake_structured(sys_prompt, user_msg, schema):
        captured_msgs.append((sys_prompt, user_msg, schema))
        return _make_minimal_rebalance_dict()

    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys prompt")
    monkeypatch.setattr(advisor, "build_context", lambda: "MY CONTEXT DATA")
    monkeypatch.setattr(advisor, "_call_llm_structured", fake_structured)
    monkeypatch.setattr(advisor.advisor_validator, "validate", lambda r: [])

    advisor.monthly_rebalance()

    _, user_msg, _ = captured_msgs[0]
    assert "MY CONTEXT DATA" in user_msg
    assert "Monthly rebalance" in user_msg


# ---------------------------------------------------------------------------
# analyze_alert
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# analyze_opportunity
# ---------------------------------------------------------------------------


def test_analyze_opportunity(monkeypatch):
    import advisor

    raw_json = json.dumps({"summary": "buy PLTR €300", "report": "PLTR looks good"})
    monkeypatch.setattr(advisor, "_load_system_prompt", lambda: "sys")
    monkeypatch.setattr(advisor, "build_context", lambda: "context data")

    with mock.patch.object(advisor, "_call_llm", return_value=raw_json) as mock_call:
        result = advisor.analyze_opportunity("PLTR")

    assert result.summary == "buy PLTR €300"
    assert result.report == "PLTR looks good"
    user_msg = mock_call.call_args[0][1]
    assert "PLTR" in user_msg
    assert "context data" in user_msg
    assert "JSON" in user_msg  # JSON format instruction included


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_rebalance(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "rebalance"])

    def fake_rebalance():
        return advisor.MonthlyRebalance(**_make_minimal_rebalance_dict())

    monkeypatch.setattr(advisor, "monthly_rebalance", fake_rebalance)
    advisor.main()
    assert "Monthly Rebalance" in capsys.readouterr().out


def test_main_alert(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "alert", "AAPL dropped 15%"])
    monkeypatch.setattr(advisor, "analyze_alert", lambda d: f"Alert: {d}")
    advisor.main()
    assert "Alert: AAPL dropped 15%" in capsys.readouterr().out


def test_main_analyze(monkeypatch, capsys):
    import advisor

    monkeypatch.setattr(sys, "argv", ["advisor", "analyze", "PLTR"])
    monkeypatch.setattr(
        advisor,
        "analyze_opportunity",
        lambda t: advisor.AdvisorResponse(summary="buy PLTR", report=f"Analyze: {t}"),
    )
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
