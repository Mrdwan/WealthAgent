"""Stock screener — discovers new investment candidates via finvizfinance.

Filters US stocks by growth criteria, fetches fundamentals, scores each
candidate via Ollama, and stores results in the ``screener_candidates`` table.

CLI usage (inside the container):
    python -m screener
"""

import json
import logging
import re
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from config.settings import settings
from db import db_conn, get_conn
from fundamentals import fetch_fundamentals
from ollama_client import post_chat_completion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schema for Ollama scoring response
# ---------------------------------------------------------------------------


class ScoredCandidate(BaseModel):
    """Ollama's scoring output for a screener candidate."""

    score: float = Field(ge=0.0, le=10.0)
    thesis: str
    risk: str


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------


def _get_held_tickers() -> set[str]:
    """Return the set of tickers currently in the portfolio."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM holdings").fetchall()
    finally:
        conn.close()
    return {row["ticker"].upper() for row in rows}


def screen_us_stocks() -> list[str]:
    """Screen US stocks using finvizfinance programmatic filters.

    Returns up to 50 tickers passing the screen, excluding tickers already
    held in the portfolio.  Returns an empty list on failure (finvizfinance
    is fragile and can break if Finviz changes its HTML).
    """
    held = _get_held_tickers()

    try:
        from finvizfinance.screener.overview import Overview

        foverview = Overview()
        foverview.set_filter(
            filters_dict={
                "Market Cap.": "+Small (over $300mln)",
                "EPS growththis year": "Over 15%",
                "EPS growthnext 5 years": "Over 15%",
                "Return on Equity": "Over 15%",
                "Current Ratio": "Over 1",
            },
        )
        df = foverview.screener_view()
    except Exception as exc:
        log.error("finvizfinance screen failed: %s", exc)
        return []

    if df is None or df.empty:
        log.info("No stocks passed the screen.")
        return []

    tickers = df["Ticker"].tolist()[:50]
    filtered = [t for t in tickers if t.upper() not in held]
    log.info("Screen: %d passed, %d after excluding held", len(tickers), len(filtered))
    return filtered


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_SCORING_SYSTEM_PROMPT = (
    "You are a stock scoring tool. Score the candidate from 0 to 10 "
    "based on growth potential, valuation, and risk. "
    "Provide a one-sentence thesis and one-sentence main risk."
)


def _parse_scored_candidate(content: str) -> ScoredCandidate:
    """Parse a ScoredCandidate from raw Ollama response.

    Handles plain JSON, markdown code blocks, and brace extraction.
    """
    # Attempt 1: direct parse
    try:
        return ScoredCandidate.model_validate_json(content)
    except (ValidationError, ValueError):
        pass

    # Attempt 2: markdown code block
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if md_match:
        try:
            return ScoredCandidate.model_validate_json(md_match.group(1).strip())
        except (ValidationError, ValueError):
            pass

    # Attempt 3: first {...} block
    brace_match = re.search(r"\{[\s\S]*\}", content)
    if brace_match:
        try:
            raw = json.loads(brace_match.group(0))
            return ScoredCandidate.model_validate(raw)
        except (ValidationError, ValueError, json.JSONDecodeError):
            pass

    raise ValueError(f"Could not parse ScoredCandidate from LLM response: {content[:200]!r}")


def score_candidate(ticker: str, fundamentals_summary: str) -> ScoredCandidate:
    """Score a candidate ticker using the local Ollama LLM.

    Args:
        ticker: The stock ticker symbol.
        fundamentals_summary: Formatted string of the ticker's fundamentals.

    Returns:
        ScoredCandidate with score, thesis, and risk.
    """
    payload: dict = {
        "model": settings.ollama_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": f"Ticker: {ticker}\n\n{fundamentals_summary}"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ScoredCandidate",
                "schema": ScoredCandidate.model_json_schema(),
            },
        },
    }

    content = post_chat_completion(payload)
    return _parse_scored_candidate(content)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _store_candidate(
    ticker: str,
    fund: dict,
    scored: ScoredCandidate,
) -> None:
    """Persist a scored candidate to the screener_candidates table."""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO screener_candidates"
            " (ticker, screened_at, market_cap, revenue_growth, pe_ratio,"
            "  sector, country, dividend_yield, debt_to_equity,"
            "  llm_score, llm_thesis, llm_risk, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                ticker,
                datetime.now().isoformat(),
                fund.get("market_cap"),
                fund.get("revenue_growth"),
                fund.get("pe_ratio"),
                fund.get("sector"),
                fund.get("country"),
                fund.get("dividend_yield"),
                fund.get("debt_to_equity"),
                scored.score,
                scored.thesis,
                scored.risk,
            ),
        )


def _format_fundamentals_for_scoring(fund: dict) -> str:
    """Format fundamentals dict as a readable string for the scoring LLM."""
    lines = []
    for key in (
        "pe_ratio",
        "revenue_growth",
        "profit_margin",
        "free_cash_flow",
        "debt_to_equity",
        "dividend_yield",
        "market_cap",
        "sector",
        "industry",
        "country",
    ):
        val = fund.get(key)
        if val is not None:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) if lines else "  No data available"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_monthly_screen() -> int:
    """Full screening pipeline: screen -> fetch fundamentals -> score -> store.

    Returns the number of candidates scored and stored.
    """
    tickers = screen_us_stocks()
    if not tickers:
        log.info("No candidates to score.")
        return 0

    log.info("Scoring %d candidates (this may take a while)…", len(tickers))
    count = 0

    for i, ticker in enumerate(tickers, 1):
        log.info("[%d/%d] Processing %s…", i, len(tickers), ticker)

        # Fetch fundamentals via yfinance
        fund = fetch_fundamentals(ticker)
        if fund is None:
            log.warning("Skipping %s — no fundamentals data.", ticker)
            continue

        fund_dict = fund.model_dump()

        # Filter: skip high-dividend stocks
        dy = fund_dict.get("dividend_yield")
        if dy is not None and dy > settings.dividend_yield_max / 100:
            log.info("Skipping %s — dividend yield %.2f%% > max.", ticker, dy * 100)
            continue

        # Score with Ollama
        summary = _format_fundamentals_for_scoring(fund_dict)
        try:
            scored = score_candidate(ticker, summary)
        except Exception as exc:
            log.error("Failed to score %s: %s", ticker, exc)
            continue

        _store_candidate(ticker, fund_dict, scored)
        log.info("%s: score=%.1f thesis=%s", ticker, scored.score, scored.thesis[:80])
        count += 1

    log.info("Screener done: %d candidates scored and stored.", count)
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the monthly screen and print results."""
    count = run_monthly_screen()
    print(f"Screener complete. {count} candidate(s) scored and stored.")


if __name__ == "__main__":
    main()
