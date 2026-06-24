# LocalOwl

[![Stars](https://img.shields.io/github/stars/EOSKILLZ/LocalOwl?style=flat&color=f59e0b)](https://github.com/EOSKILLZ/LocalOwl/stargazers)
[![Forks](https://img.shields.io/github/forks/EOSKILLZ/LocalOwl?style=flat&color=555)](https://github.com/EOSKILLZ/LocalOwl/forks)
[![License](https://img.shields.io/github/license/EOSKILLZ/LocalOwl?style=flat)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue?style=flat)](https://python.org)

Self-hosted AI code reviewer. Monitors your GitHub repos and posts structured PR reviews using a local LLM via LM Studio. No cloud, no subscriptions, no data leaving your machine.

---

## Requirements

- Python 3.8+
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

Go to `github.com/settings/apps` → New GitHub App. Set Pull Requests to Read & Write and Contents to Read. Download the private key into this folder and install the app on your repos. Set `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY_PATH` in `.env`.

A personal token (`GITHUB_TOKEN`) works too but hits a lower rate limit.

## Models

Anything running in LM Studio works. Recommendations:

| Model | RAM | Context |
|---|---|---|
| Liquid LFM2.5-1.2B | 1.5 GB | 120K |
| IBM Granite 4 Tiny | 3 GB | 128K |
| Mistral 7B | 5 GB | 32K |

## Config

All options are in `.env.example`. The main ones:

| Variable | Default | What it does |
|---|---|---|
| `POLL_INTERVAL` | `300` | Seconds between checks |
| `SKIP_DRAFT_PRS` | `true` | Ignore draft PRs |
| `RECHECK_UPDATED_PRS` | `true` | Re-review on new commits |
| `MAX_DIFF_CHARS` | `30000` | Max diff size sent to the model |
| `IGNORE_FILE_PATTERNS` | `*.lock,...` | Files to skip in diffs |

## License

MIT
