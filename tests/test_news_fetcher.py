"""Unit tests for news_fetcher.py."""

import time
from unittest import mock

from db import db_conn


class _Entry(dict):
    """Dict subclass with attribute access, mimicking feedparser entries."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


# --- _parse_published_at ---


def test_parse_published_at_struct_time():
    from news_fetcher import _parse_published_at

    entry = _Entry(published_parsed=time.strptime("2024-04-08", "%Y-%m-%d"))
    result = _parse_published_at(entry)
    assert result is not None
    assert result.year == 2024


def test_parse_published_at_raw_published():
    from news_fetcher import _parse_published_at

    entry = _Entry(published="Mon, 08 Apr 2024 12:00:00 GMT")
    result = _parse_published_at(entry)
    assert result is not None


def test_parse_published_at_updated_fallback():
    from news_fetcher import _parse_published_at

    entry = _Entry(updated="Mon, 08 Apr 2024 12:00:00 GMT")
    result = _parse_published_at(entry)
    assert result is not None


def test_parse_published_at_none():
    from news_fetcher import _parse_published_at

    assert _parse_published_at(_Entry()) is None


def test_parse_published_at_bad_struct():
    from news_fetcher import _parse_published_at

    entry = _Entry(published_parsed="not-a-struct")
    result = _parse_published_at(entry)
    # Falls through to raw parsing, which also fails → None
    assert result is None


def test_parse_published_at_bad_raw():
    from news_fetcher import _parse_published_at

    entry = _Entry(published="totally-invalid-date-string")
    assert _parse_published_at(entry) is None


# --- _existing_urls ---


def test_existing_urls_empty():
    from news_fetcher import _existing_urls

    assert _existing_urls() == set()


def test_existing_urls_with_data():
    from news_fetcher import _existing_urls

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, title, processed) VALUES (?, ?, ?)",
            ("https://example.com/1", "Test", 0),
        )
    assert "https://example.com/1" in _existing_urls()


# --- fetch_feed ---


def _mock_parsed(entries, bozo=False, bozo_exception=None, feed_title="Test Feed"):
    p = mock.MagicMock()
    p.bozo = bozo
    p.entries = entries
    p.bozo_exception = bozo_exception
    p.feed.get = lambda k, default=None: ({"title": feed_title}.get(k) if k == "title" else default)
    return p


def test_fetch_feed_success():
    from news_fetcher import fetch_feed

    entry = {
        "link": "https://example.com/article1",
        "title": "Test Article",
        "summary": "Summary text",
        "published_parsed": time.strptime("2024-04-08", "%Y-%m-%d"),
    }
    with mock.patch("news_fetcher.feedparser.parse", return_value=_mock_parsed([entry])):
        assert fetch_feed("https://example.com/rss") == 1


def test_fetch_feed_duplicate_skip():
    from news_fetcher import fetch_feed

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_articles (url, title, processed) VALUES (?, ?, ?)",
            ("https://example.com/existing", "Old", 0),
        )
    entry = {"link": "https://example.com/existing", "title": "Same"}
    with mock.patch("news_fetcher.feedparser.parse", return_value=_mock_parsed([entry])):
        assert fetch_feed("https://example.com/rss") == 0


def test_fetch_feed_no_link():
    from news_fetcher import fetch_feed

    entry = {"title": "No Link"}  # no link, no id → url=""
    with mock.patch("news_fetcher.feedparser.parse", return_value=_mock_parsed([entry])):
        assert fetch_feed("https://example.com/rss") == 0


def test_fetch_feed_id_fallback():
    from news_fetcher import fetch_feed

    entry = {"id": "https://example.com/id-only", "title": "ID article"}
    with mock.patch("news_fetcher.feedparser.parse", return_value=_mock_parsed([entry])):
        assert fetch_feed("https://example.com/rss") == 1


def test_fetch_feed_parse_exception():
    from news_fetcher import fetch_feed

    with mock.patch("news_fetcher.feedparser.parse", side_effect=Exception("parse err")):
        assert fetch_feed("https://example.com/rss") == 0


def test_fetch_feed_bozo_no_entries():
    from news_fetcher import fetch_feed

    with mock.patch(
        "news_fetcher.feedparser.parse",
        return_value=_mock_parsed([], bozo=True, bozo_exception="XML err"),
    ):
        assert fetch_feed("https://example.com/rss") == 0


def test_fetch_feed_bozo_with_entries():
    from news_fetcher import fetch_feed

    entry = {"link": "https://example.com/bozo1", "title": "Bozo"}
    with mock.patch(
        "news_fetcher.feedparser.parse",
        return_value=_mock_parsed([entry], bozo=True),
    ):
        assert fetch_feed("https://example.com/rss") == 1


def test_fetch_feed_store_exception():
    from news_fetcher import fetch_feed

    entry = {"link": "https://example.com/fail-store", "title": "Fail"}
    with (
        mock.patch("news_fetcher.feedparser.parse", return_value=_mock_parsed([entry])),
        mock.patch("news_fetcher.db_conn", side_effect=Exception("DB err")),
    ):
        assert fetch_feed("https://example.com/rss") == 0


def test_fetch_feed_no_title_no_summary():
    from news_fetcher import fetch_feed

    entry = {"link": "https://example.com/bare"}
    parsed = _mock_parsed([entry], feed_title=None)
    # Override feed.get to return None for title
    parsed.feed.get = lambda k, default=None: None
    with mock.patch("news_fetcher.feedparser.parse", return_value=parsed):
        assert fetch_feed("https://example.com/rss") == 1


# --- fetch_all_feeds ---


def test_fetch_all_feeds(monkeypatch):
    import news_fetcher

    monkeypatch.setattr(news_fetcher, "fetch_feed", lambda url: 2)
    mock_settings = mock.MagicMock()
    mock_settings.rss_feeds = ["https://a.com/rss", "https://b.com/rss"]
    monkeypatch.setattr(news_fetcher, "settings", mock_settings)
    assert news_fetcher.fetch_all_feeds() == 4


def test_fetch_all_feeds_exception(monkeypatch):
    import news_fetcher

    def fail(url):
        raise Exception("feed err")

    monkeypatch.setattr(news_fetcher, "fetch_feed", fail)
    mock_settings = mock.MagicMock()
    mock_settings.rss_feeds = ["https://a.com/rss"]
    monkeypatch.setattr(news_fetcher, "settings", mock_settings)
    assert news_fetcher.fetch_all_feeds() == 0


# --- main ---


def test_main(monkeypatch, capsys):
    import news_fetcher

    monkeypatch.setattr(news_fetcher, "fetch_all_feeds", lambda: 5)
    news_fetcher.main()
    assert "5 new article(s)" in capsys.readouterr().out
