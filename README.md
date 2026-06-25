# LocalOwl

[![Stars](https://img.shields.io/github/stars/EOSKILLZ/LocalOwl?style=flat&color=f59e0b)](https://github.com/EOSKILLZ/LocalOwl/stargazers)
[![Forks](https://img.shields.io/github/forks/EOSKILLZ/LocalOwl?style=flat&color=555)](https://github.com/EOSKILLZ/LocalOwl/forks)
[![License](https://img.shields.io/github/license/EOSKILLZ/LocalOwl?style=flat)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat)](https://python.org)

Self-hosted AI code reviewer. Monitors your GitHub repos and posts structured PR reviews using a local LLM via LM Studio. No cloud, no subscriptions, no data leaving your machine.

---

## Requirements

- Python 3.10+
- [LM Studio](https://lmstudio.ai) with a model loaded and the local server running on port 1234
- A GitHub App (recommended) or personal access token

## Setup

```bash
git clone https://github.com/EOSKILLZ/LocalOwl
cd LocalOwl
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your credentials, then:

```bash
python main.py
```

Open a PR on any configured repo and a review posts within the next poll cycle.

## GitHub App setup

Go to `github.com/settings/apps` → New GitHub App.

**Permissions required:**
- Contents: Read
- Pull requests: Read & Write
- Issues: Read (required for comment commands via webhook)

**Webhook events** (if using webhook mode):
- Pull requests
- Issues

Download the private key into this folder and install the app on your repos. Set `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY_PATH` in `.env`.

A personal token (`GITHUB_TOKEN`) works too but hits a lower rate limit and doesn't support webhook mode.

## Webhook mode

Webhook mode reviews PRs instantly on open/push instead of on a timer.

1. Set `WEBHOOK_SECRET` to a random string (`openssl rand -hex 32`)
2. Expose the bot publicly (reverse proxy, ngrok, etc.)
3. Point your GitHub App webhook at `https://yourdomain/webhook`
4. LocalOwl listens on `WEBHOOK_PORT` (default 8090)

In webhook mode, `GITHUB_REPO` is optional — the bot reviews any repo where the GitHub App is installed.

## Comment commands

In any PR comment, tag the bot to trigger a command:

| Command | What it does |
|---|---|
| `@diffowlbot review` | Run a full review on the current PR state |
| `@diffowlbot explain` | Plain-language explanation of what the PR does |
| `@diffowlbot summarize` | 3–5 bullet point summary of key changes |

## Models

Anything running in LM Studio works. Recommendations:

| Model | RAM | Context | Notes |
|---|---|---|---|
| Liquid LFM2.5-1.2B | 1.5 GB | 128K | Recommended — fast, low RAM |
| IBM Granite 4 Tiny | 3 GB | 128K | Good quality, still lightweight |
| Mistral 7B | 5 GB | 32K | Strong reviews, needs more RAM |

## Config

All options are in `.env.example`. The main ones:

| Variable | Default | What it does |
|---|---|---|
| `POLL_INTERVAL` | `300` | Seconds between checks (polling mode) |
| `SKIP_DRAFT_PRS` | `true` | Ignore draft PRs |
| `RECHECK_UPDATED_PRS` | `true` | Re-review when new commits are pushed |
| `MAX_DIFF_CHARS` | `400000` | Max diff size sent to the model |
| `IGNORE_FILE_PATTERNS` | `*.lock,...` | Files to skip in diffs |
| `AUTO_APPROVE` | `false` | Submit formal APPROVE/REQUEST\_CHANGES reviews by verdict; off = always COMMENT |

## Formal reviews

When `AUTO_APPROVE=true`, LocalOwl submits a formal GitHub review (visible in the PR review timeline) with the appropriate event:

- **✅ Approve** verdict → `APPROVE`
- **❌ Request changes** verdict → `REQUEST_CHANGES`
- **⚠️ Approve with suggestions** → `COMMENT`

This lets you use LocalOwl as a required reviewer in branch protection rules. With `AUTO_APPROVE=false` (default), all reviews are posted as `COMMENT` events and never block merges.

## Per-repo config

Add a `.localowl.yml` to a repo root to customise the review style for that repo:

```yaml
tone: strict        # strict | balanced | lenient
style: concise      # detailed | concise
focus:              # any subset of the list below
  - bugs
  - security
  - performance
  - code-quality
ignore_patterns:
  - "migrations/*"
  - "*.generated.ts"
custom_instructions: "Always check for missing database indexes."
```

## License

MIT
