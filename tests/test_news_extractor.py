"""Unit tests for news_extractor.py."""

import json
from datetime import UTC, datetime
from unittest import mock

import pytest
import requests as req_lib

from db import db_conn, get_conn

# --- _post_to_ollama ---


def _ollama_response(content: str):
    resp = mock.MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _signal_json(**overrides):
    data = {
        "tickers": ["AAPL"],
        "sentiment": "positive",
        "catalyst": "earnings",
        "timeframe": "weeks",
        "summary": "Strong results",
    }
    data.update(overrides)
    return json.dumps(data)


def test_post_to_ollama_success():
    from news_extractor import _post_to_ollama

    with mock.patch("news_extractor.requests.post", return_value=_ollama_response("ok")):
        assert _post_to_ollama({"model": "test"}) == "ok"


def test_post_to_ollama_retry_then_succeed():
    from news_extractor import _post_to_ollama

    with (
        mock.patch(
            "news_extractor.requests.post",
            side_effect=[req_lib.exceptions.ConnectionError("fail"), _ollama_response("ok")],
        ),
        mock.patch("news_extractor.time.sleep"),
    ):
        assert _post_to_ollama({"model": "test"}) == "ok"


def test_post_to_ollama_all_retries_fail():
    from news_extractor import _post_to_ollama

    with (
        mock.patch(
            "news_extractor.requests.post",
            side_effect=req_lib.exceptions.ConnectionError("fail"),
        ),
        mock.patch("news_extractor.time.sleep"),
        pytest.raises(req_lib.exceptions.ConnectionError),
    ):
        _post_to_ollama({"model": "test"})


def test_post_to_ollama_timeout():
    from news_extractor import _post_to_ollama

    with (
        mock.patch(
            "news_extractor.requests.post",
            side_effect=req_lib.exceptions.Timeout("timed out"),
        ),
        pytest.raises(TimeoutError, match="Ollama timed out"),
    ):
        _post_to_ollama({"model": "test"})


# --- _parse_signal_from_content (gap coverage) ---


def test_parse_signal_markdown_invalid_then_brace():
    """Markdown block fails validation, brace match succeeds."""
    from news_extractor import _parse_signal_from_content

    # Markdown content without braces so greedy brace regex only matches the valid JSON
    content = "```json\ninvalid-content\n```\n" + _signal_json()
    sig = _parse_signal_from_content(content)
    assert sig.sentiment == "positive"


def test_parse_signal_brace_match_invalid():
    """Brace match found but invalid → ValueError."""
    from news_extractor import _parse_signal_from_content

    with pytest.raises(ValueError, match="Could not parse"):
        _parse_signal_from_content("some {invalid json} here")


# --- call_ollama ---


def test_call_ollama():
    from news_extractor import call_ollama

    with mock.patch(
        "news_extractor.requests.post",
        return_value=_ollama_response(_signal_json()),
    ):
        sig = call_ollama("Apple earnings beat expectations")
    assert sig.tickers == ["AAPL"]
    assert sig.sentiment == "positive"


# --- score_confidence ---


def test_score_confidence_all_agree():
    from news_extractor import score_confidence

    with mock.patch(
        "news_extractor.requests.post",
        return_value=_ollama_response(_signal_json()),
    ):
        sig, conf = score_confidence("Apple earnings")
    assert conf == 0.9


def test_score_confidence_sentiment_disagree():
    from news_extractor import score_confidence

    resps = [
        _ollama_response(_signal_json(sentiment="positive")),
        _ollama_response(_signal_json(sentiment="negative")),
        _ollama_response(_signal_json(sentiment="positive")),
    ]
    with mock.patch("news_extractor.requests.post", side_effect=resps):
        _, conf = score_confidence("Test")
    assert conf == 0.6  # tickers agree, sentiment disagrees


def test_score_confidence_all_disagree():
    from news_extractor import score_confidence

    resps = [
        _ollama_response(_signal_json(tickers=["AAPL"], sentiment="positive")),
        _ollama_response(_signal_json(tickers=["MSFT"], sentiment="negative")),
        _ollama_response(_signal_json(tickers=["GOOG"], sentiment="neutral")),
    ]
    with mock.patch("news_extractor.requests.post", side_effect=resps):
        _, conf = score_confidence("Test")
    assert conf == 0.3


