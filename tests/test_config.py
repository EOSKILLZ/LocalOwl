import logging
from pathlib import Path

from dotenv import dotenv_values
from src import config as cfg


def test_env_example_parses_without_bogus_keys():
    """The shipped .env.example must load cleanly — no section headers or
    stray keys sneaking in as empty environment variables."""
    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env.example")
    assert values, "expected .env.example to define variables"
    assert "[required]" not in values and "[optional]" not in values
    assert values["GITHUB_APP_ID"] == "your_app_id"
    assert values["WEBHOOK_PORT"] == "8090"


def test_parse_ignore_repos(monkeypatch):
    monkeypatch.setenv("IGNORE_REPOS", "a/b,c/d,, e/f ")
    assert cfg._parse_ignore_repos() == frozenset({"a/b", "c/d", "e/f"})


def test_default_ignore_patterns_cover_build_artifacts():
    assert "*.lock" in cfg._DEFAULT_IGNORE
    assert "dist/*" in cfg._DEFAULT_IGNORE
    assert "*.min.js" in cfg._DEFAULT_IGNORE


def test_get_repos_splits_and_strips(monkeypatch):
    monkeypatch.setattr(cfg, "GITHUB_REPO", "a/b, c/d ,")
    assert cfg.get_repos() == ["a/b", "c/d"]


def test_validate_config_flags_missing_auth(monkeypatch):
    monkeypatch.setattr(cfg, "GITHUB_TOKEN", "")
    monkeypatch.setattr(cfg, "GITHUB_APP_ID", "")
    monkeypatch.setattr(cfg, "GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setattr(cfg, "GITHUB_REPOS", [])
    monkeypatch.setattr(cfg, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(cfg, "LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    logger = logging.getLogger("localowl.test")
    assert cfg.validate_config(logger) is False


def test_validate_config_passes_with_token_and_repo(monkeypatch):
    monkeypatch.setattr(cfg, "GITHUB_TOKEN", "ghp_x")
    monkeypatch.setattr(cfg, "GITHUB_APP_ID", "")
    monkeypatch.setattr(cfg, "GITHUB_APP_PRIVATE_KEY", "")
    monkeypatch.setattr(cfg, "GITHUB_REPOS", ["owner/repo"])
    monkeypatch.setattr(cfg, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(cfg, "LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    logger = logging.getLogger("localowl.test")
    assert cfg.validate_config(logger) is True
