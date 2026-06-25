# 🦉 LocalOwl

[![Stars](https://img.shields.io/github/stars/EOSKILLZ/LocalOwl?style=flat&color=f59e0b)](https://github.com/EOSKILLZ/LocalOwl/stargazers)
[![Forks](https://img.shields.io/github/forks/EOSKILLZ/LocalOwl?style=flat&color=555)](https://github.com/EOSKILLZ/LocalOwl/forks)
[![License](https://img.shields.io/github/license/EOSKILLZ/LocalOwl?style=flat)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat)](https://python.org)

> Self-hosted AI code reviewer. Open a PR, get a review — bugs, security issues, and a clear approve/deny verdict, posted as a bot comment. Runs 100% locally. No cloud, no subscription, no data leaving your machine.

---

## How it works

LocalOwl connects to your GitHub repos and watches for pull requests. When one opens or gets new commits, it pulls the diff, sends it to a local LLM running in LM Studio, and posts a structured review as a GitHub comment.

That's it.

---

## Requirements

- Python 3.10+
- [LM Studio](https://lmstudio.ai) with a model loaded and the server running on port 1234
- A GitHub App or personal access token

---

## Setup

```bash
git clone https://github.com/EOSKILLZ/LocalOwl
cd LocalOwl
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your credentials
python main.py
```

---

## GitHub App setup

Go to `github.com/settings/apps` → New GitHub App and give it these permissions:

| Permission | Level |
|---|---|
| Contents | Read |
| Pull requests | Read & Write |
| Issues | Read |

Subscribe to **Pull request** and **Issue comment** webhook events. Download the private key, drop it in this folder, and set `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY_PATH` in `.env`.

> A personal token (`GITHUB_TOKEN`) works too but doesn't support webhook mode.

---

## Webhook mode

Webhook mode gives you instant reviews on PR open/push instead of polling on a timer.

1. Set `WEBHOOK_SECRET` to a random string — `openssl rand -hex 32`
2. Expose the bot publicly (reverse proxy, ngrok, etc.)
3. Point your GitHub App webhook at `https://yourdomain/webhook`

In webhook mode, `GITHUB_REPO` is optional — the bot reviews any repo the app is installed on.

---

## Comment commands

Reply to any PR comment to trigger a command:

| Command | What it does |
|---|---|
| `@yourbot review` | Full review of the current PR state |
| `@yourbot explain` | Plain-language explanation of what the PR does |
| `@yourbot summarize` | 3–5 bullet summary of key changes |

> Replace `yourbot` with your GitHub App's username. Set `BOT_HANDLE` in `.env` to match.

---

## Models

Anything that runs in LM Studio works. A few that are known good:

| Model | RAM | Notes |
|---|---|---|
| Liquid LFM2.5-1.2B | ~1.5 GB | Fast, low footprint — good starting point |
| IBM Granite 4 Tiny | ~3 GB | Better quality, still lightweight |
| Mistral 7B | ~5 GB | Strong reviews, needs more RAM |

---

## Config

All options are in `.env.example`. Key ones:

| Variable | Default | What it does |
|---|---|---|
| `POLL_INTERVAL` | `300` | Seconds between checks (polling mode only) |
| `SKIP_DRAFT_PRS` | `true` | Skip draft PRs |
| `RECHECK_UPDATED_PRS` | `true` | Re-review on new commits |
| `IGNORE_REPOS` | — | Comma-separated repos to never review |
| `AUTO_APPROVE` | `false` | Submit formal APPROVE / REQUEST_CHANGES reviews |
| `BOT_HANDLE` | _(your app name)_ | Your GitHub App's username — used to parse comment commands |

### `AUTO_APPROVE=true`

When enabled, LocalOwl submits a proper GitHub review instead of a plain comment:

- **✅ Approve** → `APPROVE`
- **❌ Request changes** → `REQUEST_CHANGES`
- **⚠️ Approve with suggestions** → `COMMENT`

This lets you add the bot as a required reviewer in branch protection rules.

---

## Per-repo config

Drop a `.localowl.yml` in the root of any repo to customise how it gets reviewed:

```yaml
tone: technical        # technical | strict | balanced | lenient
style: concise         # detailed | concise
focus:
  - bugs
  - security
  - performance
  - code-quality
ignore_patterns:
  - "migrations/*"
  - "*.generated.ts"
custom_instructions: "Always check for missing database indexes."
```

---

## License

MIT
