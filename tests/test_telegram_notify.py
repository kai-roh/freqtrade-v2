"""telegram_notify: 미구성 폴백, HTTP mock, 분할 로직."""

from __future__ import annotations

import importlib
import json
import urllib.error


def _load(monkeypatch, with_creds=True):
    if with_creds:
        monkeypatch.setenv("TELEGRAM_TOKEN", "tok123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    else:
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    import sys

    sys.modules.pop("telegram_notify", None)
    import telegram_notify

    importlib.reload(telegram_notify)
    return telegram_notify


def test_is_configured_false_without_creds(monkeypatch):
    tn = _load(monkeypatch, with_creds=False)
    assert tn.is_configured() is False


def test_is_configured_true_with_creds(monkeypatch):
    tn = _load(monkeypatch)
    assert tn.is_configured() is True


def test_send_returns_false_without_creds(monkeypatch):
    tn = _load(monkeypatch, with_creds=False)
    assert tn.send("hello") is False


class _FakeResp:
    def __init__(self, body, status=200):
        self._body = body.encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_success(monkeypatch):
    tn = _load(monkeypatch)
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data.decode()
        return _FakeResp(json.dumps({"ok": True, "result": {}}))

    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert tn.send("hello world") is True
    assert "bot" in captured["url"] and "sendMessage" in captured["url"]
    assert "chat_id=456" in captured["data"]
    assert "text=hello+world" in captured["data"]
    assert "parse_mode=Markdown" in captured["data"]


def test_send_api_not_ok(monkeypatch):
    tn = _load(monkeypatch)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda r, timeout=None: _FakeResp(json.dumps({"ok": False, "description": "x"})),
    )
    assert tn.send("x") is False


def test_send_http_error(monkeypatch):
    tn = _load(monkeypatch)

    def boom(req, timeout=None):
        raise urllib.error.URLError("dns died")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert tn.send("x") is False


def test_send_passes_disable_notification(monkeypatch):
    tn = _load(monkeypatch)
    seen = {}

    def fake(req, timeout=None):
        seen["data"] = req.data.decode()
        return _FakeResp(json.dumps({"ok": True}))

    monkeypatch.setattr("urllib.request.urlopen", fake)
    tn.send("x", disable_notification=True)
    assert "disable_notification=True" in seen["data"]


def test_split_short_message_one_chunk(monkeypatch):
    tn = _load(monkeypatch)
    assert tn._split("short") == ["short"]


def test_split_long_message_multiple_chunks(monkeypatch):
    tn = _load(monkeypatch)
    body = ("line\n" * 1000).rstrip()  # ~5000 chars
    chunks = tn._split(body, limit=4096)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == body


def test_send_splits_long_message(monkeypatch):
    tn = _load(monkeypatch)
    calls = {"n": 0}

    def fake(req, timeout=None):
        calls["n"] += 1
        return _FakeResp(json.dumps({"ok": True}))

    monkeypatch.setattr("urllib.request.urlopen", fake)
    tn.send("x" * 10_000)
    assert calls["n"] >= 3
