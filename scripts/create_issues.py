#!/usr/bin/env python3
"""
create-issues.py — Parse a markdown plan and create all GitHub issues.

Usage:
    python scripts/create-issues.py preflight --org ORG --repo REPO --project N
    python scripts/create-issues.py parse --plan PLAN_FILE
    python scripts/create-issues.py create --plan PLAN_FILE \
        --org ORG --repo REPO --project N

Subcommands:
    preflight   Validate Issue Type IDs and project V2 field IDs.
                Writes manifest-config.json.
    parse       Parse a markdown plan and print a hierarchy summary.
    create      Run preflight, parse, then create all issues top-down.
                Writes manifest.json with number/nodeId/databaseId per issue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.constants import (
    LEVEL_TO_ISSUE_TYPE,
    MUTATION_KEYWORDS,
    SECURITY_SECTION,
    TDD_SENTINEL,
)
from scripts.gh_helpers import (
    AuthError,
    GitHubAPIError,
    PreflightError,
    check_auth,
    run_gh,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEVEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("scope", re.compile(r"^#\s+(Project\s+Scope:|PS-)", re.IGNORECASE)),
    ("initiative", re.compile(r"^##\s+(Initiative:|INIT-)", re.IGNORECASE)),
    ("epic", re.compile(r"^###\s+(Epic:|EP-)", re.IGNORECASE)),
    ("story", re.compile(r"^####\s+(Story:|User\s+Story:)", re.IGNORECASE)),
    ("task", re.compile(r"^#####\s+Task:", re.IGNORECASE)),
]

PRIORITY_RE = re.compile(r"^Priority:\s*(P[012])", re.IGNORECASE | re.MULTILINE)
SIZE_RE = re.compile(r"^Size:\s*(XS|S|M|L|XL)\b", re.IGNORECASE | re.MULTILINE)
BLOCKS_RE = re.compile(r"^Blocks?:\s*(.+)", re.IGNORECASE | re.MULTILINE)

# Directory containing asset templates (resolved relative to repo root)
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# ---------------------------------------------------------------------------
# Phase 1 / Task #15: parse_plan
# ---------------------------------------------------------------------------


def parse_plan(filepath: str) -> dict[str, Any]:
    """Parse a KDTIX markdown plan into a 5-level hierarchy dict.

    Args:
        filepath: Path to the markdown plan file.

    Returns:
        Dict with keys: scope, initiative, epics, stories, tasks.
        Each item has: title, description, priority, size, blocking,
        and (for epics/stories/tasks) parent_ref.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Plan file not found: {filepath}")

    lines = path.read_text(encoding="utf-8").splitlines()

    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    def _flush() -> None:
        if current is not None:
            current["description"] = "\n".join(body_lines).strip()
            body_body = current["description"]
            current["priority"] = _extract_priority(body_body)
            current["size"] = _extract_size(body_body)
            current["blocking"] = _extract_blocking(body_body)
            items.append(current)

    for line in lines:
        level = _detect_level(line)
        if level is not None:
            _flush()
            body_lines = []
            title = _strip_header_prefix(line)
            current = {
                "level": level,
                "title": title,
                "description": "",
                "priority": "P1",
                "size": "M",
                "blocking": [],
                "parent_ref": None,
            }
        else:
            body_lines.append(line)
    _flush()

    return _build_hierarchy(items)


def _detect_level(line: str) -> str | None:
    for level, pattern in LEVEL_PATTERNS:
        if pattern.match(line):
            return level
    return None


def _strip_header_prefix(line: str) -> str:
    """Remove markdown heading hashes and known prefixes, return clean title."""
    stripped = re.sub(r"^#+\s+", "", line).strip()
    prefixes = [
        r"^Project\s+Scope:\s*",
        r"^PS-\S+\s*",
        r"^Initiative:\s*",
        r"^INIT-\S+\s*",
        r"^Epic:\s*",
        r"^EP-\S+\s*",
        r"^(User\s+)?Story:\s*",
        r"^Task:\s*",
    ]
    for prefix in prefixes:
        stripped = re.sub(prefix, "", stripped, flags=re.IGNORECASE).strip()
    return stripped


def _extract_priority(text: str) -> str:
    m = PRIORITY_RE.search(text)
    return m.group(1).upper() if m else "P1"


