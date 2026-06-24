import json
import sqlite3
import logging
from pathlib import Path
from . import config

log = logging.getLogger("localowl.db")

_DB_PATH: Path = Path(config.DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                repo        TEXT    NOT NULL,
                pr_number   INTEGER NOT NULL,
                pr_title    TEXT,
                pr_url      TEXT,
                verdict     TEXT    DEFAULT 'unknown',
                comment_id  INTEGER,
                reviewed_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
    log.info("Database ready at %s", _DB_PATH)


def save_review(
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    verdict: str,
    comment_id: int | None,
    reviewed_at: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO reviews
               (repo, pr_number, pr_title, pr_url, verdict, comment_id, reviewed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (repo, pr_number, pr_title, pr_url, verdict, comment_id, reviewed_at),
        )
    log.debug("Saved review: %s PR #%d verdict=%s", repo, pr_number, verdict)


def get_reviews(
    limit: int = 50,
    offset: int = 0,
    verdict: str | None = None,
    repo: str | None = None,
) -> list[dict]:
    clauses = []
    params: list = []
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    if repo:
        clauses.append("repo = ?")
        params.append(repo)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM reviews {where} ORDER BY reviewed_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def count_reviews(verdict: str | None = None, repo: str | None = None) -> int:
    clauses = []
    params: list = []
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    if repo:
        clauses.append("repo = ?")
        params.append(repo)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM reviews {where}", params).fetchone()[0]


_SETTINGS_KEY = "ai_settings"


def get_settings() -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_SETTINGS_KEY,)
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except Exception:
        return {}


def save_settings(settings: dict) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (_SETTINGS_KEY, json.dumps(settings)),
        )
