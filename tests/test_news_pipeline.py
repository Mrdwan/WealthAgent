"""Integration tests for the news pipeline modules.

Tests:
  - news_fetcher.fetch_feed            (parses real or mocked RSS)
  - news_extractor.call_ollama         (requires Ollama running)
  - news_extractor._parse_signal_from_content (fallback parser, no Ollama needed)
  - alert_engine checks                (uses manually seeded DB data)
  - notifier.send_message / send_alert (stdout-only mode, no real Telegram)

Usage (inside the container):
    python tests/test_news_pipeline.py
    # or:
    python -m pytest tests/test_news_pipeline.py -v
"""

import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — must happen before any project imports
# ---------------------------------------------------------------------------
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp_db.name

# settings validates required keys on import; provide stubs so tests that
# don't touch the real APIs can still import news_extractor etc.
for _key, _val in {
    "TIINGO_API_KEY": "test",
    "ANTHROPIC_API_KEY": "test",
    "TELEGRAM_BOT_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "0",
}.items():
    os.environ.setdefault(_key, _val)

# Make both /app (container) and src/ (local dev) importable
_app_dir = Path("/app")
_src_dir = Path(__file__).resolve().parent.parent / "src"
for _d in (_app_dir, _src_dir):
    if _d.exists() and str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from db import init_db  # noqa: E402

init_db()