def _extract_size(text: str) -> str:
    m = SIZE_RE.search(text)
    return m.group(1).upper() if m else "M"


def _extract_blocking(text: str) -> list[str]:
    matches = BLOCKS_RE.findall(text)
    result = []
    for m in matches:
        for ref in re.split(r",\s*", m.strip()):
            ref = ref.strip()
            if ref:
                result.append(ref)
    return result


def _build_hierarchy(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Assign parent_refs and split items into hierarchy buckets."""
    scope = next((i for i in items if i["level"] == "scope"), None)
    initiative = next((i for i in items if i["level"] == "initiative"), None)
    epics = [i for i in items if i["level"] == "epic"]
    stories = [i for i in items if i["level"] == "story"]
    tasks = [i for i in items if i["level"] == "task"]

    # Assign parent refs: each item gets the title of the most recently seen
    # item one level above it in the flat list.
    last: dict[str, str | None] = {
        "scope": scope["title"] if scope else None,
        "initiative": initiative["title"] if initiative else None,
        "epic": None,
        "story": None,
    }

    for item in items:
        level = item["level"]
        if level == "epic":
            item["parent_ref"] = last["initiative"]
            last["epic"] = item["title"]
        elif level == "story":
            item["parent_ref"] = last["epic"]
            last["story"] = item["title"]
        elif level == "task":
            item["parent_ref"] = last["story"]

    return {
        "scope": scope,
        "initiative": initiative,
        "epics": epics,
        "stories": stories,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# Phase 1 / Task #16: preflight
# ---------------------------------------------------------------------------

_ISSUE_TYPES_QUERY = """
query($org: String!) {
  organization(login: $org) {
    issueTypes(first: 20) {
      nodes { id name }
    }
  }
}
"""

_PROJECT_FIELDS_QUERY = """
query($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      fields(first: 30) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id name options { id name }
          }
        }
      }
    }
  }
}
"""

EXPECTED_ISSUE_TYPES = {
    "project scope": "scope",
    "initiative": "initiative",
    "epic": "epic",
    "user story": "story",
    "task": "task",
}

EXPECTED_FIELDS = ("Priority", "Size", "Status")


def preflight(
    org: str, repo: str, project_number: int, output_dir: Path | None = None
) -> dict[str, Any]:
    """Validate org Issue Types and project V2 field IDs.

    Returns a config dict with issue_type_ids and field_ids.

    Raises:
        AuthError: If gh is not authenticated.
        PreflightError: If required types or fields are missing.
        GitHubAPIError: If API calls fail.
    """
    check_auth()

    out = output_dir or Path(".")

    # --- Issue Types ---
    result = run_gh(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_ISSUE_TYPES_QUERY}",
            "-f",
            f"org={org}",
        ],
    )
    data = json.loads(result.stdout)
    nodes = (
        data.get("data", {})
        .get("organization", {})
        .get("issueTypes", {})
        .get("nodes", [])
    )
    if not nodes:
        raise PreflightError(
            f"No Issue Types found for org '{org}'. "
            "Configure Issue Types (Project Scope, Initiative, Epic, User Story, Task) "
            f"at https://github.com/organizations/{org}/settings/issue-types"
        )

    issue_type_ids: dict[str, str] = {}
    for node in nodes:
        key = node["name"].lower()
        for expected, alias in EXPECTED_ISSUE_TYPES.items():
            if expected in key:
                issue_type_ids[alias] = node["id"]
                break

    missing_types = [
        k for k in EXPECTED_ISSUE_TYPES.values() if k not in issue_type_ids
    ]
    if missing_types:
        raise PreflightError(
            f"Missing Issue Types in org '{org}': {missing_types}. "
            "All 5 types (Project Scope, Initiative, Epic, "
            "User Story, Task) are required."
        )

    # --- Project V2 fields ---
    result = run_gh(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_PROJECT_FIELDS_QUERY}",
            "-f",
            f"org={org}",
            "-F",
            f"number={project_number}",
        ],
    )
    data = json.loads(result.stdout)
    proj = data.get("data", {}).get("organization", {}).get("projectV2", {})
    if not proj:
        raise PreflightError(f"Project #{project_number} not found in org '{org}'.")

    project_id = proj["id"]
    field_nodes = proj.get("fields", {}).get("nodes", [])

    field_ids: dict[str, Any] = {}
    for node in field_nodes:
        name = node.get("name", "")
        if name in EXPECTED_FIELDS:
            field_ids[name] = {
                "id": node["id"],
                "options": {opt["name"]: opt["id"] for opt in node.get("options", [])},
            }

    missing_fields = [f for f in EXPECTED_FIELDS if f not in field_ids]
    if missing_fields:
        raise PreflightError(
            f"Missing project fields: {missing_fields}. "
            "Project V2 must have Priority, Size, and Status single-select fields."
        )

    config = {
        "project_id": project_id,
        "org": org,
        "repo": repo,
        "project_number": project_number,
        "issue_type_ids": issue_type_ids,
        "field_ids": field_ids,
    }

    config_path = out / "manifest-config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"[OK] preflight passed → {config_path}")
    return config


# ---------------------------------------------------------------------------
# Phase 3 / Task #18: generate_body (template-based)
# ---------------------------------------------------------------------------

# Cache loaded templates
_template_cache: dict[str, str] = {}


def _load_template(level: str) -> str:
    """Load a markdown template from assets/, with caching."""
    if level not in _template_cache:
        filename = f"template-{level}.md"
        # For story, the template is named template-story.md
        path = _ASSETS_DIR / filename
        if path.exists():
            _template_cache[level] = path.read_text(encoding="utf-8")
        else:
            _template_cache[level] = ""
    return _template_cache[level]


def generate_body(
    item: dict[str, Any],
    level: str,
    manifest: dict[str, int] | None = None,
) -> str:
    """Generate a template-compliant issue body for the given item and level."""
    template_text = _load_template(level)

    if template_text:
        body = _render_template(template_text, item, level)
    else:
        # Fallback to programmatic generation
        generators = {
            "scope": _body_scope,
            "initiative": _body_initiative,
            "epic": _body_epic,
            "story": _body_story,
            "task": _body_task,
        }
        body = generators[level](item, manifest or {})

    # Auto-inject TDD sentinel if missing
    if "TDD followed" not in body:
        if "I Know I Am Done When" in body:
            body = body.replace(
                "I Know I Am Done When\n",
                f"I Know I Am Done When\n{TDD_SENTINEL}\n",
            )
        else:
            body += f"\n\n## I Know I Am Done When\n{TDD_SENTINEL}\n"

    # Auto-inject Security/Compliance if mutation keywords present and missing
    title_and_body = item.get("title", "") + " " + body
    if MUTATION_KEYWORDS.search(title_and_body) and "Security/Compliance" not in body:
        body += SECURITY_SECTION

    return body


def _render_template(template_text: str, item: dict[str, Any], level: str) -> str:
    """Render an asset template by substituting placeholders."""
    title = item.get("title", "")
    desc = item.get("description", "")
    priority = item.get("priority", "P1")
    size = item.get("size", "M")
    parent = item.get("parent_ref", "")
    vision_text = desc or "[Vision statement]"
    obj_text = desc or "[Objective]"
    summary_text = desc or "[1-sentence summary]"
    impl_text = desc or "[What this task implements]"

    replacements = {
        "[TITLE]": title,
        "[CODE] [TITLE]": title,
        "[CODE]": "",
        "[STATUS]": "Backlog",
        "[PRIORITY]": priority,
        "[P0/P1/P2]": priority,
        "[P0/P1/P2] — [LABEL]": priority,
        "[SIZE]": size,
        "[TIMEFRAME]": "TBD",
        "[OWNER]": "TBD",
        (
            "[VISION — 1-2 sentences on the end" " state and the value delivered]"
        ): vision_text,
        (
            "[Why this initiative exists and what" " problem it solves — 2-4 sentences]"
        ): obj_text,
        "[Why this epic exists — 2-4 sentences]": obj_text,
        ("[1-sentence summary of what" " this story delivers]"): summary_text,
        ("[1-3 sentences describing exactly" " what this task implements]"): impl_text,
        "[VERSION]": "TBD",
        "[POINTS]": "TBD",
        "[HOURS]": "TBD",
        "[POINTS] pts": "TBD",
        "[HOURS] hrs": "TBD",
        "#[N] [EPIC TITLE]": parent or "TBD",
        "#[N] [STORY TITLE]": parent or "TBD",
        "[Backend/Frontend/Infrastructure/QA]": "Backend",
    }

    rendered = template_text
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    # Inject parent reference for levels that have one
    if parent and level in ("epic", "story", "task"):
        if "> **Parent" not in rendered:
            # Add parent ref after the first heading line
            lines = rendered.split("\n", 1)
            if len(lines) == 2:
                parent_labels = {
                    "epic": "Parent Initiative",
                    "story": "Parent Epic",
                    "task": "Parent Story",
                }
                label = parent_labels.get(level, "Parent")
                rendered = f"{lines[0]}\n\n> **{label}**: {parent}\n{lines[1]}"

    return rendered


def _meta_block(item: dict[str, Any], level: str) -> str:
    priority = item.get("priority", "P1")
    size = item.get("size", "M")
    return (
        f"> **Status**: Backlog\n"
        f"> **Priority**: {priority}\n"
        f"> **Size**: {size}\n"
        f"> **Type**: {LEVEL_TO_ISSUE_TYPE[level]}\n"
    )


def _done_when_block(extra: list[str] | None = None) -> str:
    lines = extra or ["[PROJECT-SPECIFIC CRITERION]"]
    criteria = "\n".join(f"- [ ] {c}" for c in lines)
    return f"## I Know I Am Done When\n\n" f"{criteria}\n" f"{TDD_SENTINEL}\n"


def _moscow_block(musts: list[str]) -> str:
    rows = "\n".join(f"| Must Have | {m} |" for m in musts)
    return (
        "## MoSCoW Classification\n\n"
        "| Priority | Item |\n"
        "|----------|------|\n"
        f"{rows}\n"
    )


def _assumptions_block(items: list[str] | None = None) -> str:
    lines = items or ["[Add assumptions here]"]
    return "## Assumptions\n\n" + "\n".join(f"- {a}" for a in lines) + "\n"


def _body_scope(item: dict[str, Any], manifest: dict) -> str:
    title = item.get("title", "")
    desc = item.get("description", "")
    return (
        f"# Project Scope: {title}\n\n"
        f"{_meta_block(item, 'scope')}\n"
        "---\n\n"
        "## Vision\n\n"
        f"{desc or '[Vision statement]'}\n\n"
        "---\n\n"
        "## Business Problem & Current State\n\n"
        "[Describe the problem being solved]\n\n"
        "---\n\n"
        "## Success Criteria\n\n"
        "- [ ] [Criterion 1]\n\n"
        "---\n\n"
        f"{_assumptions_block()}\n"
        "---\n\n"
        "## Out of Scope\n\n"
        "- [Item]\n\n"
        "---\n\n"
        f"{_moscow_block(['[Must Have item]'])}\n"
        "---\n\n"
        f"{_done_when_block()}\n"
    )


def _body_initiative(item: dict[str, Any], manifest: dict) -> str:
    title = item.get("title", "")
    desc = item.get("description", "")
    return (
        f"# Initiative: {title}\n\n"
        f"{_meta_block(item, 'initiative')}\n"
        "---\n\n"
        "## PRODUCT SECTION\n\n"
        "### Objective\n\n"
        f"{desc or '[Objective]'}\n\n"
        "---\n\n"
        "### Release Value\n\n"
        "[What becomes possible after this ships]\n\n"
        "---\n\n"
        "### Success Criteria\n\n"
        "- [ ] [Criterion]\n\n"
        "---\n\n"
        "### Feature Scope\n\n"
        "| # | Feature/Capability | What It Includes | What It Enables |\n"
        "|---|-------------------|------------------|-----------------|\n"
        "| 1 | [Feature] | [Includes] | [Enables] |\n\n"
        "---\n\n"
        f"{_assumptions_block()}\n"
        "---\n\n"
        "### Dependencies\n\n"
        "| Dependency | Type | Owner | Status |\n"
        "|------------|------|-------|--------|\n"
        "| [Dependency] | [Type] | [Owner] | [Status] |\n\n"
        "---\n\n"
        f"{_done_when_block()}\n"
    )


def _body_epic(item: dict[str, Any], manifest: dict) -> str:
    title = item.get("title", "")
    desc = item.get("description", "")
    parent = item.get("parent_ref", "")
    return (
        f"# Epic: {title}\n\n"
        f"> **Parent Initiative**: {parent}\n\n"
        f"{_meta_block(item, 'epic')}\n"
        "---\n\n"
        "## PRODUCT SECTION\n\n"
        "### Objective\n\n"
        f"{desc or '[Objective]'}\n\n"
        "### Release Value\n\n"
        "[What becomes possible after this epic ships]\n\n"
        "### Success Criteria\n\n"
        "- [ ] [Criterion]\n\n"
        "---\n\n"
        "### Feature Scope\n\n"
        "| # | Feature/Capability | What It Includes | What It Enables |\n"
        "|---|-------------------|------------------|-----------------|\n"
        "| 1 | [Feature] | [Includes] | [Enables] |\n\n"
        "---\n\n"
        f"{_assumptions_block()}\n"
        "---\n\n"
        "### Dependencies\n\n"
        "| Dependency | Type | Owner | Status |\n"
        "|------------|------|-------|--------|\n"
        "| [Dependency] | [Type] | [Owner] | [Status] |\n\n"
        "---\n\n"
        f"{_done_when_block()}\n"
    )


def _body_story(item: dict[str, Any], manifest: dict) -> str:
    title = item.get("title", "")
    desc = item.get("description", "")
    parent = item.get("parent_ref", "")
    priority = item.get("priority", "P1")
    size = item.get("size", "M")
    return (
        f"# User Story: {title}\n\n"
        f"> **Parent Epic**: {parent}\n"
        f"> **Status**: Backlog | **Priority**: {priority} | **Size**: {size}\n\n"
        "## PRODUCT SECTION\n\n"
        "### User Story\n\n"
        "```\n"
        "As a [role],\n"
        "I want [what],\n"
        "So that [outcome].\n"
        "```\n\n"
        "### TL;DR\n\n"
        f"{desc or '[1-sentence summary]'}\n\n"
        "### Why This Matters\n\n"
        "[Why this story is needed — 2-3 sentences]\n\n"
        f"{_assumptions_block()}\n"
        f"{_moscow_block(['[Must Have item]'])}\n\n"
        "### Dependencies\n\n"
        "| Ticket | Description | Status |\n"
        "|--------|-------------|--------|\n"
        "| None | No blocking dependencies | N/A |\n\n"
        f"{_done_when_block()}\n"
        "### Acceptance Criteria\n\n"
        "**Scenario 1**: [Name]\n"
        "- **Given**: [Precondition]\n"
        "- **When**: [Action]\n"
        "- **Then**: [Expected outcome]\n\n"
        "### Subtasks Needed\n\n"
        "| # | Task | Points | Blocking |\n"
        "|---|------|--------|----------|\n"
        "| 1 | [Task] | [pts] | No |\n"
    )


def _body_task(item: dict[str, Any], manifest: dict) -> str:
    title = item.get("title", "")
    desc = item.get("description", "")
    parent = item.get("parent_ref", "")
    priority = item.get("priority", "P1")
    return (
        f"# Task: {title}\n\n"
        f"> **Status**: Backlog | **Priority**: {priority}\n"
        f"> **Parent Story**: {parent}\n"
        "> **Area**: Backend\n"
        "> **Estimate**: TBD\n\n"
        "---\n\n"
        "## Summary\n\n"
        f"{desc or '[What this task implements]'}\n\n"
        "---\n\n"
        "## Context\n\n"
        "- **Parent Story AC**: \"[AC text]\"\n"
        "- **Preceding Task**: None\n"
        "- **Blocking Tasks**: None\n\n"
        "---\n\n"
        f"{_done_when_block()}\n"
        "---\n\n"
        "## Implementation Notes\n\n"
        "### Approach\n\n"
        "[How to implement this]\n"
    )


# ---------------------------------------------------------------------------
# Phase 3 / Task #17: create_all_issues
# ---------------------------------------------------------------------------


def create_all_issues(
    hierarchy: dict[str, Any],
    config: dict[str, Any],
    repo: str,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Create all issues top-down and return a manifest dict.

    Args:
        hierarchy: Output of parse_plan().
        config: Output of preflight().
        repo: GitHub repo in owner/name format.
        output_dir: Directory to write manifest.json (default: CWD).

    Returns:
        manifest: Maps unique key → {number, nodeId, databaseId, level, ...}
    """
    out = output_dir or Path(".")
    manifest: dict[str, Any] = {}
    ordered: list[tuple[str, dict[str, Any] | None]] = []

    if hierarchy.get("scope"):
        ordered.append(("scope", hierarchy["scope"]))
    if hierarchy.get("initiative"):
        ordered.append(("initiative", hierarchy["initiative"]))
    for epic in hierarchy.get("epics", []):
        ordered.append(("epic", epic))
    for story in hierarchy.get("stories", []):
        ordered.append(("story", story))
    for task in hierarchy.get("tasks", []):
        ordered.append(("task", task))

    # Track per-level index for unique keys
    level_counters: dict[str, int] = {}

    for level, item in ordered:
        if item is None:
            continue
        title_prefix = {
            "scope": "Project Scope",
            "initiative": "Initiative",
            "epic": "Epic",
            "story": "Story",
            "task": "Task",
        }[level]
        full_title = f"{title_prefix}: {item['title']}"
        body = generate_body(item, level, manifest)

        url = _create_issue(repo, full_title, body)
        number = int(url.rstrip("/").split("/")[-1])
        ids = _get_issue_ids(repo, number)

        # Use level-index key to avoid title collisions
        level_counters[level] = level_counters.get(level, 0) + 1
        manifest_key = f"{level}-{level_counters[level]}"

        record = {
            "number": number,
            "nodeId": ids["nodeId"],
            "databaseId": ids["databaseId"],
            "level": level,
            "title": item["title"],
            "parent_ref": item.get("parent_ref"),
            "priority": item.get("priority", "P1"),
            "size": item.get("size", "M"),
            "blocking": item.get("blocking", []),
        }
        manifest[manifest_key] = record
        print(f"[created] #{number} {full_title}")
        time.sleep(0.5)

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[OK] manifest → {manifest_path} ({len(manifest)} issues)")
    return manifest


