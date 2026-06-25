import json
import logging
import os
import time
from pathlib import Path
from .api_gateway import GitHubClient
from .config import GITHUB_REPOS, POLL_INTERVAL, STATE_FILE, SKIP_DRAFT_PRS, RECHECK_UPDATED_PRS, IGNORE_REPOS

log = logging.getLogger("localowl.monitor")

# sentinel for pre-SHA-tracking state entries — treated as unreviewed on next push
_LEGACY_SHA = ""


class PullRequestMonitor:
    def __init__(
        self,
        repo_names: list = None,
        poll_interval: int = POLL_INTERVAL,
        github_client: GitHubClient = None,
    ):
        self.repo_names    = repo_names or GITHUB_REPOS
        self.poll_interval = poll_interval
        self.github        = github_client or GitHubClient()
        self._state_path   = Path(STATE_FILE)
        self._state: dict[str, dict[str, str]] = self._load_state()

    def start_monitoring(self, callback):
        repos = [r for r in self._resolve_repos() if r not in IGNORE_REPOS]
        if not repos:
            log.error("No repositories to monitor — set GITHUB_REPO in .env")
            return

        if IGNORE_REPOS:
            log.info("Ignoring repo(s): %s", ", ".join(sorted(IGNORE_REPOS)))
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

    def _get_actionable_prs(self, repo: str) -> list[tuple]:
        prs        = self.github.get_pull_requests(repo, state="open")
        repo_state = self._state.get(repo, {})
        actionable = []

        for pr in prs:
            if SKIP_DRAFT_PRS and pr.draft:
                log.debug("[%s] Skipping draft PR #%d", repo, pr.number)
                continue

            key        = str(pr.number)
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
                owner    = pattern.split("/")[0]
                expanded = self.github.get_repos_by_owner(owner)
                resolved.extend(expanded)
                for r in expanded:
                    self._state.setdefault(r, {})
            else:
                resolved.append(pattern)
                self._state.setdefault(pattern, {})
        return resolved

    def _load_state(self) -> dict[str, dict[str, str]]:
        if not self._state_path.exists():
            return {}
        try:
            raw   = json.loads(self._state_path.read_text())
            state: dict[str, dict[str, str]] = {}
            for repo, value in raw.items():
                if isinstance(value, list):
                    # migrate list-of-ints → sha-keyed dict
                    state[repo] = {str(n): _LEGACY_SHA for n in value}
                else:
                    state[repo] = value
            total = sum(len(v) for v in state.values())
            log.info("Loaded state: %d reviewed PR(s) across %d repo(s)", total, len(state))
            return state
        except Exception as e:
            log.warning("Could not load state file — starting fresh: %s", e)
            return {}

    def _save_state(self):
        # write-then-rename for atomicity — crash-safe on POSIX
        tmp = self._state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._state, indent=2))
            os.replace(tmp, self._state_path)
        except Exception as e:
            log.warning("Could not save state: %s", e)
            tmp.unlink(missing_ok=True)
