"""Unit tests for ollama_client.py — shared Ollama API helper with retry logic."""

from unittest import mock

import pytest
import requests as req_lib


def _ollama_response(content: str):
    resp = mock.MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_post_chat_completion_success():
    from ollama_client import post_chat_completion

    with mock.patch("ollama_client.requests.post", return_value=_ollama_response("ok")):
        assert post_chat_completion({"model": "test"}) == "ok"


def test_post_chat_completion_retry_then_succeed():
    from ollama_client import post_chat_completion

    with (
        mock.patch(
            "ollama_client.requests.post",
            side_effect=[req_lib.exceptions.ConnectionError("fail"), _ollama_response("ok")],
        ),
        mock.patch("ollama_client.time.sleep"),
    ):
        assert post_chat_completion({"model": "test"}) == "ok"


def test_post_chat_completion_all_retries_fail():
    from ollama_client import post_chat_completion

    with (
        mock.patch(
            "ollama_client.requests.post",
            side_effect=req_lib.exceptions.ConnectionError("fail"),
        ),
        mock.patch("ollama_client.time.sleep"),
        pytest.raises(req_lib.exceptions.ConnectionError),
    ):
        post_chat_completion({"model": "test"})


def test_post_chat_completion_timeout():
    from ollama_client import post_chat_completion

    with (
        mock.patch(
            "ollama_client.requests.post",
            side_effect=req_lib.exceptions.Timeout("timed out"),
        ),
        pytest.raises(TimeoutError, match="Ollama timed out"),
    ):
        post_chat_completion({"model": "test"})
