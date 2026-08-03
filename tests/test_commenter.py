from src.commenter import (
    _MAX_BODY_CHARS,
    PRCommenter,
    _extract_issue_sections,
    _parse_verdict,
    _verdict_to_review_event,
)


def test_parse_verdict_approve():
    assert _parse_verdict("## ✅ Verdict\n\n✅ **Approve**\nNo issues found.") == "approve"


def test_parse_verdict_suggestions():
    assert _parse_verdict("## ✅ Verdict\n\n⚠️ **Approve with suggestions** Minor nits only.") == "suggestions"


def test_parse_verdict_changes():
    assert _parse_verdict("## ✅ Verdict\n\n❌ **Request changes** There is a critical bug.") == "changes"


def test_parse_verdict_unknown_when_missing():
    assert _parse_verdict("## 📋 Overview\nLooks fine.") == "unknown"


def test_parse_verdict_echo_of_full_option_list():
    text = (
        "## ✅ Verdict\n\nPick ONE line:\n"
        "✅ **Approve**\n⚠️ **Approve with suggestions**\n❌ **Request changes**"
    )
    assert _parse_verdict(text) == "approve"


def test_verdict_to_review_event_enforced():
    assert _verdict_to_review_event("approve", True) == "APPROVE"
    assert _verdict_to_review_event("changes", True) == "REQUEST_CHANGES"
    assert _verdict_to_review_event("suggestions", True) == "COMMENT"
    assert _verdict_to_review_event("approve", False) == "COMMENT"


def test_extract_issue_sections_none_found():
    text = "## 🐛 Bugs & Logic Errors\n**None found.**\n## 🔒 Security\n**None found.**"
    assert _extract_issue_sections(text) == []


def test_extract_issue_sections_finds_bugs_only():
    text = (
        "## 🐛 Bugs & Logic Errors\n`app.py:10` 🔴 Crashes on empty input. Guard it.\n"
        "## 🔒 Security\n**None found.**\n## ⚡ Performance\n**None found.**"
    )
    sections = _extract_issue_sections(text)
    assert len(sections) == 1
    assert sections[0][0] == "🐛 Bugs & Logic Errors"
    assert "app.py:10" in sections[0][1]


def test_format_comment_includes_footer_and_verdict(monkeypatch):
    monkeypatch.setattr("src.commenter.config.BOT_HANDLE", "owlbot")
    commenter = PRCommenter(github_client=object())
    out = commenter._format_comment(
        "owner/repo",
        3,
        "Fix the bug",
        "## ✅ Verdict\n\n✅ **Approve**\nAll good.",
        {"author": "alice", "changed_files": 2, "additions": 5, "deletions": 2},
    )
    assert "@alice" in out
    assert "**Approved — safe to merge.**" in out
    assert "`@owlbot review`" in out
    assert "owner/repo" in out


def test_format_comment_marks_rereview(monkeypatch):
    monkeypatch.setattr("src.commenter.config.BOT_HANDLE", "diffowlbot")
    commenter = PRCommenter(github_client=object())
    out = commenter._format_comment(
        "owner/repo", 3, "t", "## ✅ Verdict\n\n✅ **Approve**\nok.", None, incremental=True
    )
    assert "LocalOwl Re-review" in out
    assert "re-review · new commits only" in out


def test_format_comment_truncates_oversized_review(monkeypatch):
    monkeypatch.setattr("src.commenter.config.BOT_HANDLE", "diffowlbot")
    commenter = PRCommenter(github_client=object())
    long_review = "## ✅ Verdict\n\n✅ **Approve**\n" + ("x" * (_MAX_BODY_CHARS + 5000))
    out = commenter._format_comment("owner/repo", 1, "t", long_review, None)
    assert "Review was truncated by LocalOwl" in out
    assert len(out) < _MAX_BODY_CHARS + 2000
