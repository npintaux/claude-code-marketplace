# Nicolas Pintaux — Claude Code Marketplace

A [Claude Code](https://claude.com/claude-code) plugin marketplace.

It currently ships one plugin, **adversarial-review**: an agentic security
reviewer that puts every piece of freshly written code through an attacker's-eye
pass — backed by the [Google Antigravity SDK](https://pypi.org/project/google-antigravity/) —
and makes the findings be reconciled before a task is considered done.

## Plugins

| Plugin | Description | Version |
| --- | --- | --- |
| [`adversarial-review`](plugins/adversarial-review) | Antigravity SDK-based adversarial code reviewer that runs automatically after edits and on demand | 1.0.0 |

## Installation

From inside Claude Code, add this marketplace and install the plugin:

```
/plugin marketplace add npintaux/claude-code-marketplace
/plugin install adversarial-review@Nicolas-Pintaux-Claude-Code-Marketplace
```

The first command registers the marketplace (GitHub `owner/repo` shorthand; a
full `https://github.com/npintaux/claude-code-marketplace.git` URL also works).
The second installs the plugin and wires up its hooks, skill, and command.

To update later, re-sync the marketplace and reinstall:

```
/plugin marketplace update Nicolas-Pintaux-Claude-Code-Marketplace
/plugin install adversarial-review@Nicolas-Pintaux-Claude-Code-Marketplace
```

## Requirements

The reviewer runs a small Python script that talks to the Antigravity SDK:

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** on your `PATH` — it reads the script's
  [PEP 723](https://peps.python.org/pep-0723/) inline metadata and resolves
  `google-antigravity` automatically, so you don't need to install anything by
  hand.
- **Google / Gemini credentials** in the environment (one of):
  - `GEMINI_API_KEY`
  - `GOOGLE_API_KEY`
  - `GOOGLE_CLOUD_PROJECT` with Application Default Credentials (ADC)

  If credentials are missing the reviewer exits non-zero with a clear message
  rather than reporting a false "clean" review.

## Usage

Once installed, the plugin works three ways:

### 1. Automatically, after code edits

The plugin registers two hooks (see [`hooks.json`](plugins/adversarial-review/hooks/hooks.json)):

- A **`PostToolUse`** hook silently records each code file you write or edit
  (`Write` / `Edit` / `MultiEdit`) into a per-session queue. Nothing interrupts
  your editing.
- A **`Stop`** hook fires once when Claude finishes the turn, reviews every
  queued code file in a single batch, and prints the findings — led by a bold
  **Launching review agent based on Antigravity SDK** banner — *before* Claude
  begins reconciling them.

Batching to the end of the turn means a multi-file change triggers **one**
review, not one per edit. Only source files are reviewed (`.py`, `.js`, `.ts`,
`.go`, `.rb`, `.rs`, `.java`, `.sql`, …); docs, config, and data are skipped.

### 2. On demand, via the slash command

```
/adversarial-review
```

Reviews the current `git diff HEAD` (falling back to the full contents of files
you just changed), then reconciles the findings.

### 3. Via the skill

Ask for an "adversarial review", "security review", or an "attacker's-eye look"
at your changes, and the `adversarial-review` skill runs the same flow.

## How findings are reconciled

Every finding must end in one of three states before the task is complete:

- **Fixed** — a real bug in the changed code; the fix is applied.
- **Justified** — not applicable under the app's threat model; a one-line reason
  is recorded.
- **Already mitigated** — the protection already exists; where it lives is noted.

You always get a finding → disposition summary.

## Repository structure

```
.claude-plugin/
  marketplace.json                      # marketplace manifest (lists plugins)
plugins/
  adversarial-review/
    .claude-plugin/plugin.json          # plugin manifest
    commands/adversarial-review.md       # /adversarial-review slash command
    hooks/
      hooks.json                         # PostToolUse + Stop hook wiring
      review_hook.py                     # batches edits, runs the batched review
    skills/adversarial-review/
      SKILL.md                           # the adversarial-review skill
      scripts/run_review.py              # PEP 723 reviewer (Antigravity SDK)
      evals/trigger_evals.json           # skill trigger evals
```

## Running the reviewer directly

Outside Claude Code you can pipe code straight into the reviewer:

```bash
git diff HEAD | uv run plugins/adversarial-review/skills/adversarial-review/scripts/run_review.py
# or, with no git / an empty diff:
cat path/to/changed_file.py | uv run plugins/adversarial-review/skills/adversarial-review/scripts/run_review.py
```

## Author

Maintained by Nicolas Pintaux.
