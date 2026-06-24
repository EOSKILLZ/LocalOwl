"""Polls GitHub repos for new/updated PRs and fires a callback for each."""
import json
import logging
import os
import time
from pathlib import Path
from .api_gateway import GitHubClient
from .config import GITHUB_REPOS, POLL_INTERVAL, STATE_FILE, SKIP_DRAFT_PRS, RECHECK_UPDATED_PRS

log = logging.getLogger("localowl.monitor")

# Sentinel stored in state for PRs reviewed before SHA-tracking was added.
# These are treated as "already reviewed, don't re-review" until a new commit lands.
_LEGACY_SHA = ""


class PullRequestMonitor:
    """Monitors GitHub repos for new and updated pull requests."""

    def __init__(
        self,
        repo_names: list = None,
        poll_interval: int = POLL_INTERVAL,
        github_client: GitHubClient = None,
    ):
        self.repo_names = repo_names or GITHUB_REPOS
        self.poll_interval = poll_interval
        self.github = github_client or GitHubClient()
        self._state_path = Path(STATE_FILE)
        # state shape: {repo: {str(pr_number): head_sha}}
        self._state: dict[str, dict[str, str]] = self._load_state()

    # ── public ────────────────────────────────────────────────────────────────

    def start_monitoring(self, callback):
        """Poll indefinitely; call callback(repo, pr) for each actionable PR."""
        repos = self._resolve_repos()
        if not repos:
            log.error("No repositories to monitor — set GITHUB_REPO in .env")
            return

        log.info("Monitoring %d repo(s): %s", len(repos), ", ".join(repos))
        log.info("Poll interval: %ds | skip_drafts=%s | recheck_on_push=%s",
                 self.poll_interval, SKIP_DRAFT_PRS, RECHECK_UPDATED_PRS)

        cycle = 0
        try:
            while True:
                cycle += 1
                log.info("── Cycle #%d — checking %d repo(s) ──", cycle, len(repos))
                self.github.log_rate_limit()

                dirty = False
                found_total = 0
                for repo in repos:
                    prs = self._get_actionable_prs(repo)
                    for pr, reason in prs:
                        log.info("[%s] %s PR #%d: %s", repo, reason, pr.number, pr.title)
                        try:
                            callback(repo, pr)
                        except Exception:
                            log.exception("Callback error for %s PR #%d", repo, pr.number)
                        self._mark_processed(repo, pr.number, pr.head.sha)
                        dirty = True
                        found_total += 1

                if dirty:
                    self._save_state()
                    log.info("Cycle #%d done — reviewed %d PR(s)", cycle, found_total)
                else:
                    log.info("Cycle #%d done — no new PRs. Next check in %ds", cycle, self.poll_interval)

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            log.info("Monitoring stopped by user")

    # ── internals ─────────────────────────────────────────────────────────────

    def _get_actionable_prs(self, repo: str) -> list[tuple]:
        """Return list of (pr, reason_str) for PRs that need a review."""
        prs = self.github.get_pull_requests(repo, state="open")
        repo_state = self._state.get(repo, {})
        actionable = []

        for pr in prs:
            if SKIP_DRAFT_PRS and pr.draft:
                log.debug("[%s] Skipping draft PR #%d", repo, pr.number)
                continue

            key = str(pr.number)
            stored_sha = repo_state.get(key)

            if stored_sha is None:
                actionable.append((pr, "New"))
            elif RECHECK_UPDATED_PRS and stored_sha != _LEGACY_SHA and stored_sha != pr.head.sha:
                actionable.append((pr, "Updated"))

        return actionable

    def _mark_processed(self, repo: str, pr_number: int, head_sha: str):
        self._state.setdefault(repo, {})[str(pr_number)] = head_sha

    def _resolve_repos(self) -> list[str]:
        resolved = []
        for pattern in self.repo_names:
            if "*" in pattern:
                owner = pattern.split("/")[0]
                expanded = self.github.get_repos_by_owner(owner)
                resolved.extend(expanded)
                for r in expanded:
                    self._state.setdefault(r, {})
            else:
                resolved.append(pattern)
                self._state.setdefault(pattern, {})
        return resolved

    # ── state persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, dict[str, str]]:
        if not self._state_path.exists():
            log.debug("No state file — starting fresh")
            return {}
        try:
            raw = json.loads(self._state_path.read_text())
            state: dict[str, dict[str, str]] = {}
            for repo, value in raw.items():
                if isinstance(value, list):
                    # Migrate old format (list of ints) → new format (dict of sha strings)
                    state[repo] = {str(n): _LEGACY_SHA for n in value}
                    log.debug("Migrated old state for %s (%d PRs)", repo, len(value))
                else:
                    state[repo] = value
            total = sum(len(v) for v in state.values())
            log.info("Loaded state: %d reviewed PR(s) across %d repo(s)", total, len(state))
            return state
        except Exception as e:
            log.warning("Could not load state file — starting fresh: %s", e)
            return {}

    def _save_state(self):
        """Write atomically: write to a temp file then rename, so a crash can't corrupt state."""
        tmp = self._state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._state, indent=2))
            os.replace(tmp, self._state_path)  # atomic on POSIX
            log.debug("State saved (%d repos)", len(self._state))
        except Exception as e:
            log.warning("Could not save state: %s", e)
            tmp.unlink(missing_ok=True)
