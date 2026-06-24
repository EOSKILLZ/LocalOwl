"""Configuration and logging setup."""
import os
import logging
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(level: str = None) -> logging.Logger:
    log_level = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    if root.handlers:
        return logging.getLogger("localowl")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt))

    file_handler = logging.FileHandler("localowl.log")
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))

    root.setLevel(log_level)
    root.addHandler(handler)
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("github").setLevel(logging.WARNING)

    return logging.getLogger("localowl")


# ── LM Studio ─────────────────────────────────────────────────────────────────

LM_STUDIO_BASE_URL   = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
LM_STUDIO_API_KEY    = os.getenv("LM_STUDIO_API_KEY", "")
LM_STUDIO_MODEL      = os.getenv("LM_STUDIO_MODEL", "local")
LM_STUDIO_MAX_TOKENS = int(os.getenv("LM_STUDIO_MAX_TOKENS", "2000"))
LM_STUDIO_TEMPERATURE = float(os.getenv("LM_STUDIO_TEMPERATURE", "0.3"))

# ── GitHub — personal token (fallback) ────────────────────────────────────────

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "")


def get_repos() -> list[str]:
    if not GITHUB_REPO:
        return []
    return [r.strip() for r in GITHUB_REPO.split(",") if r.strip()]


GITHUB_REPOS = get_repos()

# ── GitHub App (preferred over personal token) ────────────────────────────────

GITHUB_APP_ID              = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_INSTALLATION_ID = os.getenv("GITHUB_APP_INSTALLATION_ID", "")


def _load_app_private_key() -> str:
    """Load the GitHub App private key from a file path or inline env var."""
    path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "")
    if path:
        try:
            from pathlib import Path as _Path
            return _Path(path).read_text().strip()
        except Exception as e:
            pass  # will be caught by validate_config
    # Inline PEM stored in env (replace escaped newlines)
    return os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n").strip()


GITHUB_APP_PRIVATE_KEY = _load_app_private_key()

# ── Service ───────────────────────────────────────────────────────────────────

POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", "300"))
STATE_FILE     = os.getenv("STATE_FILE", ".processed_prs.json")

# Skip pull requests that are still marked as draft
SKIP_DRAFT_PRS = os.getenv("SKIP_DRAFT_PRS", "true").lower() == "true"

# Post a fresh review when new commits are pushed to an already-reviewed PR
RECHECK_UPDATED_PRS = os.getenv("RECHECK_UPDATED_PRS", "true").lower() == "true"

# Diff extraction limits — lfm2.5-1.2b has 120K context, use it
MAX_DIFF_CHARS      = int(os.getenv("MAX_DIFF_CHARS", "30000"))
MAX_FILES_IN_DIFF   = int(os.getenv("MAX_FILES_IN_DIFF", "20"))
MAX_LINES_PER_FILE  = int(os.getenv("MAX_LINES_PER_FILE", "300"))

# Comma-separated glob patterns for files to exclude from the diff
_DEFAULT_IGNORE = (
    "*.lock,package-lock.json,yarn.lock,pnpm-lock.yaml,"
    "*.min.js,*.min.css,*.map,"
    "dist/*,build/*,.next/*,__pycache__/*"
)
IGNORE_FILE_PATTERNS: list[str] = [
    p.strip()
    for p in os.getenv("IGNORE_FILE_PATTERNS", _DEFAULT_IGNORE).split(",")
    if p.strip()
]


# ── Validation ────────────────────────────────────────────────────────────────

def validate_config(logger: logging.Logger) -> bool:
    ok = True

    # GitHub auth: App takes priority over personal token
    has_app  = bool(GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY)
    has_token = bool(GITHUB_TOKEN)
    if has_app:
        logger.info("GitHub auth: GitHub App (ID %s)", GITHUB_APP_ID)
    elif has_token:
        logger.info("GitHub auth: personal token")
    else:
        logger.error(
            "No GitHub auth configured. Set GITHUB_TOKEN for a personal token, "
            "or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_PATH for a GitHub App."
        )
        ok = False

    if not GITHUB_REPOS:
        logger.error("GITHUB_REPO is not set — no repositories to monitor")
        ok = False
    if not LM_STUDIO_BASE_URL:
        logger.error("LM_STUDIO_BASE_URL is not set")
        ok = False
    return ok
