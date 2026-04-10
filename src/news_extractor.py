"""News extractor — sends articles to local Ollama for structured signal extraction.

Uses the Ollama /v1/chat/completions endpoint with response_format=json_schema
to guarantee valid JSON output.  Falls back to regex-based JSON extraction if
the model does not support response_format.

CLI usage (inside the container):
    python -m news_extractor
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from config.settings import settings
from db import NewsSignal, db_conn, get_conn
from ollama_client import post_chat_completion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a financial news extraction tool. "
    "Extract structured data from the article. "
    "If the article is not about a specific publicly traded company, "
    "set tickers to empty array and sentiment to neutral."
)

_MAX_ARTICLE_CHARS = 2000


# ---------------------------------------------------------------------------
# Extraction schema (used for LLM response_format)
# ---------------------------------------------------------------------------


class ExtractedSignal(BaseModel):
    """Structured signal extracted from a news article by the LLM."""

    tickers: list[str]
    sentiment: Literal["positive", "negative", "neutral"]
    catalyst: Literal[
        "earnings", "regulation", "product", "macro", "leadership", "acquisition", "none"
    ]
    timeframe: Literal["days", "weeks", "months", "years"]
    summary: str


# ---------------------------------------------------------------------------
# Ollama API helper
# ---------------------------------------------------------------------------


def _parse_signal_from_content(content: str) -> ExtractedSignal:
    """Parse an ExtractedSignal from a raw LLM response string.

    Handles:
    1. Plain JSON
    2. Markdown code blocks (```json ... ```)
    3. JSON embedded anywhere in prose
    """
    # Attempt 1: direct parse
    try:
        return ExtractedSignal.model_validate_json(content)
    except (ValidationError, ValueError):
        pass

    # Attempt 2: strip markdown code block
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if md_match:
        try:
            return ExtractedSignal.model_validate_json(md_match.group(1).strip())
        except (ValidationError, ValueError):
            pass

    # Attempt 3: extract first {...} block in the string
    brace_match = re.search(r"\{[\s\S]*\}", content)
    if brace_match:
        try:
            raw = json.loads(brace_match.group(0))
            return ExtractedSignal.model_validate(raw)
        except (ValidationError, ValueError, json.JSONDecodeError):
            pass

    raise ValueError(f"Could not parse ExtractedSignal from LLM response: {content[:200]!r}")


def call_ollama(article_text: str, temperature: float = 0.1) -> ExtractedSignal:
    """Extract structured signal from article text using the local Ollama LLM.

    Args:
        article_text: Raw article text to analyse (truncated to _MAX_ARTICLE_CHARS).
        temperature: Sampling temperature; lower = more deterministic.

    Returns:
        Parsed ExtractedSignal.
    """
    truncated = article_text[:_MAX_ARTICLE_CHARS]

    payload: dict = {
        "model": settings.ollama_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": truncated},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedSignal",
                "schema": ExtractedSignal.model_json_schema(),
            },
        },
    }

    content = post_chat_completion(payload)
    return _parse_signal_from_content(content)


def score_confidence(article_text: str) -> tuple[ExtractedSignal, float]:
    """Run extraction 3 times at different temperatures and compute a confidence score.

    Confidence rules:
    - All 3 agree on sentiment AND tickers -> 0.9
    - All 3 agree on one but not both     -> 0.6
    - Disagree on both                    -> 0.3

    Returns:
        (signal_from_lowest_temperature, confidence_score)
    """
    temperatures = [0.1, 0.3, 0.5]
    results: list[ExtractedSignal] = []

    for temp in temperatures:
        try:
            signal = call_ollama(article_text, temperature=temp)
            results.append(signal)
        except Exception as exc:
            log.warning("Extraction at temperature %.1f failed: %s", temp, exc)

    if not results:
        raise RuntimeError("All Ollama extraction attempts failed")

    # Use the most-deterministic result as the canonical one
    canonical = results[0]

    if len(results) < 2:
        return canonical, 0.3

    # Check agreement across all available results
    sentiments = {r.sentiment for r in results}
    # Normalise tickers to frozensets for comparison
    ticker_sets = [frozenset(t.upper() for t in r.tickers) for r in results]
    tickers_agree = len(set(ticker_sets)) == 1

    sentiment_agree = len(sentiments) == 1

    if sentiment_agree and tickers_agree:
        confidence = 0.9
    elif sentiment_agree or tickers_agree:
        confidence = 0.6
    else:
        confidence = 0.3

    return canonical, confidence


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_unprocessed_articles() -> list[dict]:
    """Return all articles where processed = 0."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, content_snippet FROM news_articles WHERE processed = 0"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _store_signal(
    article_id: int,
    signal: ExtractedSignal,
    confidence: float,
) -> None:
    """Persist an extracted signal to the news_signals table."""
    now = datetime.now(tz=UTC).isoformat()
    db_signal = NewsSignal(
        article_id=article_id,
        tickers=signal.tickers,
        sentiment=signal.sentiment,
        catalyst=signal.catalyst,
        timeframe=signal.timeframe,
        summary=signal.summary,
        confidence=confidence,
        extracted_at=datetime.now(tz=UTC),
    )

    with db_conn() as conn:
        conn.execute(
            "INSERT INTO news_signals"
            " (article_id, tickers, sentiment, catalyst, timeframe, summary,"
            "  confidence, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                db_signal.article_id,
                db_signal.tickers_json(),
                db_signal.sentiment,
                db_signal.catalyst,
                db_signal.timeframe,
                db_signal.summary,
                db_signal.confidence,
                now,
            ),
        )
        conn.execute(
            "UPDATE news_articles SET processed = 1 WHERE id = ?",
            (article_id,),
        )


