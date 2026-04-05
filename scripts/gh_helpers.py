"""Shared GitHub CLI helpers for plan-to-project scripts.

Provides:
- Custom exceptions (replacing sys.exit in library code)
- Subprocess wrapper with retry logic
- Common issue body read/update helpers
- GraphQL helper
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class GitHubAPIError(Exception):
    """Raised when a GitHub CLI command fails."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str = "") -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Command failed (rc={returncode}): {' '.join(cmd)}\n{stderr}".strip()
        )


class PreflightError(Exception):
    """Raised when preflight validation fails."""


class AuthError(Exception):
    """Raised when gh is not authenticated."""


# ---------------------------------------------------------------------------
# Subprocess wrapper with retry
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds
_RETRYABLE_PHRASES = ("rate limit", "502", "503", "504", "timed out", "ETIMEDOUT")


def run_gh(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    retries: int = _MAX_RETRIES,
) -> subprocess.CompletedProcess[str]:
    """Run a gh CLI command with optional retry on transient failures.

    Args:
        cmd: Command and arguments.
        check: If True, raise GitHubAPIError on non-zero exit.
        capture: If True, capture stdout/stderr.
        retries: Max retry attempts for transient failures.

    Returns:
        CompletedProcess result.

    Raises:
        GitHubAPIError: If command fails after all retries.
    """
    if retries < 1:
        retries = 1

    last_result: subprocess.CompletedProcess[str] | None = None

    for attempt in range(retries):
        result = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        if result.returncode == 0:
            return result

        last_result = result
        stderr_lower = (result.stderr or "").lower()

        # Only retry on transient failures
        if attempt < retries - 1 and any(
            phrase in stderr_lower for phrase in _RETRYABLE_PHRASES
        ):
            wait = _RETRY_BACKOFF_BASE * (2**attempt)
            print(
                f"[RETRY] Attempt {attempt + 1}/{retries} failed, "
                f"retrying in {wait:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
            continue

        # Non-retryable failure — break immediately
        break

    if check and last_result is not None and last_result.returncode != 0:
        raise GitHubAPIError(
            cmd,
            last_result.returncode,
            (last_result.stderr or "").strip() if capture else "",
        )

    return last_result  # type: ignore[return-value]


def check_auth() -> None:
    """Verify gh CLI is authenticated.

    Raises:
        AuthError: If gh auth status fails.
    """
    result = subprocess.run(
        ["gh", "auth", "status"],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if result.returncode != 0:
        raise AuthError("gh not authenticated. Run: gh auth login")


# ---------------------------------------------------------------------------
# GraphQL helper
# ---------------------------------------------------------------------------


def graphql(query: str, variables: dict[str, str]) -> dict[str, Any]:
    """Execute a GraphQL query via gh api.

    Raises:
        GitHubAPIError: On failure.
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd += ["-f", f"{k}={v}"]
    result = run_gh(cmd)
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Issue body helpers
# ---------------------------------------------------------------------------


def get_issue_body(repo: str, number: int) -> str:
    """Fetch an issue body via gh CLI."""
    result = run_gh(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "body",
            "--jq",
            ".body",
        ]
    )
    return result.stdout.strip()


def get_issue_labels(repo: str, number: int) -> list[str]:
    """Fetch an issue's labels via gh CLI."""
    result = run_gh(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "labels",
            "--jq",
            "[.labels[].name]",
        ]
    )
    return json.loads(result.stdout.strip() or "[]")


def update_issue_body(repo: str, number: int, body: str) -> None:
    """Update an issue body via gh CLI using a temp file."""
    fd, tmp = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        run_gh(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                "--repo",
                repo,
                "--body-file",
                tmp,
            ]
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
