import hashlib
import hmac
import http.client
import json
import threading

import requests
from src.webhook_server import _MAX_BODY_BYTES, WebhookServer, _parse_bot_command

SECRET = "test-secret"


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _start_server():
    captured = []
    event = threading.Event()

    def pr_callback(repo, pr_number, head_sha, is_draft):
        captured.append(("pr", repo, pr_number, head_sha, is_draft))
        event.set()

    def comment_callback(repo, pr_number, command):
        captured.append(("comment", repo, pr_number, command))
        event.set()

    server = WebhookServer(0, SECRET, pr_callback, comment_callback)
    port = server._server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, captured, event


def test_parse_bot_command():
    assert _parse_bot_command("@diffowlbot review") == "review"
    assert _parse_bot_command("please @diffowlbot  SUMMARIZE now") == "summarize"
    assert _parse_bot_command("@diffowlbot explain") == "explain"
    assert _parse_bot_command("@diffowlbot merge") is None
    assert _parse_bot_command("just a normal comment") is None
    assert _parse_bot_command("@someoneelse review") is None


def test_valid_pull_request_webhook_invokes_callback():
    server, port, captured, event = _start_server()
    try:
        body = json.dumps({
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "pull_request": {"number": 42, "head": {"sha": "abc123"}, "draft": False},
        }).encode()
        resp = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sig(body)},
            timeout=5,
        )
        assert resp.status_code == 200
        assert event.wait(5)
        assert captured == [("pr", "owner/repo", 42, "abc123", False)]
    finally:
        server.shutdown()


def test_issue_comment_command_webhook():
    server, port, captured, event = _start_server()
    try:
        body = json.dumps({
            "action": "created",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 7, "pull_request": {}},
            "comment": {"body": "@diffowlbot review"},
        }).encode()
        resp = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "issue_comment", "X-Hub-Signature-256": _sig(body)},
            timeout=5,
        )
        assert resp.status_code == 200
        assert event.wait(5)
        assert captured == [("comment", "owner/repo", 7, "review")]
    finally:
        server.shutdown()


def test_bad_signature_is_rejected():
    server, port, captured, event = _start_server()
    try:
        body = json.dumps({"action": "opened"}).encode()
        resp = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=deadbeef"},
            timeout=5,
        )
        assert resp.status_code == 401
        assert not event.wait(0.3)
        assert captured == []
    finally:
        server.shutdown()


def test_unknown_path_is_404():
    server, port, captured, event = _start_server()
    try:
        resp = requests.post(f"http://127.0.0.1:{port}/other", data=b"{}", timeout=5)
        assert resp.status_code == 404
    finally:
        server.shutdown()


def test_oversized_body_is_rejected():
    server, port, captured, event = _start_server()
    try:
        body = b"x" * (_MAX_BODY_BYTES + 1)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("POST", "/webhook", body=body)
        resp = conn.getresponse()
        assert resp.status == 413
        conn.close()
        assert not event.wait(0.3)
    finally:
        server.shutdown()
