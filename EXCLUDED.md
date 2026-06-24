# What's not in this repo

LocalOwl is open source, but the hosted service we run at [localowl.sbs](https://localowl.sbs) is built on top of this core with additional infrastructure we keep private. Here's exactly what's excluded and why.

---

## Excluded components

### Dashboard & web interface
The web dashboard at localowl.sbs — the UI for viewing review history, tweaking bot settings, and monitoring activity — is not included. It's a private frontend that talks to our backend API over the Cloudflare tunnel. You don't need it to run the bot yourself.

### Backend API server
We run a Flask API alongside the bot that stores review history in SQLite, exposes settings endpoints, and feeds the dashboard. This is internal infrastructure tied to our hosted service and isn't part of the self-hostable agent.

### OAuth worker
The GitHub OAuth flow for our dashboard is handled by a Cloudflare Worker that exchanges OAuth codes for tokens. This is tightly coupled to our app registration and deployment — it doesn't apply to self-hosted installs.

### Developer portal
An internal admin interface (`/staff/developer`) we use to monitor installs, review logs, and track usage. Purely operational tooling.

### Internal config and planning files
`CLAUDE.md`, `MARKETPLACE.md`, and any other internal docs that describe our infrastructure, credentials structure, or deployment process. These contain references to our private VPS, Cloudflare tunnel IDs, and internal service URLs.

---

## What you get

Everything you need to run a fully functional self-hosted AI PR reviewer:

- `main.py` — entry point, handles both webhook and polling modes
- `src/api_gateway.py` — GitHub API client (App auth + PAT fallback)
- `src/pr_monitor.py` — PR polling loop, state tracking
- `src/review_engine.py` — LM Studio integration, prompt construction
- `src/commenter.py` — formats and posts review comments
- `src/webhook_server.py` — HMAC-validated webhook server
- `src/config.py` — environment config
- `.env.example` — every supported env var documented

---

## If you're using our hosted service

We take security seriously. Here's how we handle your data:

**GitHub access is scoped tight.** The GitHub App requests only the permissions it needs: read access to pull request contents and write access to post comments. It cannot read your issues, merge PRs, access secrets, or touch anything outside pull requests.

**Your code never leaves your network (self-hosted).** If you're running LocalOwl yourself, your diff data goes from GitHub → your machine → your local LLM and back. We never see it.

**For the hosted service,** diffs are sent to your configured LM Studio instance. We do not log PR content, store diffs, or send code to third-party AI APIs. Review metadata (repo name, PR number, verdict) is stored locally on our VPS to power the dashboard.

**Webhook payloads are HMAC-validated.** Every incoming webhook from GitHub is verified against the shared secret before any processing happens. Requests that fail signature validation are rejected immediately.

**Credentials are never committed.** Private keys, OAuth secrets, and API tokens are stored as environment variables or Cloudflare Worker secrets — never in source code or config files checked into version control.

**The hosted service runs on a private VPS** behind a Cloudflare tunnel. No inbound ports are exposed to the public internet. All traffic flows through Cloudflare's edge.

If you have a security concern or find a vulnerability, open an issue or contact us directly.
