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
    "test-coverage": "test coverage",
    "docs":          "documentation",
}

_INCREMENTAL_SECTIONS = """\
## 📋 What Changed
1–2 sentences on what these new commits add, fix, or remove compared to the last review.

## 🔍 New Issues Found
Bugs, security problems, performance concerns, or code quality issues introduced by these new commits only.
Use the same severity scale (🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Low).
If none, write: _No new issues in these commits._

## ✅ Status
One sentence: is the PR now ready to merge, still needs work, or were earlier issues resolved?"""

_SECTIONS = """\
## 📋 Overview
2–3 sentences: what does this PR do, and what is the likely motivation?

## 📁 File-by-File Breakdown
For every changed file write one line:
**`filename`** — what changed and any immediate concern (or "looks fine").

## 🐛 Bugs & Logic Errors
List each bug with a severity badge and a short explanation.
Severity scale:
- 🔴 **Critical** — crash, data loss, or always-wrong behaviour
- 🟠 **High** — wrong in common cases
- 🟡 **Medium** — wrong in edge cases
- 🟢 **Low** — minor incorrectness or off-by-one
If none, write: _No bugs found._

## 🔒 Security
Check for: hardcoded secrets, injection (SQL/shell/path), missing auth checks, \
unsafe deserialisation, exposed sensitive data in logs, open redirects, \
insecure defaults. Call out each finding with the filename and line.
If none, write: _No security issues found._

## ⚡ Performance
Flag: N+1 queries, unnecessary loops, blocking I/O in hot paths, \
missing indexes or caching, large allocations. Be specific.
If none, write: _No performance concerns._

## 🧹 Code Quality
Note: duplicate logic, overly complex functions, unclear naming, \
missing type hints, inadequate error handling, \
missing tests for new behaviour, leftover debug code.
If none, write: _No code quality concerns._

## ✅ Verdict
Choose exactly one and justify in one sentence:
- ✅ **Approve** — ready to merge
- ⚠️ **Approve with suggestions** — safe to merge, suggestions are non-blocking
- ❌ **Request changes** — must fix the listed issues before merging"""


def _build_incremental_prompt(config: dict | None = None) -> str:
    cfg   = config or {}
    tone  = cfg.get("tone", "balanced")
    parts = [
        "You are a senior software engineer reviewing only the new commits pushed to an already-reviewed pull request. "
        "Focus exclusively on what changed in these new commits. "
        "Do not repeat findings from the earlier review unless directly relevant to the new code. "
        "Be concise.",
    ]
    if tone == "strict":
        parts.append("Flag any potential issue introduced by the new code, even minor ones.")
    elif tone == "lenient":
        parts.append("Focus on significant new issues only — skip style nits.")
    return " ".join(parts) + "\n\nUse exactly this structure:\n\n---\n\n" + _INCREMENTAL_SECTIONS + "\n\n---"


def _build_system_prompt(config: dict | None = None) -> str:
    cfg    = config or {}
    tone   = cfg.get("tone", "balanced")
    style  = cfg.get("style", "detailed")
    focus  = set(cfg.get("focus") or list(_ALL_FOCUS))
    custom = (cfg.get("custom_instructions") or "").strip()

    parts = [
        "You are a senior software engineer conducting a thorough pull request review. "
        "Analyse the diff carefully and produce a structured report. "
        "Do NOT repeat lines from the diff verbatim. Do NOT invent issues not visible in the diff. "
        "Be specific — name the file and line number when calling something out. "
        "For short or trivial changes, keep each section brief — one sentence is enough "
        "when there is nothing substantive to say."
    ]

    if tone == "strict":
        parts.append("Be strict and thorough — flag any potential issue, even if minor.")
    elif tone == "lenient":
        parts.append("Focus on significant issues only. Skip style nitpicks and minor conventions.")

    if style == "concise":
        parts.append(
            "Be concise — keep each section to 1–3 lines. "
            "Omit detail when nothing substantive applies."
        )

    active_focus = _ALL_FOCUS & focus
    if active_focus and active_focus != _ALL_FOCUS:
        labels = ", ".join(
            _FOCUS_LABELS[k] for k in sorted(active_focus) if k in _FOCUS_LABELS
        )
        parts.append(
            f"Focus your analysis especially on: {labels}. "
            "Still include all sections but keep unfocused areas brief."
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
