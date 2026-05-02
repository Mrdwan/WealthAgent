"""Telegram notifier — sends alerts and messages via the Telegram Bot API.

Uses plain ``requests`` (not python-telegram-bot) for simplicity.
If TELEGRAM_BOT_TOKEN is not set, messages are printed to stdout instead.

CLI usage (inside the container):
    python -m notifier "your message here"
"""

import logging
import sys

import requests

from alert_engine import Alert
from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_SEND_TIMEOUT = 15
_MAX_MESSAGE_LEN = 4096

# Emoji per alert type
_ALERT_EMOJI: dict[str, str] = {
    "price_drop": "\U0001f4c9",  # 📉
    "iwda_exit": "\U0001f6aa",  # 🚪
}


def _get_credentials() -> tuple[str | None, str | None]:
    """Return (bot_token, chat_id) from environment, or (None, None) if not set."""
    token = (settings.telegram_bot_token or "").strip() or None
    chat_id = (settings.telegram_chat_id or "").strip() or None
    return token, chat_id


def _split_message(text: str) -> list[str]:
    """Split a message into chunks that respect Telegram's 4096-char limit.

    Splits on newlines where possible to avoid breaking in the middle of a line.
    """
    if len(text) <= _MAX_MESSAGE_LEN:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= _MAX_MESSAGE_LEN:
            chunks.append(text)
            break
        # Try to split at last newline within the limit
        split_pos = text.rfind("\n", 0, _MAX_MESSAGE_LEN)
        if split_pos == -1:
            # No newline found — hard split
            split_pos = _MAX_MESSAGE_LEN
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


def send_message(text: str) -> None:
    """Send a text message to the configured Telegram chat.

    If TELEGRAM_BOT_TOKEN is not set, logs to stdout instead.
    Long messages are automatically split into multiple sends.
    """
    token, chat_id = _get_credentials()

    if not token:
        log.info("[NOTIFIER stdout] %s", text)
        print(f"[Telegram (not configured)] {text}")
        return

    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    for chunk in _split_message(text):
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                timeout=_SEND_TIMEOUT,
            )
            resp.raise_for_status()
            log.debug("Message sent (%d chars)", len(chunk))
        except requests.exceptions.RequestException as exc:
            log.error("Failed to send Telegram message: %s", exc)
            raise


def _format_alert(alert: Alert) -> str:
    """Format an Alert into a readable Telegram message."""
    emoji = _ALERT_EMOJI.get(alert.type, "\u26a0\ufe0f")  # ⚠️ fallback
    ticker_str = alert.ticker or "N/A"
    ts = alert.triggered_at.strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"{emoji} <b>{alert.type.upper().replace('_', ' ')}</b>",
        f"Ticker: <b>{ticker_str}</b>",
        f"Time: {ts}",
        "",
    ]

    # Type-specific detail formatting
    if alert.type == "price_drop":
        d = alert.details
        lines += [
            f"Drop: <b>{d.get('drop_pct', '?')}%</b>",
            f"Current: ${d.get('current_price_usd', '?')} ({d.get('current_date', '?')})",
            f"30 days ago: ${d.get('prior_price_usd', '?')} ({d.get('prior_date', '?')})",
            f"Threshold: {d.get('threshold_pct', '?')}%",
        ]
    elif alert.type == "iwda_exit":
        d = alert.details
        lines += [
            f"Prior rank: {d.get('prior_rank', '?')}",
            f"Current rank: {d.get('current_rank', 'absent')}",
            f"Top-N: {d.get('top_n', '?')} (exit buffer: {d.get('exit_buffer', '?')})",
        ]
    else:
        # Generic fallback — dump details
        for k, v in alert.details.items():
            lines.append(f"{k}: {v}")

    return "\n".join(lines)


def send_alert(alert: Alert) -> None:
    """Format and send an alert via Telegram."""
    text = _format_alert(alert)
    send_message(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Send a test message from the command line.

    Usage:
        python -m notifier "Hello from WealthAgent"
    """
    if len(sys.argv) < 2:
        print("Usage: python -m notifier <message>")
        sys.exit(1)
    message = " ".join(sys.argv[1:])
    send_message(message)
    print("Message sent (or printed above if token not configured).")


if __name__ == "__main__":
    main()
