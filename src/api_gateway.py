"""API Gateway for LM Studio (chat/completions) and GitHub."""
import logging
import time
import requests
from github import Github, GithubException, GithubIntegration, Auth
from .config import (
    LM_STUDIO_BASE_URL, LM_STUDIO_API_KEY, LM_STUDIO_MODEL,
    LM_STUDIO_MAX_TOKENS, LM_STUDIO_TEMPERATURE,
    GITHUB_TOKEN, GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, GITHUB_APP_INSTALLATION_ID,
)

log = logging.getLogger("localowl.api")


class LMStudioClient:
    """Calls LM Studio via the OpenAI-compatible chat/completions endpoint."""

    def __init__(
        self,
        base_url: str = LM_STUDIO_BASE_URL,
        api_key: str = LM_STUDIO_API_KEY,
        model: str = LM_STUDIO_MODEL,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._endpoint = f"{self.base_url}/chat/completions"

        # Persistent session for connection pooling (keeps TCP alive between reviews)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

        log.debug("LMStudioClient ready — endpoint: %s", self._endpoint)

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = LM_STUDIO_MAX_TOKENS,
        temperature: float = LM_STUDIO_TEMPERATURE,
        retries: int = 3,
    ) -> str:
        """Send a chat request and return the assistant reply. Retries on transient errors."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        last_error = ""
        for attempt in range(1, retries + 1):
            try:
                log.debug("LM Studio request (attempt %d/%d)", attempt, retries)
                resp = self._session.post(self._endpoint, json=payload, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                tokens = data.get("usage", {}).get("total_tokens", "?")
                log.debug("LM Studio OK — %s tokens used", tokens)
                return text

            except requests.exceptions.ConnectionError as e:
                last_error = f"connection refused — is LM Studio running? ({e})"
                log.warning("LM Studio unreachable (attempt %d/%d): %s", attempt, retries, e)

            except requests.exceptions.Timeout:
                last_error = "request timed out"
                log.warning("LM Studio timed out (attempt %d/%d)", attempt, retries)

            except requests.exceptions.HTTPError as e:
                # 4xx errors won't recover with a retry
                log.error("LM Studio HTTP %s: %s", e.response.status_code, e)
                return ""

            except (KeyError, IndexError) as e:
                log.error("Unexpected LM Studio response shape: %s", e)
                return ""

            except Exception as e:
                log.error("LM Studio unexpected error: %s", e)
                return ""

            if attempt < retries:
                wait = 2 ** attempt
                log.info("Retrying in %ds…", wait)
                time.sleep(wait)

        log.error("LM Studio: all %d attempts failed — %s", retries, last_error)
        return ""

    def health_check(self) -> bool:
        """Return True if LM Studio is reachable and has at least one model loaded."""
        try:
            resp = self._session.get(f"{self.base_url}/models", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("data", [])
            chat_models = [m["id"] for m in models if "embed" not in m["id"].lower()]
            if chat_models:
                log.info("LM Studio loaded model(s): %s", ", ".join(chat_models))
                return True
            log.warning("LM Studio reachable but no chat models loaded")
            return False
        except Exception as e:
            log.error("LM Studio health check failed: %s", e)
            return False


class GitHubClient:
    """
    Thin wrapper around PyGithub.
    Authenticates via GitHub App (preferred) or personal token (fallback).

    GitHub App advantages:
      - 5,000 req/hr per installation (vs shared pool for personal tokens)
      - Fine-grained permissions — no access to repos outside your App
      - Tokens auto-rotate every hour (PyGithub handles this transparently)
    """

    def __init__(self):
        self.github = self._build_github()
        log.debug("GitHubClient initialised")

    @staticmethod
    def _build_github() -> Github:
        """Return an authenticated Github instance (App → token → anonymous)."""
        if GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY:
            try:
                auth = Auth.AppAuth(str(GITHUB_APP_ID), GITHUB_APP_PRIVATE_KEY)
                gi = GithubIntegration(auth=auth)

                if GITHUB_APP_INSTALLATION_ID:
                    install = gi.get_installation(int(GITHUB_APP_INSTALLATION_ID))
                else:
                    installs = list(gi.get_installations())
                    if not installs:
                        raise RuntimeError("GitHub App has no installations — install it on at least one repo")
                    install = installs[0]
                    if len(installs) > 1:
                        log.warning(
                            "Multiple App installations found (%d); using first (ID %d). "
                            "Set GITHUB_APP_INSTALLATION_ID to be explicit.",
                            len(installs), install.id,
                        )

                log.info("GitHub auth: App '%s' installation %d", GITHUB_APP_ID, install.id)
                return install.get_github_for_installation()

            except Exception as e:
                log.error("GitHub App auth failed (%s) — falling back to personal token", e)

        if GITHUB_TOKEN:
            log.info("GitHub auth: personal token")
            return Github(GITHUB_TOKEN)

        log.warning("No GitHub auth configured — unauthenticated (60 req/hr limit)")
        return Github()

    # ── pull requests ─────────────────────────────────────────────────────────

    def get_pull_requests(self, repo_name: str, state: str = "open") -> list:
        try:
            repo = self.github.get_repo(repo_name)
            # Use lazy pagination — don't call list() and load everything at once
            prs = [pr for pr in repo.get_pulls(state=state)]
            log.debug("Fetched %d %s PR(s) from %s", len(prs), state, repo_name)
            return prs
        except GithubException as e:
            log.error("GitHub API error fetching PRs from %s: %s %s", repo_name, e.status, e.data)
            return []
        except Exception as e:
            log.error("Unexpected error fetching PRs from %s: %s", repo_name, e)
            return []

    def post_comment(self, repo_name: str, pr_number: int, comment: str) -> bool:
        try:
            repo = self.github.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            pr.create_issue_comment(comment)
            log.info("Comment posted on %s PR #%d", repo_name, pr_number)
            return True
        except GithubException as e:
            log.error(
                "GitHub error posting on %s PR #%d: %s %s",
                repo_name, pr_number, e.status, e.data,
            )
            return False
        except Exception as e:
            log.error("Unexpected error posting comment: %s", e)
            return False

    def get_repos_by_owner(self, owner: str) -> list[str]:
        try:
            user = self.github.get_user(owner)
            repos = [f"{owner}/{r.name}" for r in user.get_repos()]
            log.info("Expanded wildcard for %s → %d repos", owner, len(repos))
            return repos
        except Exception as e:
            log.error("Could not list repos for owner %s: %s", owner, e)
            return []

    # ── rate limit ────────────────────────────────────────────────────────────

    def log_rate_limit(self):
        """Log remaining GitHub API rate limit. Call once per poll cycle."""
        try:
            rl = self.github.get_rate_limit().core
            log.info(
                "GitHub rate limit: %d/%d remaining (resets at %s UTC)",
                rl.remaining, rl.limit, rl.reset.strftime("%H:%M:%S"),
            )
            if rl.remaining < 50:
                log.warning("GitHub rate limit critically low: %d calls left", rl.remaining)
        except Exception as e:
            log.debug("Could not fetch rate limit: %s", e)
