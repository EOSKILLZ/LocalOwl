import json
from types import SimpleNamespace

from src.pr_monitor import _LEGACY_SHA, PullRequestMonitor


def _pr(number, sha="abc", draft=False):
    return SimpleNamespace(number=number, head=SimpleNamespace(sha=sha), draft=draft)


class _FakeGithub:
    def __init__(self, prs=None):
        self._prs = prs or []

    def get_pull_requests(self, repo, state="open"):
        return self._prs

    def log_rate_limit(self):
        pass

    def get_repos_by_owner(self, owner):
        return []


def _monitor(prs, tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr("src.pr_monitor.SKIP_DRAFT_PRS", kwargs.get("skip_drafts", True))
    monkeypatch.setattr("src.pr_monitor.RECHECK_UPDATED_PRS", kwargs.get("recheck", True))
    monkeypatch.setattr("src.pr_monitor.STATE_FILE", str(tmp_path / "state.json"))
    return PullRequestMonitor(repo_names=["owner/repo"], github_client=_FakeGithub(prs))


def test_new_prs_are_actionable(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "sha1"), _pr(2, "sha2")], tmp_path, monkeypatch)
    result = m._get_actionable_prs("owner/repo")
    assert [(pr.number, reason) for pr, reason in result] == [(1, "New"), (2, "New")]


def test_updated_pr_is_actionable(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "newsha")], tmp_path, monkeypatch)
    m._state["owner/repo"] = {"1": "oldsha"}
    result = m._get_actionable_prs("owner/repo")
    assert [(pr.number, reason) for pr, reason in result] == [(1, "Updated")]


def test_unchanged_pr_is_not_actionable(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "same")], tmp_path, monkeypatch)
    m._state["owner/repo"] = {"1": "same"}
    assert m._get_actionable_prs("owner/repo") == []


def test_draft_prs_skipped(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "sha", draft=True)], tmp_path, monkeypatch, skip_drafts=True)
    assert m._get_actionable_prs("owner/repo") == []


def test_recheck_disabled_skips_updates(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "newsha")], tmp_path, monkeypatch, recheck=False)
    m._state["owner/repo"] = {"1": "oldsha"}
    assert m._get_actionable_prs("owner/repo") == []


def test_legacy_entries_are_not_retried(tmp_path, monkeypatch):
    m = _monitor([_pr(1, "newsha")], tmp_path, monkeypatch)
    m._state["owner/repo"] = {"1": _LEGACY_SHA}
    assert m._get_actionable_prs("owner/repo") == []


def test_state_file_migrates_list_to_sha_dict(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"owner/repo": [1, 2]}))
    monkeypatch.setattr("src.pr_monitor.STATE_FILE", str(state_file))
    m = PullRequestMonitor(repo_names=["owner/repo"], github_client=_FakeGithub())
    assert m._state == {"owner/repo": {"1": _LEGACY_SHA, "2": _LEGACY_SHA}}


def test_corrupt_state_file_starts_fresh(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text("{not valid json")
    monkeypatch.setattr("src.pr_monitor.STATE_FILE", str(state_file))
    m = PullRequestMonitor(repo_names=["owner/repo"], github_client=_FakeGithub())
    assert m._state == {}
