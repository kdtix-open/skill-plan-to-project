#!/usr/bin/env python3
"""
set_relationships.py — Set sub-issue relationships and blocking labels.

Usage:
    python scripts/set_relationships.py --manifest manifest.json --repo REPO
    python scripts/set_relationships.py --manifest manifest.json \
        --repo REPO --labels-only

Reads manifest.json produced by create_issues.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.gh_helpers import (
    GitHubAPIError,
    get_issue_body,
    run_gh,
    update_issue_body,
)

# ---------------------------------------------------------------------------
# Sub-issue relationships
# ---------------------------------------------------------------------------


def set_sub_issues(manifest: dict[str, Any], repo: str) -> None:
    """Link each child issue to its parent using the sub-issues REST API."""
    by_title: dict[str, dict[str, Any]] = {v["title"]: v for v in manifest.values()}
    by_parent_ref: dict[str, list[dict[str, Any]]] = {}
    for record in manifest.values():
        parent_ref = record.get("parent_ref")
        if parent_ref:
            by_parent_ref.setdefault(parent_ref, []).append(record)

    linked = 0
    for parent_title, children in by_parent_ref.items():
        parent = by_title.get(parent_title)
        if not parent:
            print(
                f"[WARN] Parent '{parent_title}' not found in manifest — skipping",
                file=sys.stderr,
            )
            continue
        parent_number = parent["number"]
        for child in children:
            child_db_id = child["databaseId"]
            run_gh(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    "-H",
                    "Accept: application/vnd.github+json",
                    "-H",
                    "X-GitHub-Api-Version: 2022-11-28",
                    f"/repos/{repo}/issues/{parent_number}/sub_issues",
                    "-F",
                    f"sub_issue_id={child_db_id}",
                ]
            )
            print(f"[linked] #{parent_number} ← #{child['number']} ({child['title']})")
            linked += 1

    print(f"[OK] set_sub_issues: {linked} relationships linked")


# ---------------------------------------------------------------------------
# Blocking labels and dependency tables
# ---------------------------------------------------------------------------


def set_blocking_labels(manifest: dict[str, Any], repo: str) -> None:
    """Apply blocks/blocked labels and update dependency tables."""
    by_title: dict[str, dict[str, Any]] = {v["title"]: v for v in manifest.values()}
    # Collect all (blocker, blocked) pairs
    pairs: list[tuple[dict, dict]] = []
    for record in manifest.values():
        for blocking_ref in record.get("blocking", []):
            blocked = _find_by_ref(blocking_ref, by_title)
            if blocked:
                pairs.append((record, blocked))
            else:
                print(
                    f"[WARN] Blocking ref '{blocking_ref}' not found in manifest",
                    file=sys.stderr,
                )

    for blocker, blocked in pairs:
        # Add labels
        _add_label(repo, blocker["number"], "blocks")
        _add_label(repo, blocked["number"], "blocked")
        # Patch dependency table into blocked issue body
        _patch_dependency_table(repo, blocked, blocker)
        print(f"[labels] #{blocker['number']} blocks #{blocked['number']}")

    print(f"[OK] set_blocking_labels: {len(pairs)} blocking pairs processed")


def _find_by_ref(ref: str, by_title: dict[str, Any]) -> dict[str, Any] | None:
    """Find a manifest record by reference string.

    Uses exact match first, then falls back to substring matching.
    Warns if multiple substring matches are found.
    """
    ref_lower = ref.lower().strip()

    # 1. Exact match
    for title, record in by_title.items():
        if title.lower() == ref_lower:
            return record

    # 2. Substring match — collect all candidates
    candidates: list[tuple[str, dict[str, Any]]] = []
    for title, record in by_title.items():
        if ref_lower in title.lower():
            candidates.append((title, record))

    if len(candidates) == 1:
        return candidates[0][1]

    if len(candidates) > 1:
        titles = [c[0] for c in candidates]
        print(
            f"[WARN] Ambiguous blocking ref '{ref}' matched {len(candidates)} "
            f"titles: {titles}. Using first match.",
            file=sys.stderr,
        )
        return candidates[0][1]

    return None


def _add_label(repo: str, number: int, label: str) -> None:
    run_gh(
        [
            "gh",
            "issue",
            "edit",
            str(number),
            "--repo",
            repo,
            "--add-label",
            label,
        ],
        check=False,
    )


def _patch_dependency_table(
    repo: str,
    blocked: dict[str, Any],
    blocker: dict[str, Any],
) -> None:
    body = get_issue_body(repo, blocked["number"])
    dep_row = f"| #{blocker['number']} {blocker['title']} | Blocking | " f"Open |\n"
    dep_table = (
        "\n\n### Dependencies\n\n"
        "| Ticket | Description | Status |\n"
        "|--------|-------------|--------|\n"
        f"| #{blocker['number']} | {blocker['title']} | Open |\n"
    )
    if "### Dependencies" not in body and "## Dependencies" not in body:
        body += dep_table
    else:
        # Append row to existing table (before next ## header or end)
        body = body.replace(
            "| None | No blocking dependencies | N/A |",
            dep_row,
        )

    update_issue_body(repo, blocked["number"], body)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set sub-issue relationships and blocking labels."
    )
    parser.add_argument("--manifest", required=True, help="Path to manifest.json")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument(
        "--labels-only",
        action="store_true",
        help="Only apply blocking labels (skip sub-issue linking)",
    )
    args = parser.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(path.read_text(encoding="utf-8"))

    try:
        if not args.labels_only:
            set_sub_issues(manifest, args.repo)
        set_blocking_labels(manifest, args.repo)
    except GitHubAPIError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
