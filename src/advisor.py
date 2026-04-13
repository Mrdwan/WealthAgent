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
from pathlib import Path

import requests
from pydantic import BaseModel

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

# Appended to user messages that expect a structured JSON response.
_JSON_FORMAT_INSTRUCTION = (
    "\n\nIMPORTANT: Respond with a single valid JSON object — no text outside it, "
    "no markdown fences. Use exactly these two keys:\n"
    '  "summary": one short line listing only the actions '
    '(e.g. "sell MSFT 10 shares, buy TSLA €400, hold AAPL") — max 200 chars\n'
    '  "report": the full markdown analysis as a string'
)


class AdvisorResponse(BaseModel):
    """Structured response from the advisor LLM."""

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
# LLM call
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def monthly_rebalance() -> AdvisorResponse:
    """Generate a monthly rebalance recommendation.

    Builds the full portfolio context and asks the advisor LLM for
    buy/sell/hold recommendations with reasoning.
    """
    system = _load_system_prompt()
    context = build_context()
    user_msg = (
        "Monthly rebalance review. Analyse my portfolio and give specific "
        "recommendations.\n\nPORTFOLIO STATE:\n\n" + context + _JSON_FORMAT_INSTRUCTION
    )
    return _parse_advisor_response(_call_llm(system, user_msg))


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
