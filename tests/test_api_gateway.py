import logging

import requests
from src.api_gateway import LMStudioClient


class _FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text or "{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._data


def _client(post_impl, monkeypatch):
    client = LMStudioClient()
    monkeypatch.setattr("src.api_gateway.time.sleep", lambda s: None)
    client._session.post = post_impl
    return client


def test_chat_returns_stripped_content(monkeypatch):
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResp(200, {"choices": [{"message": {"content": "  great review  "}}]})

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user") == "great review"
    assert calls["n"] == 1


def test_chat_retries_on_429(monkeypatch):
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, text="rate limited")
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user", retries=3) == "ok"
    assert calls["n"] == 2


def test_chat_retries_on_5xx_until_exhausted(monkeypatch):
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResp(500, text="server error")

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user", retries=3) == ""
    assert calls["n"] == 3


def test_chat_does_not_retry_on_400(monkeypatch):
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResp(400, text="bad request")

    client = _client(post, monkeypatch)
    monkeypatch.setattr(client, "_list_chat_models", lambda: [])
    assert client.chat("sys", "user", retries=3) == ""
    assert calls["n"] == 1


def test_chat_auto_resolves_model_on_400(monkeypatch):
    calls = {"n": 0}
    posted_models = []

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        posted_models.append(json["model"])
        if calls["n"] == 1:
            return _FakeResp(400, text='model "local" not found')
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    client = _client(post, monkeypatch)
    monkeypatch.setattr(
        client, "_list_chat_models", lambda: ["liquid/lfm2.5-1.2b", "some-embed-model"]
    )
    assert client.chat("sys", "user") == "ok"
    assert calls["n"] == 2
    assert posted_models == ["local", "liquid/lfm2.5-1.2b"]
    assert client._auto_model_resolved is True


def test_chat_does_not_auto_resolve_with_no_loaded_models(monkeypatch):
    def post(url, json=None, timeout=None):
        return _FakeResp(400, text="unknown model")

    client = _client(post, monkeypatch)
    monkeypatch.setattr(client, "_list_chat_models", lambda: [])
    assert client.chat("sys", "user", retries=2) == ""


def test_chat_recovers_from_timeout(monkeypatch):
    calls = {"n": 0}

    def post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout()
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user", retries=2) == "ok"
    assert calls["n"] == 2


def test_chat_returns_empty_on_connection_error(monkeypatch):
    def post(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("refused")

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user", retries=1) == ""


def test_chat_handles_malformed_response(monkeypatch):
    def post(url, json=None, timeout=None):
        return _FakeResp(200, {"unexpected": True})

    client = _client(post, monkeypatch)
    assert client.chat("sys", "user") == ""


def test_logger_created_without_crashing():
    logger = logging.getLogger("localowl.api")
    assert logger.name == "localowl.api"
