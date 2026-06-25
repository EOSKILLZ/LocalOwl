import fnmatch
import logging
from .api_gateway import LMStudioClient
from .config import (
    MAX_DIFF_CHARS, MAX_FILES_IN_DIFF, MAX_LINES_PER_FILE, IGNORE_FILE_PATTERNS,
)

log = logging.getLogger("localowl.review")

_ALL_FOCUS = frozenset({"bugs", "security", "performance", "code-quality", "test-coverage", "docs"})

_FOCUS_LABELS = {
    "bugs":          "Bugs & Logic Errors",
    "security":      "Security",
    "performance":   "Performance",
    "code-quality":  "Code Quality",
    "test-coverage": "Test Coverage",
    "docs":          "Documentation",
}

_INCREMENTAL_SECTIONS = """\
## 📋 What Changed
Write 1 or 2 sentences. Say what the new commits add, fix, or remove since the last review.

## 🔍 New Issues Only
Look ONLY at the new commits. List any new bug, security hole, slow code, or messy code they add.
Write each issue on its own line like this:
`path/to/file.py:42` 🔴 Problem in one sentence. Fix in one sentence.
Pick the badge: 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low.
If you find nothing wrong, write exactly: **None found.**

## ✅ Status
Write 1 sentence. Is the PR ready to merge now, or does it still need work?"""

# Each section gives the model a copyable line format and an exact empty-case
# string — small models follow concrete examples far better than abstract rules.
_SECTIONS = """\
## 📋 Overview
Write 1 or 2 short sentences. Say what this PR does and why.

## 📁 File-by-File Breakdown
Write one line for each changed file, exactly like this:
**`path/to/file.py`** — what changed here, and any concern (or write "no concerns").

## 🐛 Bugs & Logic Errors
Find code that does the wrong thing: crashes, wrong output, bad if/else, off-by-one, null/undefined, race conditions.
Write each bug on its own line, exactly like this:
`path/to/file.py:42` 🔴 The bug in one sentence. How to fix it in one sentence.
Pick the badge by how bad it is:
🔴 Critical = crash or data loss · 🟠 High = wrong in normal use · 🟡 Medium = wrong in rare cases · 🟢 Low = small mistake
If you find no bugs, write exactly: **None found.**

## 🔒 Security
Look for: passwords or API keys written in the code, SQL/shell/path injection, missing login checks, \
unsafe data parsing, secrets printed to logs, open redirects.
Write each problem on its own line, exactly like this:
`path/to/file.py:42` 🔴 The risk in one sentence. How to fix it in one sentence.
If you find no security problems, write exactly: **None found.**

## ⚡ Performance
Look for: a database call inside a loop, missing database index, slow blocking calls, loading too much into memory, \
work repeated that could be cached.
Write each problem on its own line, exactly like this:
`path/to/file.py:42` 🟠 What is slow in one sentence. How to fix it in one sentence.
If you find no performance problems, write exactly: **None found.**

## 🧹 Code Quality
Look for: copy-pasted code, functions that are too long, confusing names, missing error handling, \
missing tests for new code, leftover debug prints or commented-out code.
Write each problem on its own line, exactly like this:
`path/to/file.py:42` What is messy in one sentence. How to improve it in one sentence.
If you find nothing, write exactly: **None found.**

## ✅ Verdict
Pick ONE line below and copy it. Then add one sentence saying why. Do NOT copy the other two lines.

✅ **Approve**
⚠️ **Approve with suggestions**
❌ **Request changes**

Rules:
- Copy ✅ **Approve** only if every section above says **None found.**
- Copy ⚠️ **Approve with suggestions** if the issues are minor and safe to merge.
- Copy ❌ **Request changes** only if there is at least one 🔴 Critical or 🟠 High issue."""


# Hard rules every model needs, written as short plain commands. Kept identical
# between full and incremental prompts so a 1B model sees one consistent contract.
_BASE_RULES = (
    "You are an expert code reviewer. You read a code diff and write a clear review.\n\n"
    "Follow these rules:\n"
    "1. Only talk about code you can see in the diff. Never guess about code that is not shown.\n"
    "2. Never invent a problem. If the code is fine, say it is fine.\n"
    "3. Every problem you report must name the file and line, like `path/file.py:42`.\n"
    "4. Do not copy lines from the diff. Explain in your own words.\n"
    "5. Write the section headers exactly as given. Keep every section, in order.\n"
    "6. If a change is tiny or simple, keep each section to one short line.\n"
    "7. When a section has no problems, write exactly: **None found.**"
)


def _tone_rule(tone: str) -> str | None:
    if tone == "technical":
        return ("Tone: be direct and technical. No praise, no filler. "
                "For each problem give the exact risk and a concrete one-line fix.")
    if tone == "strict":
        return "Tone: be strict. Report every problem you find, even small ones."
    if tone == "lenient":
        return "Tone: be relaxed. Report only Critical and High problems. Skip small style issues."
    return None  # balanced — no extra tone rule


