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
import re
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

_LABEL_SPECS = {
    "blocks": {
        "color": "5319E7",
        "description": "Blocks another issue",
    },
    "blocked": {
        "color": "B60205",
        "description": "Blocked by another issue",
    },
}

_DEPENDENCIES_SECTION_RE = re.compile(
    r"(?ms)^(?P<heading>#{2,6}\s+Dependencies\s*$)\n*(?P<section>.*?)(?=^#{2,6}\s+\S|\Z)"
)
_BLOCKED_BY_LINE_RE = re.compile(r"(?m)^Blocked by:\s*.*(?:\n)?")


def set_blocking_labels(manifest: dict[str, Any], repo: str) -> None:
    """Apply native blocker relationships, labels, and body metadata."""
    by_title: dict[str, dict[str, Any]] = {v["title"]: v for v in manifest.values()}
    blocked_by_number: dict[int, dict[str, Any]] = {}
    blockers_by_blocked: dict[int, list[dict[str, Any]]] = {}
    pairs: list[tuple[dict, dict]] = []

    for record in manifest.values():
        for blocking_ref in record.get("blocking", []):
            # record is the blocker (it declares "Blocks: <ref>"); the
            # referenced issue is the one being blocked.
            blocked = _find_by_ref(blocking_ref, by_title)
            if blocked:
                blocker = record
                pairs.append((blocker, blocked))
                blocked_by_number[blocked["number"]] = blocked
                blockers_by_blocked.setdefault(blocked["number"], []).append(blocker)
            else:
                print(
                    (
                        f"[WARN] Blocking ref '{blocking_ref}' not found in manifest "
                        f"for #{record['number']} {record['title']}"
                    ),
                    file=sys.stderr,
                )

    if not pairs:
        print("[OK] set_blocking_labels: 0 blocking pairs processed")
        return

    _ensure_label_exists(repo, "blocks")
    _ensure_label_exists(repo, "blocked")

    for blocked_number, blocked in blocked_by_number.items():
        blockers = _dedupe_and_sort_blockers(blockers_by_blocked[blocked_number])
        native_blockers = _get_existing_blocker_ids(repo, blocked_number)

        for blocker in blockers:
            if blocker["databaseId"] not in native_blockers:
                _create_native_blocker_relationship(repo, blocked_number, blocker)
            _add_label(repo, blocker["number"], "blocks")
            print(f"[labels] #{blocker['number']} blocks #{blocked_number}")

        _add_label(repo, blocked_number, "blocked")
        _patch_dependency_table(repo, blocked, blockers)

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
        title_lower = title.lower()
        if ref_lower in title_lower or title_lower in ref_lower:
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
        ]
    )


def _patch_dependency_table(
    repo: str,
    blocked: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> None:
    body = get_issue_body(repo, blocked["number"])
    update_issue_body(
        repo,
        blocked["number"],
        _normalize_dependency_metadata(body, blockers),
    )


def _ensure_label_exists(repo: str, label: str) -> None:
    result = run_gh(
        [
            "gh",
            "label",
            "list",
            "--repo",
            repo,
            "--json",
            "name",
            "--limit",
            "200",
        ]
    )
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        payload = []
    existing = {
        entry["name"]
        for entry in payload
        if isinstance(entry, dict) and entry.get("name")
    }
    if label in existing:
        return

    spec = _LABEL_SPECS[label]
    run_gh(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            repo,
            "--color",
            spec["color"],
            "--description",
            spec["description"],
        ]
    )


def _get_existing_blocker_ids(repo: str, blocked_number: int) -> set[int]:
    result = run_gh(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            f"/repos/{repo}/issues/{blocked_number}/dependencies/blocked_by",
        ]
    )
    payload = json.loads(result.stdout or "{}")
    if not isinstance(payload, dict):
        return set()
    dependencies = payload.get("dependencies", [])
    if not isinstance(dependencies, list):
        return set()
    return {
        dependency["issue"]["id"]
        for dependency in dependencies
        if isinstance(dependency, dict)
        and dependency.get("issue", {}).get("id") is not None
    }


def _create_native_blocker_relationship(
    repo: str,
    blocked_number: int,
    blocker: dict[str, Any],
) -> None:
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
            f"/repos/{repo}/issues/{blocked_number}/dependencies/blocked_by",
            "-F",
            f"issue_id={blocker['databaseId']}",
        ]
    )


def _dedupe_and_sort_blockers(
    blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: dict[int, dict[str, Any]] = {}
    for blocker in blockers:
        deduped[blocker["number"]] = blocker
    return [deduped[number] for number in sorted(deduped)]


def _normalize_dependency_metadata(
    body: str,
    blockers: list[dict[str, Any]],
) -> str:
    blockers = _dedupe_and_sort_blockers(blockers)
    blocked_by_line = "Blocked by: " + ", ".join(
        f"#{blocker['number']}" for blocker in blockers
    )
    dependencies_table = "\n".join(
        [
            "| Ticket | Description | Status |",
            "|--------|-------------|--------|",
            *[
                f"| #{blocker['number']} | {blocker['title']} | Open |"
                for blocker in blockers
            ],
        ]
    )
    normalized_body = _BLOCKED_BY_LINE_RE.sub("", body).rstrip()

    match = _DEPENDENCIES_SECTION_RE.search(normalized_body)
    section_heading = match.group("heading") if match else "### Dependencies"
    section_body = f"{section_heading}\n\n{blocked_by_line}\n\n{dependencies_table}"

    if match:
        before = normalized_body[: match.start()].rstrip()
        after = normalized_body[match.end() :].lstrip("\n")
        parts = [part for part in (before, section_body, after) if part]
        return "\n\n".join(parts) + "\n"

    return f"{normalized_body}\n\n{section_body}\n"


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
