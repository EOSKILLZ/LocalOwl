import sys
from src.config import (
    setup_logging, validate_config,
    GITHUB_REPOS, POLL_INTERVAL, SKIP_DRAFT_PRS,
    WEBHOOK_SECRET, WEBHOOK_PORT,
)
from src.api_gateway import GitHubClient
from src.pr_monitor import PullRequestMonitor
from src.review_engine import ReviewEngine
from src.commenter import PRCommenter

log = setup_logging()


class LocalOwl:
    def __init__(self, repo_names: list = None):
        self.repo_names = repo_names or GITHUB_REPOS
        shared_gh = GitHubClient()
        self.monitor       = PullRequestMonitor(self.repo_names, github_client=shared_gh)
        self.review_engine = ReviewEngine()
        self.commenter     = PRCommenter(github_client=shared_gh)

    def process_pull_request(self, repo: str, pull_request, since_sha: str | None = None):
        log.info("[%s] Processing PR #%d: %s", repo, pull_request.number, pull_request.title)
        repo_config = self.monitor.github.get_repo_config(repo)
        if repo_config:
            log.info(
                "[%s] .localowl.yml: tone=%s style=%s focus=%s",
                repo,
                repo_config.get("tone", "balanced"),
                repo_config.get("style", "detailed"),
                ",".join(repo_config.get("focus") or []) or "all",
            )
        result = self.review_engine.analyze_pr(pull_request, repo_config=repo_config, since_sha=since_sha)
        if result["status"] == "success":
            posted = self.commenter.post_review_comment(
                repo, pull_request.number, pull_request.title,
                result["review"], pr_meta=result.get("meta"),
                incremental=result.get("incremental", False),
            )
            if posted:
                log.info("[%s] PR #%d — review posted", repo, pull_request.number)
            else:
                log.error("[%s] PR #%d — review generated but failed to post", repo, pull_request.number)
        else:
            log.error("[%s] PR #%d — review failed: %s", repo, pull_request.number, result["review"])

    def handle_comment_command(self, repo: str, pr_number: int, command: str):
        pr = self.monitor.github.get_pull_request(repo, pr_number)
        if pr is None:
            return
        log.info("[%s] @diffowlbot %s — PR #%d", repo, command, pr_number)
        if command == "review":
            self.process_pull_request(repo, pr)
            self.monitor._mark_processed(repo, pr_number, pr.head.sha)
            self.monitor._save_state()
        elif command == "explain":
            text = self.review_engine.explain_pr(pr)
            self.commenter.post_plain_comment(repo, pr_number, f"🦉 **LocalOwl Explanation**\n\n{text}")
        elif command == "summarize":
            text = self.review_engine.summarize_pr(pr)
            self.commenter.post_plain_comment(repo, pr_number, f"🦉 **LocalOwl Summary**\n\n{text}")

    def handle_webhook_pr(self, repo: str, pr_number: int, head_sha: str, is_draft: bool):
        if SKIP_DRAFT_PRS and is_draft:
            log.info("[%s] Skipping draft PR #%d (webhook)", repo, pr_number)
            return
        prev_sha = self.monitor._state.get(repo, {}).get(str(pr_number))
        if prev_sha == head_sha:
            log.debug("[%s] PR #%d already reviewed at %s — skipping", repo, pr_number, head_sha[:7])
            return
        pr = self.monitor.github.get_pull_request(repo, pr_number)
        if pr is None:
            return
        self.process_pull_request(repo, pr, since_sha=prev_sha)
        self.monitor._mark_processed(repo, pr_number, head_sha)
        self.monitor._save_state()

    def start(self):
        log.info("=" * 55)
        log.info("  🦉 LocalOwl — AI PR Review Tool")
        log.info("=" * 55)

        if not validate_config(log):
            log.error("Aborting due to configuration errors")
            sys.exit(1)

        if not self.review_engine.lm.health_check():
            log.error("LM Studio is not reachable — start LM Studio and load a model, then retry")
            sys.exit(1)

        if WEBHOOK_SECRET:
            from src.webhook_server import WebhookServer
            log.info("Mode    : webhook (port %d)", WEBHOOK_PORT)
            log.info("Webhook : https://<your-domain>/webhook")
            log.info("Repos   : all repos with the GitHub App installed")
            server = WebhookServer(WEBHOOK_PORT, WEBHOOK_SECRET, self.handle_webhook_pr,
                                   comment_callback=self.handle_comment_command)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                log.info("Stopped")
        else:
            log.info("Mode    : polling (every %ds)", POLL_INTERVAL)
            log.info("Repos   : %s", ", ".join(self.repo_names))
            self.monitor.start_monitoring(self.process_pull_request)


if __name__ == "__main__":
    LocalOwl().start()
