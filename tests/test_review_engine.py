from types import SimpleNamespace

from src.review_engine import (
    ReviewEngine,
    _build_incremental_prompt,
    _build_system_prompt,
    _extra_ignore_patterns,
    _should_ignore,
)


def _file(filename, patch="@@ -1,1 +1,1 @@\n-foo\n+bar\n", additions=1, deletions=1):
    return SimpleNamespace(filename=filename, patch=patch, additions=additions, deletions=deletions)


def _engine():
    return ReviewEngine(lm_client=object())


def test_build_diff_respects_extra_ignore_patterns():
    diff, truncated = _engine()._build_diff(
        [_file("src/app.py"), _file("src/bundle.js")],
        extra_patterns=["*.js"],
    )
    assert "src/app.py" in diff
    assert "src/bundle.js" not in diff
    assert not truncated


def test_build_diff_respects_max_files(monkeypatch):
    monkeypatch.setattr("src.review_engine.MAX_FILES_IN_DIFF", 2)
    files = [_file(f"file{i}.py") for i in range(4)]
    diff, truncated = _engine()._build_diff(files)
    assert truncated
    assert "2 more files not shown" in diff
    for i in range(2):
        assert f"file{i}.py" in diff
    assert "file3.py" not in diff


def test_build_diff_keeps_first_file_over_limit(monkeypatch):
    """Regression: a single huge file must not drop the whole diff."""
    monkeypatch.setattr("src.review_engine.MAX_DIFF_CHARS", 120)
    monkeypatch.setattr("src.review_engine.MAX_LINES_PER_FILE", 1000)
    diff, truncated = _engine()._build_diff([_file("big.py", patch="A" * 500), _file("small.py")])
    assert "big.py" in diff
    assert "small.py" not in diff
    assert truncated


def test_build_diff_truncates_long_files(monkeypatch):
    monkeypatch.setattr("src.review_engine.MAX_LINES_PER_FILE", 2)
    patch = "\n".join(["+line%d" % i for i in range(5)])
    diff, _ = _engine()._build_diff([_file("app.py", patch=patch)])
    assert "3 more lines" in diff


def test_build_diff_empty_patch_uses_header_only():
    diff, _ = _engine()._build_diff([_file("binary.bin", patch=None)])
    assert "binary.bin" in diff


def test_system_prompt_defaults_to_technical_tone():
    prompt = _build_system_prompt({})
    assert "senior security-conscious engineer" in prompt
    assert "## 📋 Overview" in prompt


def test_system_prompt_balanced_has_no_tone_rule():
    prompt = _build_system_prompt({"tone": "balanced"})
    assert "senior security-conscious" not in prompt
    assert "## 📋 Overview" in prompt


def test_system_prompt_technical_tone():
    assert "senior security-conscious engineer" in _build_system_prompt({"tone": "technical"})


def test_system_prompt_strict_tone():
    assert "be strict" in _build_system_prompt({"tone": "strict"})


def test_system_prompt_focus_subset():
    prompt = _build_system_prompt({"focus": ["security", "bugs"]})
    assert "Pay the most attention to: Bugs & Logic Errors, Security" in prompt


def test_system_prompt_custom_instructions():
    prompt = _build_system_prompt({"custom_instructions": "Check for missing DB indexes."})
    assert "Extra instructions from the repo owner" in prompt
    assert "Check for missing DB indexes." in prompt


def test_system_prompt_concise_style():
    assert "keep each section to 1 to 3 short lines" in _build_system_prompt({"style": "concise"})


def test_incremental_prompt_focuses_on_new_commits():
    prompt = _build_incremental_prompt({})
    assert "new commits" in prompt


def test_incremental_prompt_honors_focus_and_custom():
    prompt = _build_incremental_prompt({"focus": ["security"], "custom_instructions": "Check auth everywhere."})
    assert "Pay the most attention to: Security" in prompt
    assert "Check auth everywhere." in prompt


def test_extra_ignore_patterns_list_and_csv():
    assert _extra_ignore_patterns({"ignore_patterns": ["a/*", "b/*"]}) == ["a/*", "b/*"]
    assert _extra_ignore_patterns({"ignore_patterns": "c/*, d/*"}) == ["c/*", "d/*"]
    assert _extra_ignore_patterns(None) == []


def test_should_ignore_matches_fnmatch():
    assert _should_ignore("dist/app.js", ["dist/*"])
    assert _should_ignore("src/gen.ts", ["*.generated.ts"]) is False
    assert _should_ignore("package-lock.json", ["package-lock.json"])
    assert not _should_ignore("src/app.py", ["dist/*"])
