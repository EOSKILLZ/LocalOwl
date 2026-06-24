import fnmatch
import logging
from .api_gateway import LMStudioClient
from .config import (
    MAX_DIFF_CHARS, MAX_FILES_IN_DIFF, MAX_LINES_PER_FILE, IGNORE_FILE_PATTERNS,
)

log = logging.getLogger("localowl.review")

_SYSTEM_PROMPT = """\
You are a senior software engineer conducting a thorough pull request review. \
Analyse the diff carefully and produce a detailed, structured report. \
Do NOT repeat lines from the diff. Do NOT invent issues that aren't visible. \
Be specific — name the file and line when calling something out.

Use exactly this structure:

---

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
unsafe deserialization, exposed sensitive data in logs, open redirects, \
insecure defaults. Call out each finding with the filename.
If none, write: _No security issues found._

## ⚡ Performance
Flag: N+1 queries, unnecessary loops, blocking I/O in hot paths, \
missing indexes or caching, large allocations. Be specific.
If none, write: _No performance concerns._

## 🧹 Code Quality
Note: duplicate logic, overly complex functions, unclear naming, \
missing type hints, missing or inadequate error handling, \
missing tests for new behaviour, leftover debug code.

## ✅ Verdict
Choose one and justify in one sentence:
- ✅ **Approve** — ready to merge
- ⚠️ **Approve with suggestions** — safe to merge, suggestions are non-blocking
- ❌ **Request changes** — must fix the listed issues before merging

---"""


class ReviewEngine:
    def __init__(self, lm_client: LMStudioClient = None):
        self.lm = lm_client or LMStudioClient()

    def analyze_pr(self, pull_request) -> dict:
        pr_number = pull_request.number
        pr_title = pull_request.title
        log.info("Analysing PR #%d: %s", pr_number, pr_title)

        try:
            diff, truncated = self._extract_diff(pull_request)
            meta = self._collect_meta(pull_request)
            review = self._generate_review(pr_title, pull_request.body or "", diff, truncated, meta)

            if not review:
                log.warning("PR #%d: LM Studio returned an empty review", pr_number)
                return self._error_result(pr_number, pr_title, "LM Studio returned no content")

            log.info("PR #%d: review generated (%d chars)", pr_number, len(review))
            return {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "review": review,
                "status": "success",
                "truncated": truncated,
            }

        except Exception as e:
            log.exception("Unexpected error analysing PR #%d", pr_number)
            return self._error_result(pr_number, pr_title, str(e))

    # ── internals ─────────────────────────────────────────────────────────────

    def _generate_review(self, title: str, body: str, diff: str, truncated: bool, meta: dict) -> str:
        user_msg = self._build_user_message(title, body, diff, truncated, meta)
        log.debug("Sending %d chars to LM Studio", len(user_msg))
        return self.lm.chat(_SYSTEM_PROMPT, user_msg)

    def _build_user_message(self, title: str, body: str, diff: str, truncated: bool, meta: dict) -> str:
        parts = [f"# PR #{meta['number']}: {title}"]

        # Metadata block gives the model useful context
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

    def _extract_diff(self, pull_request) -> tuple[str, bool]:
        try:
            all_files = list(pull_request.get_files())
            log.debug("PR #%d has %d changed file(s)", pull_request.number, len(all_files))

            reviewable = [f for f in all_files if not self._should_ignore(f.filename)]
            skipped = len(all_files) - len(reviewable)
            if skipped:
                log.debug("Skipped %d file(s) matching ignore patterns", skipped)

            sections = []
            total_chars = 0
            truncated = False

            for f in reviewable[:MAX_FILES_IN_DIFF]:
                header = f"### {f.filename} (+{f.additions}/-{f.deletions})"
                patch = (f.patch or "").strip()
                if patch:
                    lines = patch.splitlines()
                    if len(lines) > MAX_LINES_PER_FILE:
                        patch = "\n".join(lines[:MAX_LINES_PER_FILE])
                        patch += f"\n… ({len(lines) - MAX_LINES_PER_FILE} more lines)"
                section = f"{header}\n{patch}" if patch else header
                total_chars += len(section)

                if total_chars > MAX_DIFF_CHARS:
                    truncated = True
                    sections.append("… (remaining files omitted — diff size limit reached)")
                    break

                sections.append(section)

            if len(reviewable) > MAX_FILES_IN_DIFF:
                truncated = True
                sections.append(
                    f"… ({len(reviewable) - MAX_FILES_IN_DIFF} more files not shown)"
                )

            result = "\n\n".join(sections)
            log.debug(
                "Diff: %d chars, %d section(s), truncated=%s",
                len(result), len(sections), truncated,
            )
            return result, truncated

        except Exception as e:
            log.warning("Could not extract diff for PR #%d: %s", pull_request.number, e)
            return "", False

    @staticmethod
    def _should_ignore(filename: str) -> bool:
        return any(fnmatch.fnmatch(filename, pattern) for pattern in IGNORE_FILE_PATTERNS)

    @staticmethod
    def _error_result(pr_number: int, pr_title: str, reason: str) -> dict:
        return {
            "pr_number": pr_number,
            "pr_title": pr_title,
            "review": f"Review failed: {reason}",
            "status": "error",
            "truncated": False,
        }
