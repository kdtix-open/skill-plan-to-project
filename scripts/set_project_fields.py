#!/usr/bin/env python3
"""
set_project_fields.py — Set Priority, Size, Status, and Issue Types on project items.

Usage:
    python scripts/set_project_fields.py \\
        --manifest manifest.json --config manifest-config.json \\
        --org ORG --project PROJECT_NUMBER
    python scripts/set_project_fields.py \\
        --manifest manifest.json --config manifest-config.json \\
        --org ORG --project PROJECT_NUMBER --issue-types-only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result


# ---------------------------------------------------------------------------
# GraphQL mutations
# ---------------------------------------------------------------------------

_ADD_TO_PROJECT_MUTATION = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {
    projectId: $projectId
    contentId: $contentId
  }) {
    item { id }
  }
}
"""

_SET_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { singleSelectOptionId: $optionId }
  }) {
    projectV2Item { id }
  }
}
"""

_SET_ISSUE_TYPE_MUTATION = """
mutation($issueId: ID!, $issueTypeId: ID!) {
  updateIssue(input: { id: $issueId, issueTypeId: $issueTypeId }) {
    issue { id issueType { name } }
  }
}
"""


def _graphql(query: str, variables: dict[str, str]) -> dict[str, Any]:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd += ["-f", f"{k}={v}"]
    result = _run(cmd)
    return json.loads(result.stdout)


def _add_to_project(project_id: str, node_id: str) -> str:
    """Add issue to project V2; return the project item ID."""
    data = _graphql(
        _ADD_TO_PROJECT_MUTATION,
        {"projectId": project_id, "contentId": node_id},
    )
    return data["data"]["addProjectV2ItemById"]["item"]["id"]


def _set_field(
    project_id: str,
    item_id: str,
    field_id: str,
    option_id: str,
) -> None:
    _graphql(
        _SET_FIELD_MUTATION,
        {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "optionId": option_id,
        },
    )


def _set_issue_type(node_id: str, issue_type_id: str) -> None:
    _graphql(
        _SET_ISSUE_TYPE_MUTATION,
        {"issueId": node_id, "issueTypeId": issue_type_id},
    )


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def set_project_fields(
    manifest: dict[str, Any],
    config: dict[str, Any],
    issue_types_only: bool = False,
) -> None:
    """Set Priority, Size, Status fields and Issue Types for all issues."""
    project_id = config["project_id"]
    field_ids = config["field_ids"]
    issue_type_ids = config["issue_type_ids"]

    for title, record in manifest.items():
        node_id = record["nodeId"]
        level = record["level"]
        priority = record.get("priority", "P1")
        size = record.get("size", "M")

        # Add to project and get item ID
        item_id = _add_to_project(project_id, node_id)

        if not issue_types_only:
            # Set Priority
            priority_option = field_ids["Priority"]["options"].get(priority)
            if priority_option:
                _set_field(
                    project_id,
                    item_id,
                    field_ids["Priority"]["id"],
                    priority_option,
                )

            # Set Size
            size_option = field_ids["Size"]["options"].get(size)
            if size_option:
                _set_field(
                    project_id,
                    item_id,
                    field_ids["Size"]["id"],
                    size_option,
                )

            # Set Status = Backlog
            backlog_option = field_ids["Status"]["options"].get("Backlog")
            if backlog_option:
                _set_field(
                    project_id,
                    item_id,
                    field_ids["Status"]["id"],
                    backlog_option,
                )

        # Set Issue Type
        type_id = issue_type_ids.get(level)
        if type_id:
            _set_issue_type(node_id, type_id)

        print(f"[fields] #{record['number']} {title} → {level} / {priority} / {size}")
        time.sleep(0.1)

    print(f"[OK] set_project_fields: {len(manifest)} issues updated")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set Priority, Size, Status and Issue Types on project items."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--project", required=True, type=int)
    parser.add_argument(
        "--issue-types-only",
        action="store_true",
        help="Only set Issue Types (skip Priority/Size/Status)",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    config_path = Path(args.config)

    if not manifest_path.exists():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)
    if not config_path.exists():
        print(f"[ERROR] config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))

    set_project_fields(manifest, config, issue_types_only=args.issue_types_only)


if __name__ == "__main__":
    main()
