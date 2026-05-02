"""Advisor — LLM-powered investment analysis via OpenAI-compatible API.

Uses raw HTTP requests to any provider that supports the
``/v1/chat/completions`` endpoint (Anthropic, OpenAI, Ollama, etc.).
Configure via ``ADVISOR_MODEL``, ``ADVISOR_API_KEY``, and
``ADVISOR_BASE_URL`` environment variables.

CLI usage (inside the container):
    python -m advisor rebalance
    python -m advisor alert "TSLA dropped 15%"
    python -m advisor analyze PLTR
"""

import json
import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel

import advisor_validator
from config.settings import settings
from context_builder import build_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent / "config" / "investment_prompt.md"

_FALLBACK_SYSTEM_PROMPT = (
    "You are a disciplined investment advisor for a European retail investor. "
    "Analyse the portfolio data provided and give specific, actionable recommendations. "
    "Structure your response with a summary, per-ticker recommendations "
    "(BUY/SELL/HOLD with reasoning), tax notes, and a watchlist."
)

_REQUEST_TIMEOUT = 300

# Appended to user messages that expect a structured JSON response (for analyze_opportunity).
_JSON_FORMAT_INSTRUCTION = (
    "\n\nIMPORTANT: Respond with a single valid JSON object — no text outside it, "
    "no markdown fences. Use exactly these two keys:\n"
    '  "summary": one short line listing only the actions '
    '(e.g. "sell MSFT 10 shares, buy TSLA €400, hold AAPL") — max 200 chars\n'
    '  "report": the full markdown analysis as a string'
)


# ---------------------------------------------------------------------------
# MonthlyRebalance schema
# ---------------------------------------------------------------------------


class GapAction(StrEnum):
    """How the portfolio weight compares to the IWDA index weight."""

    ADD = "ADD"  # underweight vs index
    HOLD = "HOLD"  # within ±10% of index weight
    OVERWEIGHT = "OVERWEIGHT"  # more than 10% over index weight
    NEW = "NEW"  # in current top-N, not yet in portfolio
    EXITED = "EXITED"  # was in top-N, now out (with hysteresis)


class SellReason(StrEnum):
    """Permitted reasons for a sell recommendation."""

    TAX_HARVESTING = "tax_harvesting"
    CATASTROPHE = "catastrophe"
    DEEP_INDEX_EXIT = "deep_index_exit"


class IwdaPosition(BaseModel):
    """A single position in the IWDA top-N index snapshot."""

    rank: int
    ticker: str
    name: str
    weight_pct: float


class GapEntry(BaseModel):
    """Portfolio weight vs IWDA index weight for one ticker."""

    ticker: str
    portfolio_pct: float  # stocks-only weight (current)
    index_pct: float  # IWDA weight
    gap_pct: float  # portfolio_pct - index_pct (negative = underweight)
    action: GapAction


class Allocation(BaseModel):
    """A single buy order within the monthly stock allocation."""

    ticker: str
    amount_eur: float  # >= 0; sum across allocations <= 1050
    rationale: str  # one short sentence


class BufferDecision(BaseModel):
    """The monthly buffer allocation decision."""

    amount_eur: float  # >= 0, <= 500
    target: str  # ticker, "commodity:silver", or descriptive string
    rationale: str


class LegacyHoldingDecision(BaseModel):
    """Advisor decision for a legacy holding not in the current IWDA top-N."""

    ticker: str
    decision: Literal["hold", "trim", "sell"]
    reason: str


class SellRecommendation(BaseModel):
    """A specific sell recommendation with tax calculations."""

    ticker: str
    shares: float
    reason: SellReason
    realized_gain_eur: float  # the LLM's estimate from context data
    cgt_due_eur: float
    net_proceeds_eur: float


class TrackingErrorReport(BaseModel):
    """30-day tracking error: portfolio stocks-only vs IWDA.L."""

    portfolio_return_pct: float | None
    iwda_return_pct: float | None
    tracking_error_pp: float | None  # portfolio - iwda; null if insufficient data
    explanation: str  # one or two sentences


class TaxSummary(BaseModel):
    """Irish CGT position for the current tax year."""

    realized_gains_ytd_eur: float
    exemption_used_eur: float
    exemption_remaining_eur: float


class MonthlyRebalance(BaseModel):
    """Structured monthly rebalance recommendation from the advisor LLM."""

    summary: str  # one short line for Telegram, max 200 chars
    report: str  # full markdown commentary; the bot still uses this
    iwda_top_n: list[IwdaPosition]
    portfolio_vs_index: list[GapEntry]
    stock_allocation: list[Allocation]
    buffer_recommendation: BufferDecision
    legacy_holdings: list[LegacyHoldingDecision]
    sell_recommendations: list[SellRecommendation]
    tracking_error: TrackingErrorReport
    tax_summary: TaxSummary


