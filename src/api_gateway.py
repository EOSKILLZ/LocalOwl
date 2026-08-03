import logging
import time

import requests
import yaml
from github import Auth, Github, GithubException, GithubIntegration

from .config import (
    GITHUB_APP_ID,
    GITHUB_APP_INSTALLATION_ID,
    GITHUB_APP_PRIVATE_KEY,
    GITHUB_TOKEN,
    LM_STUDIO_API_KEY,
    LM_STUDIO_BASE_URL,
    LM_STUDIO_MAX_TOKENS,
    LM_STUDIO_MODEL,
    LM_STUDIO_TEMPERATURE,
)

log = logging.getLogger("localowl.api")


class LMStudioClient:
    def __init__(
        self,
        base_url: str = LM_STUDIO_BASE_URL,
        api_key: str = LM_STUDIO_API_KEY,
        model: str = LM_STUDIO_MODEL,
    ):
        self.base_url  = base_url.rstrip("/")
        self.model     = model
        self._endpoint = f"{self.base_url}/chat/completions"
        # persistent session — amortises TCP handshake cost across reviews
        self._session  = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"
        # set once when "local" (or a stale model id) is rejected, so we can
        # fall back to the first model actually loaded in LM Studio
        self._auto_model_resolved = False

    def _list_chat_models(self) -> list[str]:
        try:
            resp = self._session.get(f"{self.base_url}/models", timeout=10)
            resp.raise_for_status()
            return [
                m["id"]
                for m in resp.json().get("data", [])
                if "embed" not in str(m.get("id", "")).lower()
            ]
        except Exception as e:
            log.debug("Could not list LM Studio models: %s", e)
            return []

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = LM_STUDIO_MAX_TOKENS,
        temperature: float = LM_STUDIO_TEMPERATURE,
        retries: int = 3,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
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
                return resp.json()["choices"][0]["message"]["content"].strip()
            except requests.exceptions.HTTPError as e:
                body = ""
                try:
                    body = e.response.text[:300]
                except Exception:
                    pass
                status = e.response.status_code
                last_error = f"HTTP {status}: {body}"
                # The default "local" alias (or a stale model id) is rejected by
                # LM Studio — auto-switch to the first model actually loaded so
                # first-time setup works without hunting for the exact id.
                if status == 400 and not self._auto_model_resolved and payload["model"] == self.model:
                    models = self._list_chat_models()
                    if models:
                        self._auto_model_resolved = True
                        payload["model"] = models[0]
                        log.warning(
                            "Model '%s' was rejected — using loaded model '%s' instead. "
                            "Set LM_STUDIO_MODEL=%s in .env to pin it.",
                            self.model, models[0], models[0],
                        )
                        continue
                # 4xx other than 429 is a permanent error — no point retrying.
                # 429 and 5xx are transient (LM Studio busy / model still
                # loading) and are worth a backoff retry.
                if status < 500 and status != 429:
                    log.error("LM Studio HTTP error: %s — %s", status, body)
                    break
                log.warning("LM Studio HTTP %d (attempt %d/%d): %s",
                            status, attempt, retries, body)
            except requests.exceptions.Timeout:
                last_error = "timeout"
                log.warning("LM Studio timeout (attempt %d/%d)", attempt, retries)
            except requests.exceptions.ConnectionError:
                last_error = "connection refused"
                log.warning("LM Studio unreachable (attempt %d/%d)", attempt, retries)
            except (KeyError, IndexError) as e:
                last_error = f"malformed response: {e}"
                log.error("Unexpected LM Studio response shape: %s", e)
                break
            except Exception as e:
                last_error = str(e)
                log.error("LM Studio error: %s", e)
                break
            if attempt < retries:
                time.sleep(2 ** attempt)  # exponential backoff
        log.error("LM Studio failed after %d attempts: %s", retries, last_error)
        return ""

    def health_check(self) -> bool:
        chat_models = self._list_chat_models()
        if chat_models:
            log.info("LM Studio loaded model(s): %s", ", ".join(chat_models))
            return True
        log.warning("LM Studio unreachable or no chat models loaded")
        return False