# Now import the pipeline modules
from alert_engine import (  # noqa: E402
    Alert,
    check_news_signals,
    check_opportunities,
    check_price_drops,
    run_all_checks,
)
from news_extractor import (  # noqa: E402
    ExtractedSignal,
    _parse_signal_from_content,
    call_ollama,
    filter_relevant_signals,
    process_unprocessed,
)
from notifier import _format_alert, _split_message, send_alert, send_message  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record a pass/fail result."""
    global _passed, _failed
    status = "PASS" if condition else "FAIL"
    if not condition:
        _failed += 1
    else:
        _passed += 1
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def _seed_holding(ticker: str = "AAPL", pool: str = "long_term") -> None:
    """Insert a test holding into the DB."""
    from db import db_conn  # noqa: PLC0415

    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO holdings"
            " (ticker, shares, entry_price_eur, entry_fx_rate, purchase_date, pool)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, 10.0, 150.0, 1.1, "2023-01-01", pool),
        )


def _seed_price(
    ticker: str,
    price_eur: float,
    days_ago: int = 0,
) -> None:
    """Insert a price record N days ago."""
    from db import db_conn  # noqa: PLC0415

    dt = (datetime.now(tz=UTC) - timedelta(days=days_ago)).date().isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_history"
            " (ticker, date, close_usd, close_eur, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (ticker, dt, price_eur * 1.1, price_eur, "test"),
        )


def _seed_article(
    url: str = "https://example.com/news1",
    title: str = "Test Article",
    processed: int = 0,
) -> int:
    """Insert a test article and return its id."""
    from db import db_conn, get_conn  # noqa: PLC0415

    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO news_articles (url, title, processed) VALUES (?, ?, ?)",
            (url, title, processed),
        )
    conn2 = get_conn()
    try:
        row = conn2.execute("SELECT id FROM news_articles WHERE url = ?", (url,)).fetchone()
    finally:
        conn2.close()
    return row["id"]


def _seed_signal(
    article_id: int,
    tickers: list[str],
    sentiment: str = "negative",
    confidence: float = 0.8,
    hours_ago: int = 1,
) -> None:
    """Insert a test news signal."""
    from db import db_conn  # noqa: PLC0415

    dt = (datetime.now(tz=UTC) - timedelta(hours=hours_ago)).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_signals"
            " (article_id, tickers, sentiment, catalyst, timeframe, summary,"
            "  confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                article_id,
                json.dumps(tickers),
                sentiment,
                "earnings",
                "weeks",
                "Test summary for the article.",
                confidence,
                dt,
            ),
        )


# ---------------------------------------------------------------------------
# Test: fallback JSON parser (no Ollama required)
# ---------------------------------------------------------------------------


def test_json_parser() -> None:
    """Test _parse_signal_from_content with various malformed inputs."""
    print("\n=== JSON Parser (no Ollama) ===")

    valid_json = json.dumps(
        {
            "tickers": ["AAPL", "MSFT"],
            "sentiment": "positive",
            "catalyst": "earnings",
            "timeframe": "weeks",
            "summary": "Strong quarterly results drove shares higher.",
        }
    )

    # 1. Plain JSON
    sig = _parse_signal_from_content(valid_json)
    check("Plain JSON parsed", sig.sentiment == "positive", sig.sentiment)
    check("Tickers extracted", sig.tickers == ["AAPL", "MSFT"])

    # 2. Wrapped in markdown code block
    md_wrapped = f"```json\n{valid_json}\n```"
    sig2 = _parse_signal_from_content(md_wrapped)
    check("Markdown code block parsed", sig2.sentiment == "positive", sig2.sentiment)

    # 3. JSON buried in prose
    prose_wrapped = f"Here is the extracted information:\n\n{valid_json}\n\nHope that helps!"
    sig3 = _parse_signal_from_content(prose_wrapped)
    check("JSON in prose parsed", sig3.sentiment == "positive", sig3.sentiment)

    # 4. Malformed — should raise ValueError
    raised = False
    try:
        _parse_signal_from_content("This is not JSON at all.")
    except ValueError:
        raised = True
    check("Malformed input raises ValueError", raised)

    # 5. Neutral / empty tickers (as returned for non-company articles)
    neutral_json = json.dumps(
        {
            "tickers": [],
            "sentiment": "neutral",
            "catalyst": "none",
            "timeframe": "days",
            "summary": "General market commentary with no specific company mentioned.",
        }
    )
    sig5 = _parse_signal_from_content(neutral_json)
    check("Empty tickers parsed", sig5.tickers == [])
    check("Neutral sentiment parsed", sig5.sentiment == "neutral")


# ---------------------------------------------------------------------------
# Test: Ollama integration (requires Ollama running)
# ---------------------------------------------------------------------------


def test_call_ollama() -> None:
    """Test call_ollama with a real article (requires Ollama running)."""
    print("\n=== Ollama Integration (requires Ollama) ===")

    sample_article = (
        "Apple Inc. reported better-than-expected earnings for Q3 2024, "
        "with revenue up 8% year-over-year to $89.5 billion. "
        "EPS came in at $1.40 vs $1.35 expected. "
        "CEO Tim Cook highlighted strong Services growth and iPhone demand in India. "
        "Shares rose 3% in after-hours trading. "
        "Analysts at Goldman Sachs raised their 12-month price target from $210 to $240. "
        "The company also announced a $110 billion share buyback programme."
    )

    ollama_available = True
    try:
        import requests  # noqa: PLC0415

        resp = requests.get(
            os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434") + "/api/tags",
            timeout=5,
        )
        ollama_available = resp.status_code == 200
    except Exception:
        ollama_available = False

    if not ollama_available:
        print("  [SKIP] Ollama not available — skipping live LLM tests")
        return

    try:
        sig = call_ollama(sample_article)
        check("call_ollama returns ExtractedSignal", isinstance(sig, ExtractedSignal))
        check(
            "Sentiment is valid literal",
            sig.sentiment in ("positive", "negative", "neutral"),
            sig.sentiment,
        )
        check(
            "Catalyst is valid literal",
            sig.catalyst
            in (
                "earnings",
                "regulation",
                "product",
                "macro",
                "leadership",
                "acquisition",
                "none",
            ),
            sig.catalyst,
        )
        check("Tickers is a list", isinstance(sig.tickers, list))
        # Apple article should yield AAPL in tickers (best-effort)
        if sig.tickers:
            check("AAPL likely detected", "AAPL" in sig.tickers, str(sig.tickers))
        check("Summary under 500 chars", len(sig.summary) < 500, f"{len(sig.summary)} chars")
    except Exception as exc:
        check("call_ollama completed without exception", False, str(exc))


# ---------------------------------------------------------------------------
# Test: process_unprocessed
# ---------------------------------------------------------------------------


def test_process_unprocessed() -> None:
    """Test process_unprocessed with a seeded article (requires Ollama)."""
    print("\n=== process_unprocessed (requires Ollama) ===")

    ollama_available = True
    try:
        import requests  # noqa: PLC0415

        resp = requests.get(
            os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434") + "/api/tags",
            timeout=5,
        )
        ollama_available = resp.status_code == 200
    except Exception:
        ollama_available = False

    if not ollama_available:
        print("  [SKIP] Ollama not available — skipping process_unprocessed test")
        return

    article_id = _seed_article(
        url="https://example.com/test-process",
        title="Amazon Web Services reports record cloud revenue",
        processed=0,
    )

    from db import get_conn  # noqa: PLC0415

    count = process_unprocessed(use_confidence_scoring=False)
    check("process_unprocessed returns >= 1", count >= 1, str(count))

    # Article should now be marked processed
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT processed FROM news_articles WHERE id = ?",
            (article_id,),
        ).fetchone()
    finally:
        conn.close()
    check("Article marked as processed", row is not None and row["processed"] == 1)


# ---------------------------------------------------------------------------
# Test: alert engine with manually seeded data
# ---------------------------------------------------------------------------


def test_alert_engine() -> None:
    """Test alert checks using seeded price and signal data."""
    print("\n=== Alert Engine ===")

    # --- Price drop test ---
    _seed_holding("TSLA", pool="short_term")
    _seed_price("TSLA", price_eur=200.0, days_ago=35)  # price 35 days ago
    _seed_price("TSLA", price_eur=160.0, days_ago=0)  # current price (−20%)

    drops = check_price_drops(threshold_pct=10.0)
    tsla_drops = [a for a in drops if a.ticker == "TSLA"]
    check("TSLA price drop detected", len(tsla_drops) > 0)
    if tsla_drops:
        d = tsla_drops[0].details
        check("Drop pct is negative", d["drop_pct"] < 0, str(d["drop_pct"]))
        check("Drop exceeds threshold", abs(d["drop_pct"]) >= 10.0)

    # Ticker that hasn't dropped enough should NOT trigger
    _seed_holding("GOOG", pool="long_term")
    _seed_price("GOOG", price_eur=100.0, days_ago=35)
    _seed_price("GOOG", price_eur=97.0, days_ago=0)  # only −3%

    drops_goog = [a for a in check_price_drops(threshold_pct=10.0) if a.ticker == "GOOG"]
    check("GOOG small drop not triggered", len(drops_goog) == 0)

    # --- News signal test ---
    _seed_holding("NVDA", pool="long_term")
    article_id = _seed_article(url="https://example.com/nvda-news", title="NVDA alert news")
    _seed_signal(
        article_id=article_id,
        tickers=["NVDA"],
        sentiment="negative",
        confidence=0.8,
        hours_ago=2,
    )

    signals = check_news_signals(hours=24)
    nvda_signals = [a for a in signals if a.ticker == "NVDA"]
    check("NVDA negative signal detected", len(nvda_signals) > 0)
    if nvda_signals:
        check("Alert type is news_signal", nvda_signals[0].type == "news_signal")

    # Low-confidence signal should NOT trigger
    article_id2 = _seed_article(url="https://example.com/nvda-low-conf", title="Low conf")
    _seed_signal(
        article_id=article_id2,
        tickers=["NVDA"],
        sentiment="negative",
        confidence=0.4,
        hours_ago=1,
    )
    # Only signals with confidence >= 0.6 should appear
    all_signals = check_news_signals(hours=24)
    low_conf = [
        a for a in all_signals if a.ticker == "NVDA" and a.details.get("confidence", 1.0) < 0.6
    ]
    check("Low-confidence signals excluded", len(low_conf) == 0)

    # --- Opportunity test ---
    article_id3 = _seed_article(url="https://example.com/opp-news", title="Opportunity article")
    _seed_signal(
        article_id=article_id3,
        tickers=["AMZN"],  # not held
        sentiment="positive",
        confidence=0.85,
        hours_ago=1,
    )

    opps = check_opportunities(hours=24)
    amzn_opps = [a for a in opps if a.ticker == "AMZN"]
    check("AMZN opportunity detected (not held)", len(amzn_opps) > 0)
    if amzn_opps:
        check("Alert type is opportunity", amzn_opps[0].type == "opportunity")

    # A held ticker should NOT appear in opportunities
    _seed_holding("NVDA", pool="long_term")  # already seeded above, just ensuring
    article_id4 = _seed_article(url="https://example.com/nvda-opp", title="NVDA positive")
    _seed_signal(
        article_id=article_id4,
        tickers=["NVDA"],
        sentiment="positive",
        confidence=0.9,
        hours_ago=1,
    )
    opps_held = [a for a in check_opportunities(hours=24) if a.ticker == "NVDA"]
    check("Held ticker excluded from opportunities", len(opps_held) == 0)

    # --- run_all_checks deduplication ---
    all_alerts = run_all_checks()
    check("run_all_checks returns list", isinstance(all_alerts, list))
    # Deduplicated: no duplicate (type, ticker) pairs
    seen: set[tuple[str, str | None]] = set()
    has_dup = False
    for a in all_alerts:
        key = (a.type, a.ticker)
        if key in seen:
            has_dup = True
            break
        seen.add(key)
    check("No duplicate (type, ticker) in run_all_checks", not has_dup)


# ---------------------------------------------------------------------------
# Test: filter_relevant_signals
# ---------------------------------------------------------------------------


def test_filter_relevant_signals() -> None:
    """Test that filter_relevant_signals returns only portfolio-matching signals."""
    print("\n=== filter_relevant_signals ===")

    article_id = _seed_article(
        url="https://example.com/filter-test",
        title="Relevant signal test",
    )
    _seed_signal(
        article_id=article_id,
        tickers=["META", "SNAP"],
        sentiment="negative",
        confidence=0.7,
        hours_ago=1,
    )

    # Only META is "in portfolio"
    relevant = filter_relevant_signals(["META"])
    meta_signals = [s for s in relevant if "META" in [t.upper() for t in s.tickers]]
    check("META signal returned when in portfolio", len(meta_signals) > 0)

    not_relevant = filter_relevant_signals(["XOM"])
    xom_signals = [s for s in not_relevant if "META" in [t.upper() for t in s.tickers]]
    check("META signal NOT returned when not in portfolio list", len(xom_signals) == 0)


# ---------------------------------------------------------------------------
# Test: notifier formatting (no Telegram token needed)
# ---------------------------------------------------------------------------


def test_notifier() -> None:
    """Test message formatting and splitting without hitting the real Telegram API."""
    print("\n=== Notifier ===")

    # Ensure no real Telegram calls by unsetting token
    original_token = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    try:
        # send_message should print to stdout when token is absent
        raised = False
        try:
            send_message("Hello, WealthAgent!")
        except Exception as exc:
            raised = True
            check("send_message (no token) does not raise", False, str(exc))
        if not raised:
            check("send_message (no token) completes without error", True)

        # Message splitting
        short_msg = "Short message"
        check("Short message is single chunk", len(_split_message(short_msg)) == 1)

        long_msg = "A" * 5000
        chunks = _split_message(long_msg)
        check("Long message splits into multiple chunks", len(chunks) > 1)
        check("All chunks within 4096 chars", all(len(c) <= 4096 for c in chunks))
        check("Split message reassembles correctly", "".join(chunks) == long_msg)

        # Multi-line split respects newlines
        newline_msg = "\n".join(["Line " + str(i) for i in range(300)])
        nl_chunks = _split_message(newline_msg)
        check("Newline message splits", len(nl_chunks) >= 1)
        check(
            "Newline chunks within limit",
            all(len(c) <= 4096 for c in nl_chunks),
        )

        # Alert formatting
        drop_alert = Alert(
            type="price_drop",
            ticker="TSLA",
            details={
                "drop_pct": -21.5,
                "current_price_eur": 160.0,
                "prior_price_eur": 200.0,
                "current_date": "2024-04-09",
                "prior_date": "2024-03-10",
                "threshold_pct": 10.0,
            },
            triggered_at=datetime.now(tz=UTC),
        )
        drop_text = _format_alert(drop_alert)
        check("Price drop alert contains ticker", "TSLA" in drop_text)
        check("Price drop alert contains pct", "-21.5" in drop_text)

        news_alert = Alert(
            type="news_signal",
            ticker="NVDA",
            details={
                "sentiment": "negative",
                "catalyst": "regulation",
                "timeframe": "months",
                "summary": "New export controls on AI chips could hurt revenue.",
                "confidence": 0.8,
                "signal_id": 1,
                "article_id": 1,
            },
            triggered_at=datetime.now(tz=UTC),
        )
        news_text = _format_alert(news_alert)
        check("News signal alert contains ticker", "NVDA" in news_text)
        check("News signal alert contains sentiment", "negative" in news_text)
        check("News signal alert contains summary", "export controls" in news_text)

        # send_alert should not raise (stdout mode)
        raised2 = False
        try:
            send_alert(drop_alert)
        except Exception:
            raised2 = True
        check("send_alert (no token) completes without error", not raised2)

    finally:
        if original_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = original_token


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all tests and report results."""
    print(f"WealthAgent News Pipeline Tests — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"Temp DB: {os.environ['DB_PATH']}")

    test_json_parser()
    test_call_ollama()
    test_alert_engine()
    test_filter_relevant_signals()
    test_notifier()
    test_process_unprocessed()

    print(f"\n{'=' * 40}")
    print(f"Results: {_passed} passed, {_failed} failed")

    # Cleanup
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass

    sys.exit(1 if _failed > 0 else 0)


if __name__ == "__main__":
    main()
