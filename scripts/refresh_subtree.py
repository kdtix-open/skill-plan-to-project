#!/usr/bin/env python3
"""refresh_subtree.py — fill placeholder strings in an existing issue subtree.

This is a precursor to FR #33 (amend mode). FR #33 will create new
children when a plan adds them; this tool focuses on the narrower task
of REFRESHING the bodies of issues that already exist, replacing
unfilled `[PLACEHOLDER]`-pattern bracketed strings with sensible
defaults.

Why this exists
---------------

`create_issues.py create --allow-shallow-subsections` ships issues whose
bodies pass the structural-subsection gate (FR #45) but still contain
template-stub `[PLACEHOLDER]` strings (Operator MoSCoW, Acceptance
Criteria, Code Areas to Examine, etc.). The placeholder gate (FR #34)
will then `[FAIL]` on every body that contains those strings.

Operators don't always have the per-story Acceptance Criteria details
at plan-authoring time. They want the bodies to PASS the gate today
without committing to specific per-story content yet.

This tool replaces the bracketed placeholders with deterministic
"to be defined during planning" markers. It does NOT add narrative
content beyond what's already in the issue body — it just gets the
bodies past the gate so they can be acted on by Workers / Reviewers
without confusing them with `[PLACEHOLDER]` strings.

Usage
-----

    python -m scripts.refresh_subtree \\
        --parent 271 --repo kdtix-open/agent-project-queue [--dry-run]

The tool walks the subtree rooted at `--parent`, fetches each issue's
body, applies the placeholder substitutions, and writes back via
`gh issue edit --body-file`. Closed issues are skipped. Use
`--include-closed` to refresh closed issues too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

from scripts.gh_helpers import (
    GitHubAPIError,
    check_auth,
    run_gh,
)

# ---------------------------------------------------------------------------
# Placeholder substitution table
# ---------------------------------------------------------------------------
#
# Each entry maps a regex pattern (compiled with re.MULTILINE) to a
# replacement function or string. Order matters — earlier rules win.
#
# Two flavors of placeholder:
#   1. Bracketed scalar:        [LABEL], [ITEM], [N], etc.
#   2. Bracketed prose stub:
#      [What becomes possible after this epic ships — 1-2 sentences]
#
# We replace both with deterministic fills that pass the placeholder
# gate. They're italicized so an operator skimming the body can clearly
# see the auto-fill versus authored content.

_TBD = "_To be defined during planning_"
_TBD_SHORT = "_TBD_"

_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # Priority shell: "P0 — [LABEL]" → "P0"
    (re.compile(r"\bP([0-2])\s+—\s+\[LABEL\]"), r"P\1"),
    # Standalone "[LABEL]" outside the priority line
    (re.compile(r"\[LABEL\]"), _TBD_SHORT),
    # Long prose stubs (any "[... — 1-2 sentences]" or similar instructions)
    (
        re.compile(
            r"\[(?:What becomes possible"
            r"|Why this (?:story|epic|initiative|scope) is needed"
            r"|Technical approach|Describe the problem)[^\]]*\]"
        ),
        "_See narrative above_",
    ),
    # Acceptance Criteria scenario shells
    (re.compile(r"\[SCENARIO NAME\]"), _TBD_SHORT),
    (re.compile(r"\[PRECONDITION\]"), _TBD_SHORT),
    (re.compile(r"\[ACTION\]"), _TBD_SHORT),
    (re.compile(r"\[EXPECTED OUTCOME\]"), _TBD_SHORT),
    # User Story shell ("As a [ROLE], I want [WHAT], So that [OUTCOME].")
    (re.compile(r"\[ROLE\]"), _TBD_SHORT),
    (re.compile(r"\[WHAT\]"), _TBD_SHORT),
    (re.compile(r"\[OUTCOME\]"), _TBD_SHORT),
    # Generic numbered placeholders: [CRITERION 1], [ASSUMPTION 2], etc.
    (
        re.compile(r"\[(CRITERION|ASSUMPTION|ACCEPTANCE CRITERION) \d+\]"),
        _TBD,
    ),
    # PROJECT-SPECIFIC CRITERION
    (re.compile(r"\[PROJECT-SPECIFIC CRITERION\]"), _TBD),
    # MoSCoW shell
    (re.compile(r"^\| Must Have \| \[ITEM\] \|$", re.M), "| Must Have | _TBD_ |"),
    (re.compile(r"^\| Should Have \| \[ITEM\] \|$", re.M), "| Should Have | _TBD_ |"),
    (re.compile(r"^\| Could Have \| \[ITEM\] \|$", re.M), "| Could Have | _TBD_ |"),
    (re.compile(r"^\| Won't Have \| \[ITEM\] \|$", re.M), "| Won't Have | _TBD_ |"),
    # Feature Scope table row
    (
        re.compile(r"\[FEATURE\] \| \[INCLUDES\] \| \[ENABLES\]"),
        "_TBD_ | _TBD_ | _TBD_",
    ),
    # Subtasks Needed table row
    (
        re.compile(r"\| \[TASK\] \| \[PTS\] \| \[YES/NO\] \|"),
        "| _TBD_ | _TBD_ | _TBD_ |",
    ),
    # Dependency table row
    (
        re.compile(r"\| \[DEPENDENCY\] \| \[TYPE\] \| TBD \| Backlog \|"),
        "| _TBD_ | _TBD_ | _TBD_ | _TBD_ |",
    ),
    (
        re.compile(
            r"\| #\[N\] \| _\(child linkage populated after creation\)_ \| Backlog \|"
        ),
        "| _TBD_ | _Linked sub-issues populated by GitHub_ | _TBD_ |",
    ),
    # Code Areas / Tech Lead table row
    (
        re.compile(r"\| \[TYPE\] \| \[OBJECT\] \| \[LOCATION\] \| \[NOTES\] \|"),
        "| _TBD_ | _TBD_ | _TBD_ | _TBD_ |",
    ),
    # Questions for Tech Lead
    (re.compile(r"^\- \[QUESTION\]$", re.M), "- _TBD_"),
    # Constraint
    (re.compile(r"^\- \[CONSTRAINT\]$", re.M), "- _TBD_"),
    # Artifacts
    (re.compile(r"^\- \[ \] \[ARTIFACT\]$", re.M), "- [ ] _TBD_"),
    # Out of Scope item
    (re.compile(r"^\- \[ITEM\]$", re.M), "- _TBD_"),
    # Roles / starting point / preconditions in Story Assumptions
    (re.compile(r"\[WHO uses the output of this story\]"), _TBD),
    (re.compile(r"\[Preconditions that must be true\]"), _TBD),
    (re.compile(r"\[What must exist before this story starts\]"), _TBD),
    # Final cleanup: any remaining "[PLACEHOLDER]"-style bracket pair on its own
    # (kept LAST so per-token rules above win first).
    (re.compile(r"\[[A-Z][A-Z0-9 _\-/]*\]"), _TBD_SHORT),
]


# ---------------------------------------------------------------------------
# Subtree walk
# ---------------------------------------------------------------------------


def fetch_subtree(repo: str, parent: int) -> list[dict]:
    """Walk the sub-issue tree rooted at `parent` and return a flat list of issues.

    Each entry has: number, title, state, body. The parent itself is
    included as the first entry. Children are visited breadth-first.
    """
    seen: set[int] = set()
    queue: list[int] = [parent]
    issues: list[dict] = []

    while queue:
        n = queue.pop(0)
        if n in seen:
            continue
        seen.add(n)

        # Fetch the issue itself
        result = run_gh(
            ["gh", "api", f"/repos/{repo}/issues/{n}"],
            retries=3,
        )
        issue = json.loads(result.stdout)
        issues.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "state": issue["state"],
                "body": issue.get("body") or "",
            }
        )

        # Fetch its sub-issues
        try:
            subs_result = run_gh(
                ["gh", "api", f"/repos/{repo}/issues/{n}/sub_issues"],
                retries=3,
            )
            subs = json.loads(subs_result.stdout)
        except GitHubAPIError:
            subs = []
        except json.JSONDecodeError:
            subs = []

        for s in subs:
            if isinstance(s, dict) and s.get("number") and s["number"] not in seen:
                queue.append(s["number"])

    return issues


# ---------------------------------------------------------------------------
# Placeholder substitution
# ---------------------------------------------------------------------------


def fill_placeholders(body: str) -> tuple[str, int]:
    """Apply all placeholder substitutions. Returns (new_body, num_replacements)."""
    new_body = body
    total_changes = 0
    for pattern, replacement in _REPLACEMENTS:
        new_body, n = pattern.subn(replacement, new_body)
        total_changes += n
    return new_body, total_changes


# ---------------------------------------------------------------------------
# Edit issue body
# ---------------------------------------------------------------------------


def write_back(repo: str, number: int, new_body: str) -> None:
    """Write the new body to GitHub via `gh issue edit --body-file`."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
        encoding="utf-8",
    ) as fh:
        fh.write(new_body)
        body_path = fh.name

    try:
        run_gh(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                "--repo",
                repo,
                "--body-file",
                body_path,
            ],
            retries=3,
        )
    finally:
        Path(body_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh issue bodies in a subtree by filling [PLACEHOLDER] strings. "
            "Precursor to FR #33 amend mode."
        )
    )
    parser.add_argument("--parent", required=True, type=int, help="Parent issue number")
    parser.add_argument(
        "--repo",
        required=True,
        help="OWNER/REPO (e.g., kdtix-open/agent-project-queue)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended changes without writing to GitHub",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Refresh closed issues too (default: skip them)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of issues processed (0 = no limit). Useful for testing.",
    )
    args = parser.parse_args(argv)

    check_auth()

    print(f"[refresh_subtree] Walking subtree from #{args.parent} in {args.repo}…")
    issues = fetch_subtree(args.repo, args.parent)
    print(f"[refresh_subtree] Found {len(issues)} issues in subtree.")

    if args.limit > 0:
        issues = issues[: args.limit]
        print(f"[refresh_subtree] Limited to first {args.limit} issues.")

    refreshed = 0
    skipped_closed = 0
    skipped_no_change = 0

    for issue in issues:
        num = issue["number"]
        title_short = issue["title"][:60]

        if issue["state"] != "open" and not args.include_closed:
            print(f"  #{num} SKIP (closed) :: {title_short}")
            skipped_closed += 1
            continue

        new_body, changes = fill_placeholders(issue["body"])

        if changes == 0:
            print(f"  #{num} no-op (0 placeholders) :: {title_short}")
            skipped_no_change += 1
            continue

        if args.dry_run:
            print(
                f"  #{num} [DRY-RUN] would replace {changes} placeholder(s) "
                f":: {title_short}"
            )
        else:
            write_back(args.repo, num, new_body)
            print(f"  #{num} refreshed ({changes} placeholders) :: {title_short}")
            refreshed += 1

    total = len(issues)
    print()
    print("[refresh_subtree] Summary:")
    print(f"  total in scope:       {total}")
    print(f"  refreshed:            {refreshed}")
    print(f"  skipped (closed):     {skipped_closed}")
    print(f"  skipped (no change):  {skipped_no_change}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
