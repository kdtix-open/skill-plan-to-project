#!/usr/bin/env python3
"""
queue_order.py — Apply priority algorithm to produce a recommended Story order.

Usage:
    python scripts/queue_order.py \\
        --manifest manifest.json --repo REPO --project PROJECT_NUMBER

Algorithm:
    Eligible = Status=Backlog AND no `blocked` label AND parent In Progress/Done
    Sort: P0 > P1 > P2, S < M < L (by size ascending), lowest issue # tiebreaker
    Output: ordered list of Story issues

Writes queue-order.json and prints the ordered list to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Priority / size ordering
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
SIZE_ORDER = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}


def _priority_key(record: dict[str, Any]) -> tuple[int, int, int]:
    p = PRIORITY_ORDER.get(record.get("priority", "P1"), 1)
    s = SIZE_ORDER.get(record.get("size", "M"), 2)
    n = record.get("number", 9999)
    return (p, s, n)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and result.returncode != 0:
        print(f"[ERROR] {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result


def _get_issue_labels(repo: str, number: int) -> list[str]:
    result = _run(
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


def _get_project_status(repo: str, number: int) -> str:
    """Get the Status field value of an issue (best-effort via gh)."""
    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "projectItems",
            "--jq",
            '[.projectItems[].fieldValues[].value? // empty] | first // "Backlog"',
        ],
        check=False,
    )
    return result.stdout.strip() or "Backlog"


def _get_parent_status(
    record: dict[str, Any],
    manifest: dict[str, Any],
    repo: str,
) -> str:
    """Get the project status of the parent issue."""
    parent_ref = record.get("parent_ref")
    if not parent_ref:
        return "Done"  # No parent — treat as eligible
    parent = next(
        (v for v in manifest.values() if v.get("title") == parent_ref),
        None,
    )
    if not parent:
        return "Done"
    return _get_project_status(repo, parent["number"])


# ---------------------------------------------------------------------------
# Main algorithm
# ---------------------------------------------------------------------------


def compute_queue_order(
    manifest: dict[str, Any],
    repo: str,
    statuses: dict[int, str] | None = None,
    labels_map: dict[int, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Compute the recommended execution order for Story-level issues.

    Args:
        manifest: Output of create_issues.py
        repo: GitHub repo (owner/name)
        statuses: Optional pre-fetched {number: status} map (for testing)
        labels_map: Optional pre-fetched {number: [labels]} map (for testing)

    Returns:
        Ordered list of story records.
    """
    stories = [r for r in manifest.values() if r.get("level") == "story"]

    eligible: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []

    for record in stories:
        number = record["number"]

        # Get labels (real or mocked)
        if labels_map is not None:
            labels = labels_map.get(number, [])
        else:
            labels = _get_issue_labels(repo, number)

        # Blocked issues are ineligible
        if "blocked" in labels:
            ineligible.append({**record, "_reason": "blocked"})
            continue

        # Check status is Backlog
        if statuses is not None:
            status = statuses.get(number, "Backlog")
        else:
            status = _get_project_status(repo, number)

        if status not in ("Backlog", ""):
            ineligible.append({**record, "_reason": f"status={status}"})
            continue

        # Check parent is In Progress or Done
        if statuses is not None:
            parent_ref = record.get("parent_ref")
            parent = next(
                (v for v in manifest.values() if v.get("title") == parent_ref),
                None,
            )
            parent_status = (
                statuses.get(parent["number"], "In Progress") if parent else "Done"
            )
        else:
            parent_status = _get_parent_status(record, manifest, repo)

        if parent_status not in ("In Progress", "In progress", "Done", "In Review"):
            ineligible.append({**record, "_reason": f"parent_status={parent_status}"})
            continue

        eligible.append(record)

    ordered = sorted(eligible, key=_priority_key)
    return ordered


def run_queue_order(manifest: dict[str, Any], repo: str) -> list[dict[str, Any]]:
    """Run the full queue order computation and write results."""
    ordered = compute_queue_order(manifest, repo)

    print("\n=== Recommended Queue Order (Stories) ===")
    for i, record in enumerate(ordered, 1):
        print(
            f"  {i}. #{record['number']} [{record.get('priority','P1')}/"
            f"{record.get('size','M')}] {record['title']}"
        )

    output = [
        {
            "rank": i,
            "number": r["number"],
            "title": r["title"],
            "priority": r.get("priority", "P1"),
            "size": r.get("size", "M"),
        }
        for i, r in enumerate(ordered, 1)
    ]

    out_path = Path("queue-order.json")
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n[OK] queue-order.json written ({len(output)} stories)")
    return ordered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute recommended story execution order."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--project", required=True, type=int)
    args = parser.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(path.read_text(encoding="utf-8"))
    run_queue_order(manifest, args.repo)


if __name__ == "__main__":
    main()
