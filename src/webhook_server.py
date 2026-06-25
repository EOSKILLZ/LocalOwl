import hashlib
import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from .config import IGNORE_REPOS, BOT_HANDLE

log = logging.getLogger("localowl.webhook")

_HANDLED_ACTIONS = frozenset({"opened", "synchronize", "ready_for_review"})
_BOT_COMMANDS    = frozenset({"review", "explain", "summarize"})


def _parse_bot_command(body: str) -> str | None:
    m = re.search(rf'@{re.escape(BOT_HANDLE)}\s+(\w+)', body, re.IGNORECASE)
    if m and m.group(1).lower() in _BOT_COMMANDS:
        return m.group(1).lower()
    return None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

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
                log.info("Webhook: @diffowlbot %s — %s PR #%d", command, repo, pr_number)
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
