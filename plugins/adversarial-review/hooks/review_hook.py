#!/usr/bin/env python3
"""Adversarial review hook — batches the review to the end of a turn.

The same script is wired to two events (dispatched on ``hook_event_name``):

- **PostToolUse** (Write|Edit|MultiEdit): silently records the edited code file
  into a per-session queue. No review runs here, so editing is never interrupted
  and a multi-file change triggers exactly one review instead of one per edit.

- **Stop** (fires once when Claude finishes a turn): reviews every code file
  queued during the turn in a single batch, prints a user-visible note that the
  review ran (``systemMessage``), and returns the findings to Claude to
  reconcile (``decision: block`` + ``reason``). Guarded against infinite loops
  via ``stop_hook_active``.

Design choices:
- **Fail-open**: any problem (non-code file, missing runner, review error) exits
  0 without blocking, so work is never wedged.
- **Code files only**: skips docs/config/data to avoid pointless LLM calls.
- **Private per-user state**: the queue lives in a 0700 dir under the user's
  cache, with a sanitized session id and O_NOFOLLOW appends, so a co-tenant on a
  shared box can't hijack it via a pre-planted symlink or spoof review targets.
- Standard library only; the heavy dependency lives in run_review.py, invoked
  through ``uv run`` (PEP 723 resolves google-antigravity).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:  # POSIX advisory locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

# Extensions worth an adversarial security pass.
CODE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rb", ".php",
    ".java", ".kt", ".rs", ".c", ".h", ".cc", ".cpp", ".cs", ".sh", ".bash",
    ".sql", ".pl", ".swift", ".scala", ".ex", ".exs",
}

# Cap the payload so a huge generated file can't stall the review.
MAX_BYTES = 200_000

# Safety cap on how many files one Stop batch will review. Kept low enough that
# even the worst case stays under the Stop hook's own timeout (see hooks.json):
# ceil(MAX_FILES / MAX_WORKERS) * REVIEW_TIMEOUT.
MAX_FILES = 12

# Per-file review timeout (seconds).
REVIEW_TIMEOUT = 180

# Reviews run concurrently so latency doesn't accumulate across files.
MAX_WORKERS = 6


def _dir_name() -> str:
    """Directory name bound to the current UID where the OS exposes one.

    Binding the name to the UID means the fallback path in a shared temp dir
    (``/tmp/adversarial-review-1000``) can't be pre-created and owned by another
    local user under a name we will then try to use.
    """
    try:
        return f"adversarial-review-{os.getuid()}"
    except AttributeError:  # no getuid() (e.g. Windows)
        return "adversarial-review"


def _state_dir() -> Path:
    """Return a private (0700) per-user directory for hook state.

    The directory must be owned by us and not a symlink; if a path we would use
    is already present but owned by someone else (a planted directory), we fall
    back to a fresh private ``mkdtemp`` rather than trusting it.
    """
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        root = Path(base)
    else:
        try:
            root = Path.home() / ".cache"
        except RuntimeError:  # no home directory resolvable
            root = Path(tempfile.gettempdir())
    directory = root / _dir_name()

    # Create with 0700 from the outset so there is no umask-widened window.
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError:
        return Path(tempfile.mkdtemp(prefix="adversarial-review-"))

    # Refuse a directory we don't own or that is a symlink — either signals a
    # pre-planted path on a shared filesystem.
    try:
        info = os.lstat(directory)
        owned = getattr(os, "getuid", lambda: info.st_uid)() == info.st_uid
        if not owned or not os.path.isdir(directory) or os.path.islink(directory):
            return Path(tempfile.mkdtemp(prefix="adversarial-review-"))
    except OSError:
        return Path(tempfile.mkdtemp(prefix="adversarial-review-"))

    return directory


def _queue_file(session_id: str) -> Path:
    """Return the per-session queue path shared by the record/review modes.

    The session id is sanitized to a safe filename fragment so it can never
    introduce path separators or traversal. When it is missing we fall back to a
    hash of the working directory (stable across the PostToolUse/Stop processes
    of one session, yet distinct per workspace) rather than a global name that
    disjoint sessions would clobber. Note a PID would be wrong here: record and
    review run in different processes.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "")[:128]
    if not safe:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = ""
        safe = "cwd-" + hashlib.sha256(cwd.encode("utf-8", "replace")).hexdigest()[:16]
    return _state_dir() / f"queue-{safe}.txt"