def _build_incremental_prompt(config: dict | None = None) -> str:
    cfg   = config or {}
    tone  = cfg.get("tone", "technical")
    parts = [
        _BASE_RULES,
        "This PR was already reviewed once. Now review ONLY the new commits since then. "
        "Do not repeat old findings unless the new code makes them worse.",
    ]
    tr = _tone_rule(tone)
    if tr:
        parts.append(tr)
    return "\n\n".join(parts) + "\n\nWrite your review using exactly these sections:\n\n---\n\n" + _INCREMENTAL_SECTIONS + "\n\n---"


def _build_system_prompt(config: dict | None = None) -> str:
    cfg    = config or {}
    tone   = cfg.get("tone", "technical")
    style  = cfg.get("style", "detailed")
    focus  = set(cfg.get("focus") or list(_ALL_FOCUS))
    custom = (cfg.get("custom_instructions") or "").strip()

    parts = [_BASE_RULES]

    tr = _tone_rule(tone)
    if tr:
        parts.append(tr)

    if style == "concise":
        parts.append("Length: keep each section to 1 to 3 short lines.")

    active_focus = _ALL_FOCUS & focus
    if active_focus and active_focus != _ALL_FOCUS:
        labels = ", ".join(
            _FOCUS_LABELS[k] for k in sorted(active_focus) if k in _FOCUS_LABELS
        )
        parts.append(
            f"Pay the most attention to: {labels}. "
            "Still fill in every section, but keep the others short."
        )

    prompt = "\n\n".join(parts) + "\n\nWrite your review using exactly these sections:\n\n---\n\n" + _SECTIONS

    if custom:
        prompt += f"\n\n**Extra instructions from the repo owner (follow these too):** {custom}"

    return prompt + "\n\n---"


