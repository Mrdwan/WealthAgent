"""Unit tests for purge.py."""

from datetime import datetime, timedelta

from db import db_conn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_article(fetched_at: str, processed: int = 1) -> int:
    """Insert a news article and return its id."""
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO news_articles (url, fetched_at, processed) VALUES (?, ?, ?)",
            (f"http://example.com/{fetched_at}", fetched_at, processed),
        )
        return cur.lastrowid


def _insert_signal(article_id: int, extracted_at: str) -> None:
    """Insert a news signal linked to an article."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_signals (article_id, extracted_at) VALUES (?, ?)",
            (article_id, extracted_at),
        )


def _insert_alert(triggered_at: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO alerts_log (triggered_at, ticker, alert_type) VALUES (?, ?, ?)",
            (triggered_at, "AAPL", "price_drop"),
        )


def _count(table: str) -> int:
    with db_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# purge_old_news
# ---------------------------------------------------------------------------


def test_purge_old_news_deletes_old_signal_and_article():
    from purge import purge_old_news

    old_article = _insert_article(_ago(30))
    _insert_signal(old_article, _ago(30))

    deleted = purge_old_news(days=7)

    assert deleted == 2  # 1 signal + 1 article
    assert _count("news_signals") == 0
    assert _count("news_articles") == 0


def test_purge_old_news_keeps_recent():
    from purge import purge_old_news

    recent_article = _insert_article(_ago(3))
    _insert_signal(recent_article, _ago(3))

    deleted = purge_old_news(days=7)

    assert deleted == 0
    assert _count("news_signals") == 1
    assert _count("news_articles") == 1


def test_purge_old_news_keeps_article_referenced_by_recent_signal():
    from purge import purge_old_news

    # Article is old, but the signal pointing to it is recent
    old_article = _insert_article(_ago(30))
    _insert_signal(old_article, _ago(3))

    deleted = purge_old_news(days=7)

    # Old signal deleted = 0 (signal is recent); article kept (referenced by recent signal)
    assert deleted == 0
    assert _count("news_signals") == 1
    assert _count("news_articles") == 1


def test_purge_old_news_deletes_orphaned_old_article():
    from purge import purge_old_news

    # Article with no signal at all
    _insert_article(_ago(30))

    deleted = purge_old_news(days=7)

    assert deleted == 1
    assert _count("news_articles") == 0


def test_purge_old_news_mixed():
    from purge import purge_old_news

    old_a = _insert_article(_ago(30))
    _insert_signal(old_a, _ago(30))

    new_a = _insert_article(_ago(2))
    _insert_signal(new_a, _ago(2))

    deleted = purge_old_news(days=7)

    assert deleted == 2  # old signal + old article
    assert _count("news_signals") == 1
    assert _count("news_articles") == 1


# ---------------------------------------------------------------------------
# purge_old_alerts
# ---------------------------------------------------------------------------


def test_purge_old_alerts_deletes_old():
    from purge import purge_old_alerts

    _insert_alert(_ago(30))
    _insert_alert(_ago(3))

    deleted = purge_old_alerts(days=7)

    assert deleted == 1
    assert _count("alerts_log") == 1


def test_purge_old_alerts_keeps_all_when_recent():
    from purge import purge_old_alerts

    _insert_alert(_ago(3))
    _insert_alert(_ago(5))

    deleted = purge_old_alerts(days=7)

    assert deleted == 0
    assert _count("alerts_log") == 2


# ---------------------------------------------------------------------------
# purge_all
# ---------------------------------------------------------------------------


def test_purge_all_returns_dict_with_all_keys():
    from purge import purge_all

    result = purge_all()

    assert set(result.keys()) == {"news", "alerts"}


def test_purge_all_runs_all_purges():
    from purge import purge_all

    old_a = _insert_article(_ago(30))
    _insert_signal(old_a, _ago(30))
    _insert_alert(_ago(30))

    result = purge_all()

    assert result["news"] == 2  # signal + article
    assert result["alerts"] == 1


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_prints_summary(capsys):
    from purge import main

    main()

    out = capsys.readouterr().out
    assert "Purge complete" in out
    assert "news" in out