def process_unprocessed(use_confidence_scoring: bool = False) -> int:
    """Process all unprocessed news articles through Ollama.

    Args:
        use_confidence_scoring: If True, run 3x extraction for better
            confidence scores (3x slower).

    Returns:
        Count of articles successfully processed.
    """
    articles = _get_unprocessed_articles()
    if not articles:
        log.info("No unprocessed articles found.")
        return 0

    log.info(
        "Processing %d articles (confidence_scoring=%s)",
        len(articles),
        use_confidence_scoring,
    )
    processed_count = 0

    for article in articles:
        article_id = article["id"]
        text = f"{article.get('title', '')}\n\n{article.get('content_snippet', '')}"

        start = time.monotonic()
        try:
            if use_confidence_scoring:
                signal, confidence = score_confidence(text)
            else:
                signal = call_ollama(text)
                confidence = 0.0  # not scored

            _store_signal(article_id, signal, confidence)
            elapsed = time.monotonic() - start
            log.info(
                "article_id=%d tickers=%s sentiment=%s (%.2fs)",
                article_id,
                signal.tickers,
                signal.sentiment,
                elapsed,
            )
            processed_count += 1
        except Exception as exc:
            elapsed = time.monotonic() - start
            log.error(
                "Failed to process article_id=%d (%.2fs): %s",
                article_id,
                elapsed,
                exc,
            )
            # Continue — never crash on a single article failure

    log.info("Processed %d/%d articles", processed_count, len(articles))
    return processed_count


def filter_relevant_signals(portfolio_tickers: list[str]) -> list[NewsSignal]:
    """Return signals that mention tickers from the portfolio.

    Args:
        portfolio_tickers: List of ticker symbols held in the portfolio.

    Returns:
        List of NewsSignal objects whose tickers overlap with portfolio_tickers.
    """
    upper_tickers = {t.upper() for t in portfolio_tickers}
    if not upper_tickers:
        return []

    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM news_signals ORDER BY extracted_at DESC").fetchall()
    finally:
        conn.close()

    relevant: list[NewsSignal] = []
    for row in rows:
        try:
            signal_tickers = set(json.loads(row["tickers"] or "[]"))
            # Normalise to uppercase for comparison
            normalised = signal_tickers | {t.upper() for t in signal_tickers}
            if normalised & upper_tickers:
                relevant.append(
                    NewsSignal(
                        id=row["id"],
                        article_id=row["article_id"],
                        tickers=list(signal_tickers),
                        sentiment=row["sentiment"],
                        catalyst=row["catalyst"],
                        timeframe=row["timeframe"],
                        summary=row["summary"],
                        confidence=row["confidence"],
                        extracted_at=datetime.fromisoformat(row["extracted_at"])
                        if row["extracted_at"]
                        else None,
                    )
                )
        except Exception as exc:
            log.warning("Skipping malformed signal row id=%s: %s", row["id"], exc)

    return relevant


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Process all unprocessed articles and print a summary."""
    count = process_unprocessed(use_confidence_scoring=False)
    print(f"Done. {count} article(s) processed.")


if __name__ == "__main__":
    main()
