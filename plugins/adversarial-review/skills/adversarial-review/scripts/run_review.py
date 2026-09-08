# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-antigravity>=0.1",
# ]
# ///
"""Adversarial security review of code read from stdin.

Reads a diff or source file on stdin and runs it past an adversarial security
reviewer backed by the Google Antigravity SDK. Findings are printed to stdout;
diagnostics go to stderr. Exits non-zero when no code is provided, the SDK is
missing, or credentials are unavailable, so callers never mistake a skipped
review for a clean one.

Run with a PEP 723-aware runner so the dependency is resolved automatically:

    git diff HEAD | uv run scripts/run_review.py
    cat changed_file.py | uv run scripts/run_review.py
"""
from __future__ import annotations

import asyncio
import sys

SYSTEM_INSTRUCTIONS = """
You are an elite adversarial security reviewer and ethical hacker.
Relentlessly scrutinize the provided code from an attacker's perspective. Look for:
- Security flaws (injection, XSS, SSRF, path traversal, unsafe deserialization, etc.)
- Missing authentication/authorization on reachable endpoints
- Logic errors and race conditions
- Hardcoded secrets or weak cryptography
- Unbounded input / resource exhaustion (DoS)
- Unhandled edge cases that could crash or leak data

Report concisely, most severe first. For each issue give: location, why it is
exploitable, and a concrete remediation. If the code is sound, say so plainly.
"""


async def _review(code: str) -> str:
    """Run the adversarial agent over the given code and return its report."""
    from google.antigravity import Agent, LocalAgentConfig

    config = LocalAgentConfig(system_instructions=SYSTEM_INSTRUCTIONS)
    async with Agent(config) as agent:
        prompt = (
            "Review this code diff/file for security vulnerabilities and logic "
            f"flaws:\n\n{code}"
        )
        response = await agent.chat(prompt)
        return await response.text()


def main() -> int:
    """Entry point: read stdin, run the review, print the report."""
    code = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not code.strip():
        print("adversarial-review: no code provided on stdin.", file=sys.stderr)
        return 2

    try:
        from google.antigravity import Agent, LocalAgentConfig  # noqa: F401
    except ImportError:
        print(
            "adversarial-review: google-antigravity is not installed. "
            "Run via `uv run` or `pip install google-antigravity`.",
            file=sys.stderr,
        )
        return 3

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        report = asyncio.run(_review(code))
    except Exception as exc:  # noqa: BLE001 - surface any SDK/credential failure
        print(
            "adversarial-review: the review could not run "
            f"({type(exc).__name__}: {exc}). Check Google/Gemini credentials "
            "(e.g. GEMINI_API_KEY, GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT/ADC).",
            file=sys.stderr,
        )
        return 1

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
