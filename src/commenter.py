import hashlib
import logging
import threading
import requests
from datetime import datetime, timezone
from .api_gateway import GitHubClient
from . import config
from . import database as db

log = logging.getLogger("localowl.commenter")


def _parse_verdict(text: str) -> str:
    marker = "## ✅ Verdict"
    section = text.split(marker, 1)[-1] if marker in text else text
    if "❌" in section:
        return "changes"
    if "⚠️" in section:
        return "suggestions"
    if "✅" in section:
        return "approve"
    return "unknown"


class PRCommenter:
    def __init__(self, github_client: GitHubClient = None):
        self.github = github_client or GitHubClient()

    def post_review_comment(
        self, repo_name: str, pr_number: int, pr_title: str, review_text: str
    ) -> bool:
        log.info("Posting review on %s PR #%d", repo_name, pr_number)
        comment    = self._format_comment(review_text)
        comment_id = self.github.post_comment(repo_name, pr_number, comment)
        if comment_id is None:
            log.error("Failed to post comment on %s PR #%d", repo_name, pr_number)
            return False

        verdict = _parse_verdict(review_text)
        pr_url  = f"https://github.com/{repo_name}/pull/{pr_number}"
        now     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.save_review(repo_name, pr_number, pr_title, pr_url, verdict, comment_id, now)
        self._ping_stats()
        return True

    def _ping_stats(self) -> None:
        if not config.STATS_URL:
            return
        repos_count = len(config.GITHUB_REPOS)
        inst_hash   = hashlib.sha256(",".join(sorted(config.GITHUB_REPOS)).encode()).hexdigest()
        body        = {"event": "bot_review_posted", "user_hash": inst_hash, "repos_count": repos_count}
        threading.Thread(
            target=lambda: requests.post(config.STATS_URL, json=body, timeout=5),
            daemon=True,
        ).start()

    def _format_comment(self, review_text: str) -> str:
        review_text = (review_text or "").strip()
        timestamp   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        body        = "> No issues found — looks good! ✅" if not review_text or review_text.lower() in ("none", "n/a") else review_text
        return (
            "## 🦉 LocalOwl Review\n\n"
            f"{body}\n\n"
            "---\n"
            f"<sub>Reviewed by LocalOwl AI · {timestamp}</sub>"
        )