def test_score_confidence_partial_failure():
    from news_extractor import score_confidence

    with mock.patch(
        "news_extractor.requests.post",
        side_effect=[_ollama_response(_signal_json()), Exception("fail"), Exception("fail")],
    ):
        _, conf = score_confidence("Test")
    assert conf == 0.3  # only 1 result


def test_score_confidence_all_fail():
    from news_extractor import score_confidence

    with (
        mock.patch("news_extractor.requests.post", side_effect=Exception("fail")),
        pytest.raises(RuntimeError, match="All Ollama extraction attempts failed"),
    ):
        score_confidence("Test")


# --- _get_unprocessed_articles ---


def test_get_unprocessed_articles():
    from news_extractor import _get_unprocessed_articles

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, title, content_snippet, processed)"
            " VALUES (?, ?, ?, ?)",
            ("https://example.com/1", "Art 1", "Snippet", 0),
        )
        conn.execute(
            "INSERT INTO news_articles (url, title, content_snippet, processed)"
            " VALUES (?, ?, ?, ?)",
            ("https://example.com/2", "Art 2", "Snippet", 1),
        )
    articles = _get_unprocessed_articles()
    assert len(articles) == 1
    assert articles[0]["title"] == "Art 1"


# --- process_unprocessed ---


def _seed_article(url="https://example.com/test", title="Test", processed=0):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, title, content_snippet, processed)"
            " VALUES (?, ?, ?, ?)",
            (url, title, "content", processed),
        )
    conn2 = get_conn()
    try:
        return conn2.execute("SELECT id FROM news_articles WHERE url = ?", (url,)).fetchone()["id"]
    finally:
        conn2.close()


def test_process_unprocessed_empty():
    from news_extractor import process_unprocessed

    assert process_unprocessed() == 0


def test_process_unprocessed_success():
    from news_extractor import process_unprocessed

    _seed_article()
    with mock.patch(
        "news_extractor.requests.post",
        return_value=_ollama_response(_signal_json()),
    ):
        assert process_unprocessed(use_confidence_scoring=False) == 1

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT processed FROM news_articles WHERE url = ?",
            ("https://example.com/test",),
        ).fetchone()
    finally:
        conn.close()
    assert row["processed"] == 1


def test_process_unprocessed_with_confidence():
    from news_extractor import process_unprocessed

    _seed_article(url="https://example.com/conf")
    with mock.patch(
        "news_extractor.requests.post",
        return_value=_ollama_response(_signal_json()),
    ):
        assert process_unprocessed(use_confidence_scoring=True) == 1


def test_process_unprocessed_failure():
    from news_extractor import process_unprocessed

    _seed_article(url="https://example.com/fail")
    with (
        mock.patch(
            "news_extractor.requests.post",
            side_effect=req_lib.exceptions.ConnectionError("down"),
        ),
        mock.patch("news_extractor.time.sleep"),
    ):
        assert process_unprocessed(use_confidence_scoring=False) == 0


# --- filter_relevant_signals ---


def test_filter_relevant_signals_empty_portfolio():
    from news_extractor import filter_relevant_signals

    assert filter_relevant_signals([]) == []


def test_filter_relevant_signals_malformed_tickers():
    from news_extractor import filter_relevant_signals

    article_id = _seed_article(url="https://example.com/malformed", processed=1)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_signals (article_id, tickers, sentiment, confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (article_id, "not-valid-json", "positive", 0.8, datetime.now(tz=UTC).isoformat()),
        )
    result = filter_relevant_signals(["AAPL"])
    assert isinstance(result, list)


# --- main ---


def test_main(monkeypatch, capsys):
    import news_extractor

    monkeypatch.setattr(news_extractor, "process_unprocessed", lambda **kw: 3)
    news_extractor.main()
    assert "3 article(s)" in capsys.readouterr().out