def _is_code_file(file_path: str | None) -> bool:
    """True when the path points at an existing source file worth reviewing.

    Symlinks are rejected: a symlink with a code extension pointing at a
    sensitive file (``foo.py -> ~/.ssh/id_rsa``) would otherwise be read and
    piped to the reviewer, leaking its target's contents.
    """
    if not file_path:
        return False
    path = Path(file_path)
    if path.suffix.lower() not in CODE_EXTENSIONS:
        return False
    if path.is_symlink():
        return False
    return path.is_file()


def _edited_paths(tool_input: dict) -> list[str]:
    """Extract every file path a Write/Edit/MultiEdit call touched.

    Write/Edit/MultiEdit all target a single file via ``file_path`` (MultiEdit
    carries an ``edits`` list *within* that one file). ``path``/``notebook_path``
    are handled defensively in case a matched tool uses a different key.
    """
    paths = []
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    return paths


def _record(payload: dict) -> None:
    """PostToolUse: append the edited code file(s) to the queue, then exit."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)  # malformed payload — nothing to record
    code_files = [p for p in _edited_paths(tool_input) if _is_code_file(p)]
    if not code_files:
        sys.exit(0)

    queue = _queue_file(payload.get("session_id", ""))
    _append_unique(queue, [str(Path(p).resolve()) for p in code_files])
    sys.exit(0)


def _append_unique(queue: Path, lines: list[str]) -> None:
    """Append new lines to the queue under an exclusive lock, refusing symlinks.

    Opening with O_NOFOLLOW means a symlink planted at the queue path (classic
    /tmp attack) makes the open fail rather than redirecting our writes; the lock
    serializes concurrent PostToolUse hooks so parallel edits can't interleave.
    """
    # O_RDWR (not O_WRONLY) so we can read the current contents through the same
    # locked, O_NOFOLLOW descriptor instead of re-opening by path — a re-open
    # would follow a swapped-in symlink and sidestep the lock (TOCTOU).
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(queue, flags, 0o600)
    except OSError:
        return  # symlink/race/permission problem — fail open, skip queuing
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                pass
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            current = os.read(fd, MAX_BYTES).decode("utf-8", "replace")
            existing = set(current.splitlines())
        except OSError:
            existing = set()
        payload = "".join(f"{line}\n" for line in lines if line not in existing)
        if payload:
            os.lseek(fd, 0, os.SEEK_END)  # O_APPEND already forces this; explicit
            os.write(fd, payload.encode("utf-8"))
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _run_review(review_script: Path, code: str) -> tuple[str, str]:
    """Run the reviewer on one file's contents; return (findings, error).

    Hardened against a hostile repository being reviewed:
    - ``uv`` is resolved to an absolute path so a repo-local ``./uv`` on PATH
      can't be run instead of the real binary.
    - ``--no-project`` stops ``uv`` walking up into the repo's ``pyproject.toml``
      / ``uv.toml`` / ``.venv`` for dependency resolution.
    - ``cwd`` is pinned to the reviewer script's own directory, not the repo.
    """
    uv = shutil.which("uv")
    if uv is None:
        return "", "uv not found on PATH"
    try:
        result = subprocess.run(
            [uv, "run", "--no-project", str(review_script)],
            input=code,
            capture_output=True,
            text=True,
            timeout=REVIEW_TIMEOUT,
            cwd=str(review_script.parent),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if result.returncode != 0:
        return "", (result.stderr or "").strip()
    return result.stdout.strip(), (result.stderr or "").strip()


def _read_no_follow(file_path: str) -> str | None:
    """Read a file, refusing to traverse a symlink at the final path component.

    Closes the TOCTOU gap where a plain file that passed ``_is_code_file`` is
    swapped for a symlink to a sensitive target before we read it.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(file_path, flags)
    except OSError:
        return None
    try:
        return os.read(fd, MAX_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _review_one(review_script: Path, file_path: str) -> tuple[str, str, str]:
    """Read one queued file and review it; return (name, findings, error)."""
    name = Path(file_path).name
    code = _read_no_follow(file_path)
    if code is None or not code.strip():
        return name, "", ""
    findings, error = _run_review(review_script, code)
    return name, findings, error


def _review(payload: dict) -> None:
    """Stop: batch-review everything queued this turn, once, in parallel."""
    queue = _queue_file(payload.get("session_id", ""))

    # Loop guard: if we already blocked once in this stop-chain, let the turn end
    # (and drop the queue) rather than reviewing the reconciliation edits again.
    if payload.get("stop_hook_active"):
        queue.unlink(missing_ok=True)
        sys.exit(0)

    if not queue.exists():
        sys.exit(0)
    # Atomically claim the queue by renaming it aside before reading. Any
    # PostToolUse append that races with us then recreates the original queue
    # (to be picked up by the next Stop) instead of being silently dropped by a
    # read-then-unlink window.
    processing = queue.with_suffix(queue.suffix + ".processing")
    try:
        os.replace(queue, processing)
    except OSError:
        sys.exit(0)
    try:
        queued = list(dict.fromkeys(processing.read_text(encoding="utf-8").splitlines()))
    except OSError:
        processing.unlink(missing_ok=True)
        sys.exit(0)
    # Done with the claimed snapshot.
    processing.unlink(missing_ok=True)

    files = [f for f in queued if _is_code_file(f)][:MAX_FILES]
    if not files:
        sys.exit(0)

    plugin_root = Path(__file__).resolve().parent.parent
    review_script = (
        plugin_root / "skills" / "adversarial-review" / "scripts" / "run_review.py"
    )
    if not review_script.is_file():
        sys.exit(0)

    # Review files concurrently so total wall-clock is bounded by the slowest
    # file (per batch), not the sum of all files.
    with ThreadPoolExecutor(max_workers=min(len(files), MAX_WORKERS)) as pool:
        results = list(pool.map(lambda f: _review_one(review_script, f), files))

    sections = [f"### {name}\n\n{findings}" for name, findings, _ in results if findings]
    errors = [f"{name}: {err}" for name, findings, err in results if not findings and err]
    reviewed = ", ".join(name for name, _, _ in results)

    # Shown to the user whenever the review agent actually runs.
    banner = "**Launching review agent based on Antigravity SDK**"

    # No findings (or the reviewer couldn't run): just tell the user, don't block.
    if not sections:
        message = (
            f"{banner}\n\n"
            f"🔍 Adversarial review ran on {len(files)} file(s): {reviewed}. "
            "No issues found."
        )
        if errors:
            message += " Could not run: " + "; ".join(errors)
        print(json.dumps({"systemMessage": message}))
        sys.exit(0)

    # The full list of findings, formatted once and reused for both the
    # user-facing message and the instruction fed back to Claude.
    findings_report = (
        f"🔍 Adversarial review found issues in {len(files)} file(s) ({reviewed}):"
        "\n\n" + "\n\n".join(sections)
    )

    # Surface the complete findings to the USER first (systemMessage), so the
    # list is visible before Claude begins reconciling them.
    system_message = f"{banner}\n\n{findings_report}"

    # Feed the same findings back to Claude to reconcile (decision: block).
    reason = (
        findings_report + "\n\n"
        "Reconcile each finding (fix / justify / confirm already-mitigated) before "
        "considering this task complete, per the adversarial-review workflow."
    )
    print(json.dumps({
        "decision": "block",
        "reason": reason,
        "systemMessage": system_message,
    }))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # nothing to do

    if (payload.get("hook_event_name") or "") == "Stop":
        _review(payload)
    else:
        _record(payload)


if __name__ == "__main__":
    main()