class GitHubClient:
    def __init__(self):
        self._integration: GithubIntegration | None = None
        self.github = self._build_github()

    def _build_github(self) -> Github:
        if GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY:
            try:
                auth = Auth.AppAuth(str(GITHUB_APP_ID), GITHUB_APP_PRIVATE_KEY)
                gi   = GithubIntegration(auth=auth)
                self._integration = gi

                if GITHUB_APP_INSTALLATION_ID:
                    # AppInstallationAuth avoids get_installation(id) which changed
                    # signature in PyGithub 2.x (now requires owner+repo, not int id)
                    install_auth = Auth.AppInstallationAuth(auth, int(GITHUB_APP_INSTALLATION_ID))
                    log.info("GitHub auth: App '%s' installation %d", GITHUB_APP_ID, int(GITHUB_APP_INSTALLATION_ID))
                    return Github(auth=install_auth)
                else:
                    installs = list(gi.get_installations())
                    if not installs:
                        raise RuntimeError("GitHub App has no installations")
                    install = installs[0]
                    if len(installs) > 1:
                        log.warning(
                            "Multiple App installations (%d); using first (ID %d). "
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

        log.warning("No GitHub auth — unauthenticated (60 req/hr)")
        return Github()

    def get_app_installations(self) -> list[dict]:
        if not self._integration:
            return []
        try:
            installs = list(self._integration.get_installations())
            return [
                {
                    "id":           i.id,
                    "account":      i.raw_data.get("account", {}).get("login", "unknown"),
                    "account_type": i.raw_data.get("account", {}).get("type", "unknown"),
                    "repo_selection": i.raw_data.get("repository_selection", "unknown"),
                    "installed_at": i.raw_data.get("created_at", ""),
                }
                for i in installs
            ]
        except Exception as e:
            log.debug("Could not fetch installations: %s", e)
            return []

    def get_pull_requests(self, repo_name: str, state: str = "open") -> list:
        try:
            repo = self.github.get_repo(repo_name)
            prs  = [pr for pr in repo.get_pulls(state=state)]
            log.debug("Fetched %d %s PR(s) from %s", len(prs), state, repo_name)
            return prs
        except GithubException as e:
            log.error("GitHub API error fetching PRs from %s: %s %s", repo_name, e.status, e.data)
            return []
        except Exception as e:
            log.error("Unexpected error fetching PRs from %s: %s", repo_name, e)
            return []

    def get_pull_request(self, repo_name: str, pr_number: int):
        try:
            return self.github.get_repo(repo_name).get_pull(pr_number)
        except GithubException as e:
            log.error("GitHub error fetching %s PR #%d: %s %s", repo_name, pr_number, e.status, e.data)
            return None
        except Exception as e:
            log.error("Unexpected error fetching %s PR #%d: %s", repo_name, pr_number, e)
            return None

    def post_comment(self, repo_name: str, pr_number: int, comment: str) -> int | None:
        try:
            repo = self.github.get_repo(repo_name)
            pr   = repo.get_pull(pr_number)
            ic   = pr.create_issue_comment(comment)
            log.info("Comment posted on %s PR #%d (id=%d)", repo_name, pr_number, ic.id)
            return ic.id
        except GithubException as e:
            log.error("GitHub error posting on %s PR #%d: %s %s", repo_name, pr_number, e.status, e.data)
            return None
        except Exception as e:
            log.error("Unexpected error posting comment: %s", e)
            return None

    def get_repos_by_owner(self, owner: str) -> list[str]:
        try:
            user  = self.github.get_user(owner)
            repos = [f"{owner}/{r.name}" for r in user.get_repos()]
            log.info("Expanded wildcard for %s → %d repos", owner, len(repos))
            return repos
        except Exception as e:
            log.error("Could not list repos for owner %s: %s", owner, e)
            return []

    def get_repo_config(self, repo_name: str) -> dict | None:
        try:
            repo = self.github.get_repo(repo_name)
            f    = repo.get_contents(".localowl.yml")
            cfg  = yaml.safe_load(f.decoded_content.decode()) or {}
            log.debug("[%s] Loaded .localowl.yml", repo_name)
            return cfg
        except GithubException as e:
            if e.status != 404:
                log.debug("[%s] Could not fetch .localowl.yml: %s", repo_name, e)
            return None
        except Exception as e:
            log.debug("[%s] Could not parse .localowl.yml: %s", repo_name, e)
            return None

    def submit_pr_review(self, repo_name: str, pr_number: int, event: str, body: str = "") -> int | None:
        try:
            pr     = self.github.get_repo(repo_name).get_pull(pr_number)
            review = pr.create_review(body=body, event=event)
            log.info("Submitted %s review on %s PR #%d (id=%d)", event, repo_name, pr_number, review.id)
            return review.id
        except GithubException as e:
            log.error("GitHub error submitting %s review on %s PR #%d: %s %s", event, repo_name, pr_number, e.status, e.data)
            return None
        except Exception as e:
            log.error("Unexpected error submitting review: %s", e)
            return None

    def log_rate_limit(self):
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
