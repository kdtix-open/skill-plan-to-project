#!/usr/bin/env python3
"""manifest_io.py — read/write helpers for shallow-subsections manifests.

Implements Guard C marker mechanism from issue #68. A subtree is "marked
shallow" if EITHER:

  (a) ``manifests/<scope-issue>.json`` (relative to CWD), OR
      ``${SDLCA_AUDIT_DIR}/manifests/<scope-issue>.json`` (fallback)
      records ``shallow_subsections: true``, OR

  (b) The root issue carries the ``shallow:created`` label.

This module owns (a). Label detection lives in ``create_issues.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

MANIFEST_DIR = "manifests"


def _candidate_paths(scope_issue: int) -> list[Path]:
    paths = [Path.cwd() / MANIFEST_DIR / f"{scope_issue}.json"]
    base = os.environ.get("SDLCA_AUDIT_DIR")
    if base:
        paths.append(Path(base) / MANIFEST_DIR / f"{scope_issue}.json")
    paths.append(Path(os.path.expanduser("~/.sdlca")) / MANIFEST_DIR / f"{scope_issue}.json")
    # de-dup preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        s = str(p.resolve()) if p.exists() else str(p)
        if s in seen:
            continue
        seen.add(s)
        out.append(p)
    return out


def manifest_path_for(scope_issue: int, prefer_audit_dir: bool = False) -> Path:
    """Return the canonical write path for a scope manifest."""
    if prefer_audit_dir:
        base = os.environ.get("SDLCA_AUDIT_DIR") or os.path.expanduser("~/.sdlca")
        return Path(base) / MANIFEST_DIR / f"{scope_issue}.json"
    return Path.cwd() / MANIFEST_DIR / f"{scope_issue}.json"


def load_manifest(scope_issue: int) -> Optional[dict[str, Any]]:
    """Read a per-scope manifest from any candidate location, return dict or None."""
    for p in _candidate_paths(scope_issue):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def write_manifest(scope_issue: int, data: dict[str, Any], *, prefer_audit_dir: bool = False) -> Path:
    """Write a per-scope manifest atomically; create parent dirs."""
    path = manifest_path_for(scope_issue, prefer_audit_dir=prefer_audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def is_marked_shallow(scope_issue: int) -> bool:
    """Return True if the manifest records shallow_subsections=True."""
    data = load_manifest(scope_issue)
    if not data:
        return False
    return bool(data.get("shallow_subsections"))


def mark_shallow(
    scope_issue: int,
    *,
    justification: str,
    repo: str,
    plan_path: str,
    actor: str,
    prefer_audit_dir: bool = False,
) -> Path:
    """Set shallow_subsections=True in the per-scope manifest."""
    existing = load_manifest(scope_issue) or {}
    existing.update(
        {
            "scope_issue": scope_issue,
            "shallow_subsections": True,
            "shallow_subsections_justification": justification,
            "shallow_marked_by": actor,
            "repo": repo,
            "plan_path": plan_path,
        }
    )
    return write_manifest(scope_issue, existing, prefer_audit_dir=prefer_audit_dir)


def clear_shallow_debt(
    scope_issue: int,
    *,
    actor: str,
    closed_at: str,
    prefer_audit_dir: bool = False,
) -> Path:
    """Clear shallow flag and stamp graduation metadata."""
    existing = load_manifest(scope_issue) or {"scope_issue": scope_issue}
    existing["shallow_subsections"] = False
    existing["shallow_debt_closed_at"] = closed_at
    existing["shallow_debt_closed_by"] = actor
    return write_manifest(scope_issue, existing, prefer_audit_dir=prefer_audit_dir)
