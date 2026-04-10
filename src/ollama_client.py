"""Shared Ollama API client with retry logic.

Provides a single helper for POSTing to the Ollama ``/v1/chat/completions``
endpoint with configurable retries and timeouts.  Used by both
``news_extractor`` and ``screener``.

This module has no CLI.
"""

import logging
import time

import requests

from config.settings import settings

log = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 10  # seconds between retries on connection error


def post_chat_completion(payload: dict) -> str:
    """POST *payload* to Ollama ``/v1/chat/completions`` and return the content.

    Retries up to ``_RETRY_ATTEMPTS`` times on connection errors with
    ``_RETRY_DELAY`` seconds between attempts.

    Raises:
        TimeoutError: If the request times out.
        requests.exceptions.ConnectionError: After all retries are exhausted.
    """
    url = f"{settings.ollama_base_url}/v1/chat/completions"
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.post(url, json=payload, timeout=settings.ollama_timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                log.warning(
                    "Ollama connection error (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _RETRY_ATTEMPTS,
                    _RETRY_DELAY,
                    exc,
                )
                time.sleep(_RETRY_DELAY)
            else:
                raise
        except requests.exceptions.Timeout as exc:
            raise TimeoutError(
                f"Ollama timed out after {settings.ollama_timeout}s — "
                "set OLLAMA_TIMEOUT env var to increase"
            ) from exc

    # Should not be reached, but satisfy the type checker
    raise last_exc  # type: ignore[misc]  # pragma: no cover
