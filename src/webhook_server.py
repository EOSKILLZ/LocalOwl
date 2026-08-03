import hashlib
import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import BOT_HANDLE, IGNORE_REPOS

log = logging.getLogger("localowl.webhook")

_HANDLED_ACTIONS = frozenset({"opened", "synchronize", "ready_for_review"})
_BOT_COMMANDS    = frozenset({"review", "explain", "summarize"})

# GitHub webhook payloads stay well under a few MB; anything larger is either
# a bug or an abuse attempt and gets rejected before we read it into memory.
_MAX_BODY_BYTES = 5 * 1024 * 1024


def _parse_bot_command(body: str) -> str | None:
    m = re.search(rf'@{re.escape(BOT_HANDLE)}\s+(\w+)', body, re.IGNORECASE)
    if m and m.group(1).lower() in _BOT_COMMANDS:
        return m.group(1).lower()
    return None


class _Handler(BaseHTTPRequestHandler):
    def _drain_body(self, length: int, cap: int) -> None:
        """Read-and-discard a rejected request body so the connection closes
        with a clean FIN instead of a reset (avoids client-side aborts)."""
        remaining = min(length, cap)
        chunk = 64 * 1024
        while remaining > 0:
            data = self.rfile.read(min(remaining, chunk))
            if not data:
                break
            remaining -= len(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length < 0 or length > _MAX_BODY_BYTES:
            if length > 0:
                self._drain_body(length, _MAX_BODY_BYTES + 64 * 1024)
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length)

        if self.path != "/webhook":
            # drain the request body before responding so the client's write
            # completes cleanly (avoids connection aborts on some platforms)
            self.send_response(404)
            self.end_headers()
            return

        # constant-time comparison prevents timing oracle on the secret
        sig      = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            self.server.secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            log.warning("Rejected webhook delivery — bad signature")
            self.send_response(401)
            self.end_headers()
            return

        event = self.headers.get("X-GitHub-Event", "")
        # ack before processing — GitHub times out at 10s
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

        if event == "pull_request":
            try:
                payload   = json.loads(body)
                action    = payload.get("action", "")
                if action not in _HANDLED_ACTIONS:
                    return
                repo      = payload["repository"]["full_name"]
                if repo in IGNORE_REPOS:
                    log.debug("Webhook: skipping %s (in IGNORE_REPOS)", repo)
                    return
                pr_number = payload["pull_request"]["number"]
                head_sha  = payload["pull_request"]["head"]["sha"]
                is_draft  = payload["pull_request"].get("draft", False)
                log.info("Webhook: %s — %s PR #%d", action, repo, pr_number)
                threading.Thread(
                    target=self.server.callback,
                    args=(repo, pr_number, head_sha, is_draft),
                    daemon=True,
                ).start()
            except Exception as e:
                log.error("Failed to handle webhook payload: %s", e)

        elif event == "issue_comment" and self.server.comment_callback:
            try:
                payload = json.loads(body)
                if payload.get("action") != "created":
                    return
                issue = payload.get("issue", {})
                if "pull_request" not in issue:
                    return  # regular issue comment, not a PR
                comment_body = payload.get("comment", {}).get("body", "")
                command = _parse_bot_command(comment_body)
                if not command:
                    return
                repo      = payload["repository"]["full_name"]
                pr_number = issue["number"]
                log.info("Webhook: @%s %s — %s PR #%d", BOT_HANDLE, command, repo, pr_number)
                threading.Thread(
                    target=self.server.comment_callback,
                    args=(repo, pr_number, command),
                    daemon=True,
                ).start()
            except Exception as e:
                log.error("Failed to handle issue_comment webhook: %s", e)

    def log_message(self, *args):
        pass


class WebhookServer:
    def __init__(self, port: int, secret: str, callback, comment_callback=None):
        self._server                  = HTTPServer(("", port), _Handler)
        self._server.secret           = secret
        self._server.callback         = callback
        self._server.comment_callback = comment_callback

    def serve_forever(self):
        log.info("Webhook server listening on :%d/webhook", self._server.server_address[1])
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()