class AdvisorResponse(BaseModel):
    """Structured response from the advisor LLM (used by analyze_opportunity)."""

    summary: str
    report: str


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load the system prompt from config/investment_prompt.md.

    Falls back to an embedded default if the file does not exist.
    """
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    log.warning("Prompt file not found at %s — using fallback.", _PROMPT_PATH)
    return _FALLBACK_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------


def _call_llm(system_prompt: str, user_message: str) -> str:
    """Call the advisor LLM and return the response text.

    Logs token usage for cost tracking.  Returns a helpful error string
    (rather than raising) when configuration is missing.
    """
    if not settings.advisor_base_url:
        log.warning("ADVISOR_BASE_URL is not set — cannot call advisor LLM")
        return (
            "Advisor base URL not configured. "
            "Set ADVISOR_BASE_URL in your .env file "
            "(e.g. https://api.anthropic.com/v1)."
        )

    base = settings.advisor_base_url
    needs_key = "ollama" not in base
    if needs_key and not settings.advisor_api_key:
        log.warning("ADVISOR_API_KEY is not set for non-Ollama endpoint %s", base)
        return "Advisor API key not configured. Set ADVISOR_API_KEY in your .env file."

    url = f"{base.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.advisor_api_key:
        headers["Authorization"] = f"Bearer {settings.advisor_api_key}"

    payload: dict = {
        "model": settings.advisor_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    log.info(
        "Calling advisor LLM: model=%s url=%s system_chars=%d user_chars=%d",
        settings.advisor_model,
        url,
        len(system_prompt),
        len(user_message),
    )

    resp = requests.post(url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    # Log token usage for cost tracking
    usage = data.get("usage", {})
    log.info(
        "Advisor tokens: input=%d output=%d total=%d",
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        usage.get("total_tokens", 0),
    )

    content = data["choices"][0]["message"]["content"]
    log.info("Advisor response: %d chars", len(content))
    return content


def _call_llm_structured(
    system_prompt: str,
    user_message: str,
    schema_dict: dict,
) -> dict | None:
    """Call the advisor LLM with response_format=json_schema and return the parsed dict.

    Posts with structured output enforced via response_format.  On a requests
    exception or invalid JSON, logs a warning and returns None so the caller
    can fall back.

    Args:
        system_prompt: The system prompt text.
        user_message: The user message text.
        schema_dict: The JSON schema dict (from Pydantic's model_json_schema).

    Returns:
        Parsed dict on success, or None on error.
    """
    if not settings.advisor_base_url:
        log.warning("ADVISOR_BASE_URL is not set — cannot call advisor LLM")
        return None

    base = settings.advisor_base_url
    needs_key = "ollama" not in base
    if needs_key and not settings.advisor_api_key:
        log.warning("ADVISOR_API_KEY is not set for non-Ollama endpoint %s", base)
        return None

    url = f"{base.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.advisor_api_key:
        headers["Authorization"] = f"Bearer {settings.advisor_api_key}"

    payload: dict = {
        "model": settings.advisor_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "MonthlyRebalance",
                "schema": schema_dict,
                "strict": True,
            },
        },
    }

    log.info(
        "Calling advisor LLM (structured): model=%s url=%s",
        settings.advisor_model,
        url,
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Structured LLM call failed: %s", exc)
        return None

    data = resp.json()

    usage = data.get("usage", {})
    log.info(
        "Advisor tokens: input=%d output=%d total=%d",
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        usage.get("total_tokens", 0),
    )

    content = data["choices"][0]["message"]["content"]
    log.info("Advisor structured response: %d chars", len(content))

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse structured LLM response as JSON: %s", exc)
        return None


def _parse_advisor_response(content: str) -> AdvisorResponse:
    """Parse a JSON advisor response into an AdvisorResponse.

    Strips markdown code fences if present, then parses the JSON.
    Falls back to an empty summary with the raw content as the report on failure.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end]).strip()

    try:
        data = json.loads(text)
        return AdvisorResponse(summary=data["summary"], report=data["report"])
    except (json.JSONDecodeError, KeyError) as exc:
        log.warning("Failed to parse JSON advisor response: %s", exc)
        return AdvisorResponse(summary="", report=content)


