"""Posts formatted review comments to GitHub PRs."""
import logging
from datetime import datetime, timezone
from .api_gateway import GitHubClient

log = logging.getLogger("localowl.commenter")


class PRCommenter:
    """Posts LocalOwl review comments on GitHub pull requests."""

    def __init__(self, github_client: GitHubClient = None):
        self.github = github_client or GitHubClient()

    def post_review_comment(self, repo_name: str, pr_number: int, review_text: str) -> bool:
        log.info("Posting review on %s PR #%d", repo_name, pr_number)
        comment = self._format_comment(review_text)
        success = self.github.post_comment(repo_name, pr_number, comment)
        if not success:
            log.error("Failed to post comment on %s PR #%d", repo_name, pr_number)
        return success

    def _format_comment(self, review_text: str) -> str:
        review_text = (review_text or "").strip()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if not review_text or review_text.lower() in ("none", "n/a"):
            body = "> No issues found — looks good! ✅"
        else:
            body = review_text

        return (
            "## 🦉 LocalOwl Review\n\n"
            f"{body}\n\n"
            "---\n"
            f"<sub>Reviewed by LocalOwl AI · {timestamp}</sub>"
        )
