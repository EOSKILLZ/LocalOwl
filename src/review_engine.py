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
One or two sentences: what do these new commits add, fix, or remove compared to the last review?

## 🔍 New Issues Only
Issues introduced by these new commits — bugs, security problems, performance concerns, or code quality problems.
Severity: 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low. Each finding: file, line, risk, fix.
If none: **None found.**

## ✅ Status
One sentence: is the PR ready to merge, still needs work, or were earlier issues resolved?"""

_SECTIONS = """\
## 📋 Overview
What does this PR do and why? Two sentences max.

## 📁 File-by-File Breakdown
One line per changed file: **`filename`** — what changed and any immediate concern, or "no concerns".

## 🐛 Bugs & Logic Errors
Each bug: severity badge, exact file + line, one-sentence explanation.
🔴 Critical — crash/data loss · 🟠 High — wrong in common cases · 🟡 Medium — edge case · 🟢 Low — minor
If none: **None found.**

## 🔒 Security
Check: hardcoded secrets, injection (SQL/shell/path/template), missing auth, unsafe deserialisation, \
sensitive data in logs, open redirects, broken access control, insecure defaults.
Each finding: file, line, exact risk, recommended fix.
If none: **None found.**

## ⚡ Performance
Check: N+1 queries, missing indexes, blocking I/O in hot paths, large allocations, unnecessary loops, missing caching.
Each finding: file, line, impact, recommended fix.
If none: **None found.**

## 🧹 Code Quality
Check: duplicated logic, overly complex functions, unclear naming, missing error handling, \
missing tests for new behaviour, debug code left in.
If none: **None found.**

## ✅ Verdict
Write your chosen verdict on its own line, then a single sentence of justification. Do not repeat the other options.

✅ **Approve** · ⚠️ **Approve with suggestions** · ❌ **Request changes**"""


def _build_incremental_prompt(config: dict | None = None) -> str:
    cfg   = config or {}
    tone  = cfg.get("tone", "technical")
    parts = [
        "You are a senior software engineer reviewing only the new commits pushed to an already-reviewed "
        "pull request. Focus exclusively on what changed in these commits. "
        "Do not repeat findings from the earlier review unless directly relevant to new code. "
        "State every finding with the exact file and line number.",
    ]
    if tone == "technical":
        parts.append(
            "Be strictly technical — reference language specs, security standards, or performance "
            "characteristics where applicable. Every finding must include file, line, risk, and a concrete fix."
        )
    elif tone == "strict":
        parts.append("Flag every potential issue introduced by the new code, even minor ones.")
    elif tone == "lenient":
        parts.append("Focus on critical and high-severity new issues only — skip style nits.")
    return " ".join(parts) + "\n\nUse exactly this structure:\n\n---\n\n" + _INCREMENTAL_SECTIONS + "\n\n---"


def _build_system_prompt(config: dict | None = None) -> str:
    cfg    = config or {}
    tone   = cfg.get("tone", "technical")
    style  = cfg.get("style", "detailed")
    focus  = set(cfg.get("focus") or list(_ALL_FOCUS))
    custom = (cfg.get("custom_instructions") or "").strip()

    parts = [
        "You are a senior software engineer conducting a thorough, uncompromising pull request review. "
        "Analyse the diff carefully and produce a structured report. "
        "State every finding with the exact filename and line number. "
        "Do not repeat diff lines verbatim. Do not invent issues not present in the diff. "
        "For trivial or purely mechanical changes, keep each section to one sentence."
    ]

    if tone == "technical":
        parts.append(
            "Be strictly technical and direct — no qualitative opinion, no encouragement. "
            "Reference language specs, documented security standards, or performance characteristics "
            "where applicable. Every finding must include file, line, exact risk, and a concrete fix."
        )
    elif tone == "strict":
        parts.append("Be strict and thorough — flag every potential issue, even minor ones. Give no benefit of the doubt.")
    elif tone == "lenient":
        parts.append("Focus on critical and high-severity issues only. Skip style preferences and minor nitpicks.")

    if style == "concise":
        parts.append("Be concise — 1–3 lines per section. Omit detail when nothing substantive applies.")

    active_focus = _ALL_FOCUS & focus
    if active_focus and active_focus != _ALL_FOCUS:
        labels = ", ".join(
            _FOCUS_LABELS[k] for k in sorted(active_focus) if k in _FOCUS_LABELS
        )
        parts.append(
            f"Prioritise: {labels}. "
            "Still include all sections but keep non-priority areas brief."
        )

    prompt = " ".join(parts) + "\n\nUse exactly this structure:\n\n---\n\n" + _SECTIONS

    if custom:
        prompt += f"\n\n**Additional instructions from the repo owner:** {custom}"

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
