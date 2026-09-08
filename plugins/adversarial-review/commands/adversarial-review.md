---
description: Run an adversarial security review of the current changes via the Antigravity SDK and reconcile the findings.
---

# Adversarial Review Command

Invokes the Google Antigravity SDK to perform a relentless security review of the current changes,
then reconciles the findings. This is the manual entry point for the `adversarial-review` skill; the
same review also runs automatically after code edits via the plugin's PostToolUse hook.

## Instructions for Claude

When the user runs `/adversarial-review`, perform the following steps:

1. **Get context.** Run `git diff HEAD` to capture the current changes. If this is not a git repo or
   the diff is empty, use the full contents of the files just created or edited. Skip files with no
   executable code.

2. **Run the reviewer.** Pipe the diff/code into the bundled script (it declares its own dependency
   via PEP 723, so `uv run` resolves `google-antigravity` automatically):
   ```bash
   git diff HEAD | uv run "${CLAUDE_PLUGIN_ROOT}/skills/adversarial-review/scripts/run_review.py"
   # or, with no git / an empty diff:
   cat path/to/changed_file.py | uv run "${CLAUDE_PLUGIN_ROOT}/skills/adversarial-review/scripts/run_review.py"
   ```
   The script needs Google/Gemini credentials (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, or
   `GOOGLE_CLOUD_PROJECT`/ADC). If they are missing it exits non-zero with a message — surface that
   instead of reporting a clean review.

3. **Reconcile every finding.** For each finding, either fix it, justify why it does not apply (one
   line), or confirm it is already mitigated (say where).

4. **Report.** Give the user a finding → disposition summary. Do not consider the task complete until
   every finding is fixed, justified, or confirmed mitigated.
