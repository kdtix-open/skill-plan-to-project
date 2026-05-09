#!/usr/bin/env python3
"""audit_log.py — JSONL audit log for shallow-subsections invocations.

Implements Guard B from issue #68. Every successful shallow `create` /
`refresh` / `amend` invocation appends a structured JSONL entry recording
who ran the bypass, when, against which plan + repo, with what
justification, and which subsections were missing.

Default path: ``${SDLCA_AUDIT_DIR:-$HOME/.sdlca}/skill-plan-to-project-audit.jsonl``.
The directory is auto-created with mode 0o700 if missing.

A ``tail`` CLI verb (``python -m scripts.audit_log tail -n N``) pretty-prints
the last N entries for operator review.

Cross-reference: Self-Healing R-19; #271 RCA (2026-05-09).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

AUDIT_FILENAME = "skill-plan-to-project-audit.jsonl"


def _audit_dir() -> Path:
    base = os.environ.get("SDLCA_AUDIT_DIR")
    if base:
        return Path(base)
    return Path(os.path.expanduser("~/.sdlca"))


def audit_log_path() -> Path:
    """Resolve the audit log file path, honouring SDLCA_AUDIT_DIR."""
    return _audit_dir() / AUDIT_FILENAME


def ensure_audit_dir() -> Path:
    """Create the audit dir with mode 0o700 if missing; return its path."""
    d = _audit_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir(mode=...) honours umask, so re-chmod to be exact.
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def detect_actor_token_kind(token: Optional[str] = None) -> str:
    """Classify the active GitHub token as github-app | pat | unknown."""
    if token is None:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token.startswith("ghs_"):
        return "github-app"
    if token.startswith("ghp_") or token.startswith("github_pat_"):
        return "pat"
    return "unknown"


def detect_actor_user() -> str:
    """Best-effort: git config user.email -> $USER -> 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        email = result.stdout.strip()
        if email:
            return email
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("USER") or "unknown"


def detect_skill_version() -> str:
    """Read version from pyproject.toml; return 'unknown' on failure."""
    here = Path(__file__).resolve().parent.parent
    pyproject = here / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


def emit_audit_entry(
    *,
    command: str,
    plan_path: str,
    scope_issue: Optional[int],
    repo: str,
    shallow_items: list[dict[str, Any]],
    justification: str,
    severity: str = "P0",
    command_invocation: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append one JSONL entry to the audit log; return the entry dict."""
    ensure_audit_dir()
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor_user": detect_actor_user(),
        "actor_token_kind": detect_actor_token_kind(),
        "command": command,
        "plan_path": str(Path(plan_path).resolve()) if plan_path else "",
        "scope_issue": scope_issue,
        "repo": repo,
        "shallow_items": shallow_items,
        "justification": justification,
        "remediation_issue": None,
        "remediation_resolved": False,
        "severity": severity,
        "skill_version": detect_skill_version(),
        "command_invocation": command_invocation
        if command_invocation is not None
        else " ".join(sys.argv),
    }
    if extra:
        entry.update(extra)

    path = audit_log_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_entries(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Read all JSONL entries from the audit log."""
    p = path or audit_log_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _format_entry(entry: dict[str, Any]) -> str:
    sev = entry.get("severity", "?")
    ts = entry.get("ts", "?")
    cmd = entry.get("command", "?")
    repo = entry.get("repo", "?")
    scope = entry.get("scope_issue")
    actor = entry.get("actor_user", "?")
    kind = entry.get("actor_token_kind", "?")
    just = entry.get("justification", "")
    items = entry.get("shallow_items") or []
    head = f"[{sev}] {ts}  {cmd}  repo={repo}  scope=#{scope}  actor={actor} ({kind})"
    if just:
        head += f"\n      justification: {just}"
    if items:
        head += f"\n      shallow_items: {len(items)}"
        for it in items[:5]:
            level = it.get("level", "?")
            title = (it.get("title") or "")[:60]
            missing = ", ".join(it.get("missing") or [])
            head += f"\n        - [{level}] {title} (missing: {missing})"
        if len(items) > 5:
            head += f"\n        ... ({len(items) - 5} more)"
    return head


def _cmd_tail(args: argparse.Namespace) -> int:
    entries = read_entries()
    n = max(0, args.n)
    tail = entries[-n:] if n else entries
    if not tail:
        print(f"[audit_log] no entries at {audit_log_path()}", file=sys.stderr)
        return 0
    for entry in tail:
        print(_format_entry(entry))
        print()
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the skill-plan-to-project shallow-subsections audit log."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_tail = sub.add_parser("tail", help="Print the last N audit entries.")
    p_tail.add_argument("-n", type=int, default=10, help="Number of entries (default 10).")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "tail":
        return _cmd_tail(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