def _create_issue(repo: str, title: str, body: str) -> str:
    """Create a GitHub issue via gh CLI; return the issue URL."""
    fd, tmp = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        result = run_gh(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--body-file",
                tmp,
            ],
        )
        return result.stdout.strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _get_issue_ids(repo: str, number: int) -> dict[str, Any]:
    result = run_gh(
        [
            "gh",
            "api",
            f"/repos/{repo}/issues/{number}",
            "--jq",
            "{nodeId: .node_id, databaseId: .id, number: .number}",
        ],
    )
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_parse(args: argparse.Namespace) -> None:
    hierarchy = parse_plan(args.plan)
    counts = {
        "scope": 1 if hierarchy["scope"] else 0,
        "initiative": 1 if hierarchy["initiative"] else 0,
        "epics": len(hierarchy["epics"]),
        "stories": len(hierarchy["stories"]),
        "tasks": len(hierarchy["tasks"]),
    }
    total = sum(counts.values())
    print(f"Parsed {total} items from {args.plan}:")
    for level, count in counts.items():
        print(f"  {level}: {count}")


def _cmd_preflight(args: argparse.Namespace) -> None:
    out = Path(args.output_dir) if args.output_dir else None
    preflight(args.org, args.repo, args.project, output_dir=out)


def _cmd_create(args: argparse.Namespace) -> None:
    out = Path(args.output_dir) if args.output_dir else None
    config = preflight(args.org, args.repo, args.project, output_dir=out)
    hierarchy = parse_plan(args.plan)
    create_all_issues(hierarchy, config, args.repo, output_dir=out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a markdown plan into GitHub issues."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Parse plan and show summary")
    p_parse.add_argument("--plan", required=True, help="Path to markdown plan file")

    p_pre = sub.add_parser(
        "preflight", help="Validate org Issue Types and project fields"
    )
    p_pre.add_argument("--org", required=True)
    p_pre.add_argument("--repo", required=True)
    p_pre.add_argument("--project", required=True, type=int)
    p_pre.add_argument("--output-dir", default=None, help="Output directory")

    p_create = sub.add_parser("create", help="Run preflight + parse + create issues")
    p_create.add_argument("--plan", required=True)
    p_create.add_argument("--org", required=True)
    p_create.add_argument("--repo", required=True)
    p_create.add_argument("--project", required=True, type=int)
    p_create.add_argument("--output-dir", default=None, help="Output directory")

    args = parser.parse_args()
    dispatch = {
        "parse": _cmd_parse,
        "preflight": _cmd_preflight,
        "create": _cmd_create,
    }
    try:
        dispatch[args.command](args)
    except (AuthError, PreflightError, GitHubAPIError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
