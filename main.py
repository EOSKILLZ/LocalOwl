"""LocalOwl — AI-powered GitHub PR reviewer."""
import sys
from src.config import setup_logging, validate_config, GITHUB_REPOS, POLL_INTERVAL
from src.api_gateway import GitHubClient
from src.pr_monitor import PullRequestMonitor
from src.review_engine import ReviewEngine
from src.commenter import PRCommenter

log = setup_logging()


class LocalOwl:
    """Polls GitHub repos for new/updated PRs, reviews them, and posts comments."""

    def __init__(self, repo_names: list = None):
        self.repo_names = repo_names or GITHUB_REPOS

        # Single shared GitHubClient — one connection pool, one rate-limit counter
        shared_gh = GitHubClient()

        self.monitor = PullRequestMonitor(self.repo_names, github_client=shared_gh)
        self.review_engine = ReviewEngine()
        self.commenter = PRCommenter(github_client=shared_gh)

    def process_pull_request(self, repo: str, pull_request):
        log.info("[%s] Processing PR #%d: %s", repo, pull_request.number, pull_request.title)

        result = self.review_engine.analyze_pr(pull_request)

        if result["status"] == "success":
            posted = self.commenter.post_review_comment(
                repo, pull_request.number, result["review"]
            )
            if posted:
                log.info("[%s] PR #%d — review posted", repo, pull_request.number)
            else:
                log.error("[%s] PR #%d — review generated but failed to post", repo, pull_request.number)
        else:
            log.error("[%s] PR #%d — review failed: %s", repo, pull_request.number, result["review"])

    def start(self):
        log.info("=" * 55)
        log.info("  🦉 LocalOwl — AI PR Review Tool")
        log.info("=" * 55)

        if not validate_config(log):
            log.error("Aborting due to configuration errors")
            sys.exit(1)

        # Reuse the LM Studio client already inside ReviewEngine — no second instance
        if not self.review_engine.lm.health_check():
            log.error("LM Studio is not reachable — start LM Studio and load a model, then retry")
            sys.exit(1)

        log.info("Repos   : %s", ", ".join(self.repo_names))
        log.info("Interval: %ds", POLL_INTERVAL)
        self.monitor.start_monitoring(self.process_pull_request)


if __name__ == "__main__":
    LocalOwl().start()