def _extra_ignore_patterns(config: dict | None) -> list[str]:
    if not config:
        return []
    raw = config.get("ignore_patterns") or ""
    if isinstance(raw, list):
        return [p.strip() for p in raw if str(p).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


class ReviewEngine:
    def __init__(self, lm_client: LMStudioClient = None):
        self.lm = lm_client or LMStudioClient()

    def analyze_pr(self, pull_request, repo_config: dict | None = None, since_sha: str | None = None) -> dict:
        pr_number   = pull_request.number
        pr_title    = pull_request.title
        incremental = bool(since_sha)
        log.info("Analysing PR #%d%s: %s", pr_number, " (incremental)" if incremental else "", pr_title)

        try:
            extra = _extra_ignore_patterns(repo_config)
            if incremental:
                diff, truncated = self._extract_incremental_diff(pull_request, since_sha, extra_patterns=extra)
                prompt = _build_incremental_prompt(repo_config)
            else:
                diff, truncated = self._extract_diff(pull_request, extra_patterns=extra)
                prompt = _build_system_prompt(repo_config)
            meta   = self._collect_meta(pull_request)
            review = self._generate_review(pr_title, pull_request.body or "", diff, truncated, meta, prompt)

            if not review:
                log.warning("PR #%d: LM Studio returned an empty review", pr_number)
                return self._error_result(pr_number, pr_title, "LM Studio returned no content")

            log.info("PR #%d: review generated (%d chars)", pr_number, len(review))
            return {
                "pr_number":   pr_number,
                "pr_title":    pr_title,
                "review":      review,
                "status":      "success",
                "truncated":   truncated,
                "meta":        meta,
                "incremental": incremental,
            }

        except Exception as e:
            log.exception("Unexpected error analysing PR #%d", pr_number)
            return self._error_result(pr_number, pr_title, str(e))

    # ── internals ─────────────────────────────────────────────────────────────

    def _generate_review(
        self, title: str, body: str, diff: str, truncated: bool, meta: dict, system_prompt: str
    ) -> str:
        user_msg = self._build_user_message(title, body, diff, truncated, meta)
        log.debug("Sending %d chars to LM Studio", len(user_msg))
        return self.lm.chat(system_prompt, user_msg)

    def _build_user_message(self, title: str, body: str, diff: str, truncated: bool, meta: dict) -> str:
        parts = [f"# PR #{meta['number']}: {title}"]

        meta_lines = [
            f"- **Author:** {meta['author']}",
            f"- **Base → Head:** `{meta['base']}` → `{meta['head']}`",
            f"- **Commits:** {meta['commits']}",
            f"- **Changed files:** {meta['changed_files']} (+{meta['additions']} / -{meta['deletions']} lines)",
        ]
        if meta["labels"]:
            meta_lines.append(f"- **Labels:** {', '.join(meta['labels'])}")
        parts.append("\n".join(meta_lines))

        if body and body.strip():
            parts.append(f"**PR Description:**\n{body.strip()[:1000]}")

        if truncated:
            parts.append(
                "> ⚠️ **Note:** The diff was truncated at the size limit. "
                "Review what is shown; the full changeset may be larger."
            )

        parts.append(f"**Diff:**\n```diff\n{diff or '[no diff available]'}\n```")
        return "\n\n".join(parts)

    def _collect_meta(self, pull_request) -> dict:
        try:
            return {
                "number":        pull_request.number,
                "author":        pull_request.user.login,
                "base":          pull_request.base.ref,
                "head":          pull_request.head.ref,
                "commits":       pull_request.commits,
                "changed_files": pull_request.changed_files,
                "additions":     pull_request.additions,
                "deletions":     pull_request.deletions,
                "labels":        [lb.name for lb in pull_request.labels],
            }
        except Exception as e:
            log.debug("Could not collect PR metadata: %s", e)
            return {
                "number": pull_request.number, "author": "unknown",
                "base": "?", "head": "?", "commits": "?",
                "changed_files": "?", "additions": "?", "deletions": "?",
                "labels": [],
            }

    def _extract_diff(self, pull_request, extra_patterns: list[str] | None = None) -> tuple[str, bool]:
        try:
            files = list(pull_request.get_files())
            log.debug("PR #%d has %d changed file(s)", pull_request.number, len(files))
            return self._build_diff(files, extra_patterns)
        except Exception as e:
            log.warning("Could not extract diff for PR #%d: %s", pull_request.number, e)
            return "", False

    def _extract_incremental_diff(
        self, pull_request, since_sha: str, extra_patterns: list[str] | None = None
    ) -> tuple[str, bool]:
        try:
            comparison = pull_request.base.repo.compare(since_sha, pull_request.head.sha)
            files      = list(comparison.files)
            log.debug(
                "PR #%d incremental diff %s..%s — %d file(s)",
                pull_request.number, since_sha[:7], pull_request.head.sha[:7], len(files),
            )
            if not files:
                log.info("PR #%d: no file changes since last review", pull_request.number)
                return "", False
            return self._build_diff(files, extra_patterns)
        except Exception as e:
            log.warning("Could not get incremental diff for PR #%d (%s); falling back to full diff", pull_request.number, e)
            return self._extract_diff(pull_request, extra_patterns)

    def _build_diff(self, files: list, extra_patterns: list[str] | None = None) -> tuple[str, bool]:
        all_patterns = list(IGNORE_FILE_PATTERNS) + (extra_patterns or [])
        reviewable   = [f for f in files if not _should_ignore(f.filename, all_patterns)]
        skipped      = len(files) - len(reviewable)
        if skipped:
            log.debug("Skipped %d file(s) matching ignore patterns", skipped)

        sections    = []
        total_chars = 0
        truncated   = False

        for f in reviewable[:MAX_FILES_IN_DIFF]:
            header = f"### {f.filename} (+{f.additions}/-{f.deletions})"
            patch  = (f.patch or "").strip()
            if patch:
                lines = patch.splitlines()
                if len(lines) > MAX_LINES_PER_FILE:
                    patch = "\n".join(lines[:MAX_LINES_PER_FILE])
                    patch += f"\n… ({len(lines) - MAX_LINES_PER_FILE} more lines)"
            section      = f"{header}\n{patch}" if patch else header
            total_chars += len(section)

            if total_chars > MAX_DIFF_CHARS:
                truncated = True
                sections.append("… (remaining files omitted — diff size limit reached)")
                break

            sections.append(section)

        if len(reviewable) > MAX_FILES_IN_DIFF:
            truncated = True
            sections.append(f"… ({len(reviewable) - MAX_FILES_IN_DIFF} more files not shown)")

        result = "\n\n".join(sections)
        log.debug("Diff: %d chars, %d section(s), truncated=%s", len(result), len(sections), truncated)
        return result, truncated

    def explain_pr(self, pull_request) -> str:
        diff, _ = self._extract_diff(pull_request)
        meta     = self._collect_meta(pull_request)
        system   = (
            "Explain what this pull request does in plain language. "
            "Focus on intent: what problem does it solve and what changed? "
            "Write for a non-technical reviewer. 3–5 sentences, no code blocks."
        )
        user_msg = self._build_user_message(
            pull_request.title, pull_request.body or "", diff, False, meta
        )
        return self.lm.chat(system, user_msg) or "Could not generate explanation."

    def summarize_pr(self, pull_request) -> str:
        diff, _ = self._extract_diff(pull_request)
        meta     = self._collect_meta(pull_request)
        system   = (
            "Summarize this pull request in 3–5 bullet points (one sentence each). "
            "Key changes only — no opinions or review commentary."
        )
        user_msg = self._build_user_message(
            pull_request.title, pull_request.body or "", diff, False, meta
        )
        return self.lm.chat(system, user_msg) or "Could not generate summary."

    @staticmethod
    def _error_result(pr_number: int, pr_title: str, reason: str) -> dict:
        return {
            "pr_number": pr_number,
            "pr_title":  pr_title,
            "review":    f"Review failed: {reason}",
            "status":    "error",
            "truncated": False,
        }


def _should_ignore(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p) for p in patterns)
