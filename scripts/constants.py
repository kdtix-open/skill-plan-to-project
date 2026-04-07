"""Shared constants for plan-to-project scripts."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# TDD sentinel
# ---------------------------------------------------------------------------

TDD_SENTINEL = (
    "- [ ] TDD followed: failing test written BEFORE implementation"
    " (Red phase confirmed before writing any production code)"
)

# ---------------------------------------------------------------------------
# Mutation detection
# ---------------------------------------------------------------------------

MUTATION_KEYWORDS = re.compile(
    r"\b(create|update|delete|resolve|write|set|build|implement)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Hierarchy level → Issue Type display name
# ---------------------------------------------------------------------------

LEVEL_TO_ISSUE_TYPE: dict[str, str] = {
    "scope": "Project Scope",
    "initiative": "Initiative",
    "epic": "Epic",
    "story": "User Story",
    "task": "Task",
}

# ---------------------------------------------------------------------------
# Security/Compliance section (appended for mutation issues)
# ---------------------------------------------------------------------------

SECURITY_SECTION = (
    "\n\n### Security/Compliance\n\n"
    "- [ ] Input validated before use\n"
    "- [ ] No secrets committed to source\n"
    "- [ ] Least-privilege gh CLI scopes used\n"
)
