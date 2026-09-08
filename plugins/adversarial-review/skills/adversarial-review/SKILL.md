---
name: adversarial-review
description: Runs an adversarial security review of freshly written or edited code using the Google Antigravity SDK, then reconciles the findings. Use immediately after generating code, finishing a feature, or making significant edits to source files (.py, .js, .ts, .go, .rb, etc.), or when the user asks for an adversarial, security, or attacker's-eye review of changes or a diff.
---

# Adversarial Review

## Overview
After code is generated or edited, this skill pipes the change into an adversarial security
reviewer (an ethical-hacker agent backed by the Google Antigravity SDK) and forces the findings to
be *reconciled* — fixed or explicitly justified — before the task is called done. It exists to stop
the common failure where freshly generated code ships with injection flaws, missing authz,
unbounded input, or debug backdoors because nobody looked at it from an attacker's perspective.

## When to Use
Use when:
- you just generated new code, finished a feature, or made significant edits to source files;
- the user asks for an "adversarial review", "security review", "attacker's-eye" look, or to
  "review this diff/code for vulnerabilities";
- the project's guidelines (e.g. a CLAUDE.md post-generation workflow) require a review before a
  task is complete.

Do **not** use for: pure documentation/markdown/config-only edits with no executable code;
formatting-only or comment-only changes; generating tests (that's ordinary coding); or a general
code-quality/style review with no security dimension.

## Core Process

### 1. Gather the code under review
Prefer a diff; fall back to whole files.
- If in a git repo: `git diff HEAD` (or `git diff --staged`) to capture the change.
- If not a git repo, or the diff is empty: pass the full contents of the files you just created or
  edited.
Skip files that contain no executable code.

### 2. Run the reviewer
Pipe the code/diff into the bundled review script on stdin. The script declares its own
dependency (`google-antigravity`) via PEP 723 inline metadata; run it with a runner that honors
that (e.g. `uv run`), or in an environment where `google-antigravity` is installed:

```bash
git diff HEAD | uv run scripts/run_review.py       # diff-based
# or, no git / empty diff:
cat path/to/changed_file.py | uv run scripts/run_review.py
```

The script requires Google/Gemini credentials in the environment (e.g. `GEMINI_API_KEY` or
`GOOGLE_API_KEY`); if they are absent it exits non-zero with a clear message — surface that to the
user rather than silently skipping the review.

### 3. Triage every finding
For each finding the reviewer returns, classify it:
- **Fix now** — a real security bug or logic flaw in the changed code (injection, missing authz on
  a reachable endpoint, unbounded input / DoS, hardcoded secret, unsafe deserialization, debug
  backdoor). Apply the fix.
- **Justify** — not applicable given the app's threat model (e.g. auth intentionally omitted for a
  local single-user tool). Record a one-line reason.
- **Already mitigated** — confirm the mitigation exists (e.g. output is escaped at render time) and
  note where.

### 4. Re-verify after fixing
After applying fixes, re-run the changed code (tests or a quick manual exercise) to confirm nothing
broke, and — for anything non-trivial — re-run step 2 on the patched code to confirm the finding is
gone.

### 5. Report the reconciliation
Give the user a short table: finding → disposition (fixed / justified / already-mitigated) with
evidence. Do not call the task complete until every finding is in one of those three states.

## Red Flags
Stop and rework if you notice:
- claiming the review "passed" without ever running the script and reading its output;
- marking a finding "not applicable" with no stated reason;
- running the reviewer but ignoring a finding that is clearly exploitable in the changed code;
- hardcoded absolute paths (reference the bundled script relative to the skill: `scripts/run_review.py`);
- swallowing a missing-credentials or import error and pretending the review ran.

## Verification
Ship only when every box is checked (evidence, not assumptions):
- [ ] The review script actually ran and its output was read (not assumed)
- [ ] Every returned finding is fixed, justified with a reason, or confirmed already-mitigated
- [ ] Applied fixes were re-verified (tests pass / behaviour re-exercised)
- [ ] The user received a finding → disposition summary
- [ ] `python scripts/validate_skill.py <dir> --strict` exits 0
