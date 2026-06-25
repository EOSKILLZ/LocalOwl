import hashlib
import logging
import threading
import requests
from datetime import datetime, timezone
from .api_gateway import GitHubClient
from . import config

log = logging.getLogger("localowl.commenter")

_VERDICT_LABELS = {
    "approve":     ("✅", "Approved"),
    "suggestions": ("⚠️", "Approve with suggestions"),
    "changes":     ("❌", "Changes requested"),
    "unknown":     ("🔍", "Reviewed"),
}

_ISSUE_HEADERS = [
    "🐛 Bugs & Logic Errors",
    "🔒 Security",
    "⚡ Performance",
    "🧹 Code Quality",
]

_NO_ISSUE_PHRASES = [
    "no bugs found", "no security issues", "no performance concerns",
    "no code quality concerns", "none found", "_no ", "no issues",
]


def _verdict_to_review_event(verdict: str, enforce: bool) -> str:
    if not enforce:
        return "COMMENT"
    if verdict == "approve":
        return "APPROVE"
    if verdict == "changes":
        return "REQUEST_CHANGES"
    return "COMMENT"


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


def _extract_issue_sections(review_text: str) -> list[tuple[str, str]]:
    found = []
    for header in _ISSUE_HEADERS:
        marker = f"## {header}"
        if marker not in review_text:
            continue
        after = review_text.split(marker, 1)[1]
        content = after.split("\n## ", 1)[0].strip()
        if not any(p in content.lower() for p in _NO_ISSUE_PHRASES):
            found.append((header, content))
    return found


def _generate_fix_prompt(pr_title: str, issue_sections: list[tuple[str, str]]) -> str:
    if not issue_sections:
        return ""
    lines = [
        f'Fix the following code review issues in the PR "{pr_title}".',
        "",
        "Issues found:",
    ]
    for header, content in issue_sections:
        lines.append(f"\n### {header}")
        lines.append(content)
    lines += [
        "",
        "For each issue: identify the exact file and line, provide the corrected "
        "code snippet, and explain the fix in one sentence. Do not change anything "
        "outside the scope of these issues.",
    ]
    return "\n".join(lines)


class PRCommenter:
    def __init__(self, github_client: GitHubClient = None):
        self.github = github_client or GitHubClient()

    def post_review_comment(
        self,
        repo_name: str,
        pr_number: int,
        pr_title: str,
        review_text: str,
        pr_meta: dict | None = None,
        incremental: bool = False,
    ) -> bool:
        log.info("Posting%s review on %s PR #%d", " incremental" if incremental else "", repo_name, pr_number)
        comment = self._format_comment(repo_name, pr_number, pr_title, review_text, pr_meta, incremental)
        verdict = _parse_verdict(review_text)
        event   = _verdict_to_review_event(verdict, self._should_auto_approve())
        review_id = self.github.submit_pr_review(repo_name, pr_number, event, body=comment)
        if review_id is None:
            log.error("Failed to submit review on %s PR #%d", repo_name, pr_number)
            return False
        log.info("[%s] PR #%d — %s review submitted", repo_name, pr_number, event)
        self._ping_stats()
        return True

    def post_plain_comment(self, repo_name: str, pr_number: int, body: str) -> bool:
        comment_id = self.github.post_comment(repo_name, pr_number, body)
        return comment_id is not None

    def _should_auto_approve(self) -> bool:
        if config.AUTO_APPROVE:
            return True
        try:
            from . import database as db
            return bool(db.get_settings().get("auto_approve", False))
        except ImportError:
            return False
        except Exception:
            return False

    def _format_comment(
        self,
        repo_name: str,
        pr_number: int,
        pr_title: str,
        review_text: str,
        pr_meta: dict | None,
        incremental: bool = False,
    ) -> str:
        verdict = _parse_verdict(review_text)
        emoji, label = _VERDICT_LABELS.get(verdict, ("🔍", "Reviewed"))
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        pr_url = f"https://github.com/{repo_name}/pull/{pr_number}"

        meta = pr_meta or {}
        meta_parts = [f"[#{pr_number} · {pr_title}]({pr_url})"]
        if incremental:
            meta_parts.append("🔄 re-review · new commits only")
        if meta.get("changed_files") is not None:
            additions = meta.get("additions", 0)
            deletions = meta.get("deletions", 0)
            meta_parts.append(
                f"{meta['changed_files']} file{'s' if meta['changed_files'] != 1 else ''} &nbsp;·&nbsp; "
                f"+{additions} / -{deletions}"
            )
        if meta.get("author"):
            meta_parts.append(f"@{meta['author']}")

        review_body = (review_text or "").strip()
        if not review_body or review_body.lower() in ("none", "n/a"):
            review_body = "> No issues found — looks good! ✅"

        issue_sections = _extract_issue_sections(review_body)
        fix_prompt = _generate_fix_prompt(pr_title, issue_sections)

        review_label = "Re-review" if incremental else "Review"
        parts = [
            f"## 🦉 LocalOwl {review_label} &nbsp; {emoji} {label}",
            "",
            f"> {'&nbsp;·&nbsp; '.join(meta_parts)}",
            "",
            "---",
            "",
            review_body,
            "",
            "---",
        ]

        if fix_prompt:
            parts += [
                "",
                "<details>",
                "<summary>🤖 Fix these issues with AI — expand for prompt</summary>",
                "<br>",
                "",
                "Paste into your AI assistant (Copilot, Cursor, Claude, etc.):",
                "",
                "````text",
                fix_prompt,
                "````",
                "",
                "</details>",
            ]

        parts += [
            "",
            f"<sub>🦉 [LocalOwl](https://github.com/EOSKILLZ/LocalOwl) &nbsp;·&nbsp; {timestamp}</sub>",
        ]

        return "\n".join(parts)

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