def _inline_refs(schema: object) -> object:
    """Inline all $ref references in a JSON schema dict.

    Pydantic generates schemas with $defs and $ref pointers.  Some providers
    reject strict-mode schemas with $ref.  This function recursively resolves
    all $ref entries using the top-level $defs map and returns a flat schema.

    Args:
        schema: A JSON schema value (dict, list, or scalar).

    Returns:
        The schema with all $ref values replaced by their inlined definitions.
    """
    defs: dict = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def _resolve(node: object) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].split("/")[-1]
                resolved = defs.get(ref_name, node)
                return _resolve(resolved)
            return {k: _resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    result = _resolve(schema)
    if isinstance(result, dict):
        result.pop("$defs", None)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def monthly_rebalance() -> MonthlyRebalance:
    """Generate a monthly rebalance recommendation.

    Builds the full portfolio context, calls the advisor LLM with structured
    JSON output (response_format=json_schema), validates the result against
    DB state and strategy rules, and returns a MonthlyRebalance model.

    Validation errors are logged and appended to the summary so Phase 7 can
    surface them in Telegram.  The LLM's best-effort result is always returned
    — we never retry the LLM call.
    """
    system = _load_system_prompt()
    context = build_context()
    user_msg = (
        "Monthly rebalance review. Analyse my portfolio and produce the structured "
        "MonthlyRebalance JSON.\n\nPORTFOLIO STATE:\n\n" + context
    )

    raw_schema = MonthlyRebalance.model_json_schema()
    flat_schema = _inline_refs(raw_schema)

    parsed_dict = _call_llm_structured(system, user_msg, flat_schema)

    if parsed_dict is None:
        log.warning("Structured LLM call returned None — returning minimal fallback")
        return MonthlyRebalance(
            summary="⚠️ advisor unavailable — check configuration",
            report="Advisor LLM call failed. Check ADVISOR_BASE_URL and ADVISOR_API_KEY.",
            iwda_top_n=[],
            portfolio_vs_index=[],
            stock_allocation=[],
            buffer_recommendation=BufferDecision(
                amount_eur=0.0, target="", rationale="LLM unavailable"
            ),
            legacy_holdings=[],
            sell_recommendations=[],
            tracking_error=TrackingErrorReport(
                portfolio_return_pct=None,
                iwda_return_pct=None,
                tracking_error_pp=None,
                explanation="LLM unavailable",
            ),
            tax_summary=TaxSummary(
                realized_gains_ytd_eur=0.0,
                exemption_used_eur=0.0,
                exemption_remaining_eur=0.0,
            ),
        )

    try:
        rebalance = MonthlyRebalance.model_validate(parsed_dict)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to validate LLM response against MonthlyRebalance schema: %s", exc)
        return MonthlyRebalance(
            summary="⚠️ advisor response failed schema validation",
            report=(
                f"LLM response did not match MonthlyRebalance schema:\n{exc}"
                f"\n\nRaw response:\n{json.dumps(parsed_dict, indent=2)}"
            ),
            iwda_top_n=[],
            portfolio_vs_index=[],
            stock_allocation=[],
            buffer_recommendation=BufferDecision(
                amount_eur=0.0, target="", rationale="schema validation failed"
            ),
            legacy_holdings=[],
            sell_recommendations=[],
            tracking_error=TrackingErrorReport(
                portfolio_return_pct=None,
                iwda_return_pct=None,
                tracking_error_pp=None,
                explanation="schema validation failed",
            ),
            tax_summary=TaxSummary(
                realized_gains_ytd_eur=0.0,
                exemption_used_eur=0.0,
                exemption_remaining_eur=0.0,
            ),
        )

    errors = advisor_validator.validate(rebalance)
    if errors:
        for err in errors:
            log.warning("MonthlyRebalance validation error: %s", err)
        flagged = "; ".join(errors)
        rebalance = rebalance.model_copy(
            update={"summary": rebalance.summary + f" ⚠️ validation flagged: {flagged}"}
        )

    return rebalance


def analyze_alert(alert_details: str) -> str:
    """Analyse a triggered alert and recommend a single clear action.

    Args:
        alert_details: Human-readable description of the alert.
    """
    system = _load_system_prompt()
    context = build_context()
    user_msg = (
        "Alert triggered. Analyse and give me one clear action.\n\n"
        f"ALERT DETAILS:\n{alert_details}\n\n"
        f"PORTFOLIO STATE:\n\n{context}"
    )
    return _call_llm(system, user_msg)


def analyze_opportunity(ticker: str) -> AdvisorResponse:
    """Deep-dive analysis on a potential investment opportunity.

    Args:
        ticker: The stock or commodity ticker to analyse.
    """
    system = _load_system_prompt()
    context = build_context()
    user_msg = (
        f"Analyse this opportunity: {ticker}\n"
        "Should I add it to my portfolio? If so, how much and in which pool?\n\n"
        f"PORTFOLIO STATE:\n\n{context}" + _JSON_FORMAT_INSTRUCTION
    )
    return _parse_advisor_response(_call_llm(system, user_msg))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI dispatcher for the advisor module."""
    usage = "Usage: python -m advisor rebalance | alert <details> | analyze <TICKER>"

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "rebalance":
        resp = monthly_rebalance()
        print(resp.report)
    elif cmd == "alert":
        if len(sys.argv) < 3:
            print("Error: alert command requires details argument.")
            sys.exit(1)
        print(analyze_alert(sys.argv[2]))
    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("Error: analyze command requires a ticker argument.")
            sys.exit(1)
        resp = analyze_opportunity(sys.argv[2])
        print(resp.report)
    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
