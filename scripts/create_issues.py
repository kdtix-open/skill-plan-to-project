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
import datetime as _dt
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
    (
        "story",
        re.compile(r"^#{3,4}\s+(Story:|User\s+Story:)", re.IGNORECASE),
    ),
    ("task", re.compile(r"^#{4,5}\s+Task:", re.IGNORECASE)),
]

PRIORITY_RE = re.compile(r"^Priority:\s*(P[012])", re.IGNORECASE | re.MULTILINE)
SIZE_RE = re.compile(r"^Size:\s*(XS|S|M|L|XL)\b", re.IGNORECASE | re.MULTILINE)
BLOCKS_RE = re.compile(
    r"^Block(?:s|ing)?:\s*(.+)",
    re.IGNORECASE | re.MULTILINE,
)

# Directory containing asset templates (resolved relative to repo root)
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# ---------------------------------------------------------------------------
# Phase 1 / Task #15: parse_plan
# ---------------------------------------------------------------------------


def parse_plan(filepath: str) -> dict[str, Any]:
    """Parse a KDTIX markdown plan into a hierarchy dict.

    Args:
        filepath: Path to the markdown plan file.

    Returns:
        Dict with keys: scope, initiative, initiatives, epics, stories, tasks.
        ``initiative`` is preserved as a backward-compatible alias to the first
        initiative entry when present. Each item has: title, description,
        priority, size, blocking, and (for initiative/epics/stories/tasks)
        parent_ref.

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
            # Stage 2 (FR #34): extract recognized `#### Section Name`
            # subsections from the item body so the renderer can map them
            # 1:1 to template placeholder groups.  Safe for plans that
            # don't use subsections (returns `{}` + the whole body as
            # `_leading_text`).
            current["subsections"] = _parse_subsections(body_body, current["level"])
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


# ---------------------------------------------------------------------------
# Stage 2 (FR #34): Structured subsection parser.
#
# Recognizes `#### Section Name` headings inside an item's body and maps
# them to canonical keys so the template renderer can substitute them into
# placeholder groups.  Unrecognized headings + free text fall into
# `_leading_text` as a fallback for the item's primary narrative field
# (Vision for scope, Objective for initiative/epic, TL;DR for story,
# Summary for task).
#
# Subsection headings can use any markdown header depth (## through ######)
# so long as the trimmed heading text matches one of the aliases below
# (case-insensitive).  The outer plan parser (`parse_plan`) already filters
# out level-prefix lines (`Project Scope:`, `Initiative:`, etc.) so
# subsection parsing only sees the body portion of each item.
# ---------------------------------------------------------------------------

# Per-level canonical subsection keys and the heading aliases that match
# them.  Aliases are compared case-insensitively + whitespace-normalized.
SUBSECTION_HEADINGS: dict[str, dict[str, list[str]]] = {
    "scope": {
        "vision": ["vision", "project vision"],
        "business_problem": [
            "business problem",
            "business problem & current state",
            "business problem and current state",
            "current state",
        ],
        "success_criteria": ["success criteria"],
        "in_scope_capabilities": [
            "in-scope capabilities",
            "in scope capabilities",
            "in-scope",
            "in scope",
        ],
        "assumptions": ["assumptions"],
        "out_of_scope": ["out of scope", "out-of-scope"],
        "moscow": ["moscow", "moscow classification"],
        "done_when": [
            "i know i am done when",
            "done when",
            "definition of done",
        ],
    },
    "initiative": {
        "objective": ["objective"],
        "release_value": ["release value"],
        "success_criteria": ["success criteria"],
        "feature_scope": ["feature scope"],
        "assumptions": ["assumptions"],
        "dependencies": ["dependencies"],
        "out_of_scope": ["out of scope", "out-of-scope"],
        "artifacts": ["artifacts"],
        "done_when": [
            "i know i am done when",
            "done when",
            "definition of done",
        ],
    },
    "epic": {
        "objective": ["objective"],
        "release_value": ["release value"],
        "success_criteria": ["success criteria"],
        "feature_scope": ["feature scope"],
        "assumptions": ["assumptions"],
        "dependencies": ["dependencies"],
        "done_when": [
            "i know i am done when",
            "done when",
            "definition of done",
        ],
        "code_areas": ["code areas", "code areas to examine"],
        "questions_tech_lead": ["questions for tech lead"],
        "security_compliance": [
            "security/compliance",
            "security",
            "compliance",
        ],
    },
    "story": {
        "user_story": ["user story"],
        "tldr": ["tl;dr", "tldr"],
        "why_this_matters": ["why this matters"],
        "assumptions": ["assumptions"],
        "moscow": ["moscow", "moscow classification"],
        "dependencies": ["dependencies"],
        "done_when": [
            "i know i am done when",
            "done when",
            "definition of done",
        ],
        "acceptance_criteria": ["acceptance criteria"],
        "constraints": ["constraints"],
        "implementation_notes": ["implementation notes"],
        "security_compliance": [
            "security/compliance",
            "security",
            "compliance",
        ],
        "subtasks_needed": ["subtasks needed", "subtasks"],
    },
    "task": {
        "summary": ["summary"],
        "context": ["context"],
        "done_when": [
            "i know i am done when",
            "done when",
            "definition of done",
        ],
        "implementation_notes": ["implementation notes"],
        "security_compliance": [
            "security/compliance",
            "security",
            "compliance",
        ],
    },
}

# Subsections parsed as a flat list of bullets (vs. free-form paragraphs).
_BULLET_SUBSECTIONS = {
    "success_criteria",
    "assumptions",
    "out_of_scope",
    "done_when",
    "context",
    "constraints",
    "artifacts",
    "questions_tech_lead",
}

# Subsections parsed as a dict of nested bullet groups (e.g. MoSCoW's
# Must/Should/Could/Won't Have groups).  Values are sets of group name
# aliases.
_NESTED_BULLET_SUBSECTIONS: dict[str, list[str]] = {
    "moscow": [
        "must have",
        "should have",
        "could have",
        "won't have",
        "wont have",
    ],
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")
_NESTED_GROUP_RE = re.compile(r"^\*\*([^*]+)\*\*\s*:?\s*$")


def _parse_subsections(body: str, level: str) -> dict[str, Any]:
    """Extract recognized subsection content from an item's body.

    Returns a dict keyed by canonical subsection name with shape:
    - str (paragraph) for most keys
    - list[str] (bullet items) for keys in `_BULLET_SUBSECTIONS`
    - dict[str, list[str]] (nested bullets) for keys in
      `_NESTED_BULLET_SUBSECTIONS` (e.g. MoSCoW -> {must_have: [...],
      should_have: [...], ...})

    Additionally, non-subsection text before the first recognized heading
    is stored under `_leading_text` as a fallback for the item's primary
    narrative field (e.g. Vision for scope, Objective for initiative).

    Safe for plans that don't use subsections: returns `{}` (with
    `_leading_text` populated if there's any body text).
    """
    aliases = SUBSECTION_HEADINGS.get(level, {})
    alias_map: dict[str, str] = {}
    for key, names in aliases.items():
        for alias in names:
            alias_map[alias.lower()] = key

    result: dict[str, Any] = {}
    leading: list[str] = []
    current_key: str | None = None
    current_lines: list[str] = []

    def _flush_current() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            result[current_key] = _normalize_subsection(current_key, current_lines)
        current_key = None
        current_lines = []

    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            heading_text = m.group(2).strip()
            heading_key = alias_map.get(heading_text.lower())
            if heading_key is not None:
                _flush_current()
                current_key = heading_key
                continue
            # Unknown heading (e.g. `### Approach` inside Implementation
            # Notes): keep as content rather than starting a new section.
            if current_key is None:
                leading.append(line)
            else:
                current_lines.append(line)
            continue
        if current_key is None:
            leading.append(line)
        else:
            current_lines.append(line)
    _flush_current()

    leading_text = "\n".join(leading).strip()
    if leading_text:
        result["_leading_text"] = leading_text
    return result


def _normalize_subsection(key: str, lines: list[str]) -> Any:
    """Shape subsection content per key's expected type."""
    text = "\n".join(lines).strip()
    if not text:
        return "" if key not in _BULLET_SUBSECTIONS else []

    if key in _NESTED_BULLET_SUBSECTIONS:
        return _parse_nested_bullets(text, _NESTED_BULLET_SUBSECTIONS[key])

    if key in _BULLET_SUBSECTIONS:
        return _parse_bullets(text)

    # Default: paragraph (preserves original formatting + any inline markdown)
    return text


def _parse_bullets(text: str) -> list[str]:
    """Extract a flat list of bullet items from markdown text."""
    bullets: list[str] = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            content = m.group(1).strip()
            if content:
                bullets.append(content)
    if not bullets and text.strip():
        # Paragraph in a bullet-expected section: wrap as single bullet.
        bullets = [text.strip().replace("\n", " ")]
    return bullets


def _parse_nested_bullets(text: str, group_names: list[str]) -> dict[str, list[str]]:
    """Parse MoSCoW-style nested bullets with `**Group**:` sub-headers."""
    result: dict[str, list[str]] = {}
    current_group: str | None = None
    current_bullets: list[str] = []

    def _group_key(name: str) -> str:
        return name.lower().replace("won't", "wont").replace(" ", "_")

    group_keys = {g: _group_key(g) for g in group_names}

    for line in text.splitlines():
        stripped = line.strip()
        m = _NESTED_GROUP_RE.match(stripped)
        if m:
            heading_lower = m.group(1).strip().lower()
            if heading_lower in group_names:
                if current_group is not None:
                    result[current_group] = current_bullets
                current_group = group_keys[heading_lower]
                current_bullets = []
                continue
        bullet_m = _BULLET_RE.match(line)
        if bullet_m and current_group is not None:
            content = bullet_m.group(1).strip()
            if content:
                current_bullets.append(content)
    if current_group is not None:
        result[current_group] = current_bullets
    return result


def _build_hierarchy(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Assign parent_refs and split items into hierarchy buckets."""
    scope = next((i for i in items if i["level"] == "scope"), None)
    initiatives = [i for i in items if i["level"] == "initiative"]
    initiative = initiatives[0] if initiatives else None
    epics = [i for i in items if i["level"] == "epic"]
    stories = [i for i in items if i["level"] == "story"]
    tasks = [i for i in items if i["level"] == "task"]

    # Assign parent refs: each item gets the title of the most recently seen
    # item one level above it in the flat list.
    last: dict[str, str | None] = {
        "scope": scope["title"] if scope else None,
        "initiative": None,
        "epic": None,
        "story": None,
    }

    for item in items:
        level = item["level"]
        if level == "initiative":
            item["parent_ref"] = last["scope"]
            last["initiative"] = item["title"]
        elif level == "epic":
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
        "initiatives": initiatives,
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

    # Stage 2 (FR #34): ensure structured subsections are parsed before
    # rendering.  `parse_plan` populates this on every item, but callers
    # that build their own item dict (e.g. tests, ad hoc scripts) can
    # skip it — re-parse here as a safety net.
    if "subsections" not in item:
        item["subsections"] = _parse_subsections(item.get("description", ""), level)

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
    """Render an asset template by substituting metadata + subsections.

    Two-phase render:
     1. Flat metadata replacements (title, priority, size, parent ref).
     2. Per-level subsection fillers that map plan `#### Section` content
        into the template's placeholder groups (Stage 2 of FR #34).

    Plans without structured subsections retain the previous behavior:
    the raw description is used as the primary narrative field (Vision
    for scope, Objective for initiative/epic, TL;DR for story, Summary
    for task) and other placeholders remain as template text — the
    Stage 1 P0-4 scanner catches any leaked placeholders before ship.
    """
    title = item.get("title", "")
    desc = item.get("description", "")
    priority = item.get("priority", "P1")
    size = item.get("size", "M")
    parent = item.get("parent_ref", "")
    subs: dict[str, Any] = item.get("subsections") or {}

    # ----- phase 1: flat metadata replacements -----
    # The primary narrative field falls back to the leading text from
    # subsection parsing (content above the first `#### Section`), then
    # to the whole description for plans that don't use subsections.
    leading = subs.get("_leading_text", "") or desc
    vision_text = (
        subs.get("vision") or leading if level == "scope" else "[Vision statement]"
    )
    obj_text = (
        subs.get("objective") or leading
        if level in ("initiative", "epic")
        else "[Objective]"
    )
    summary_text = (
        subs.get("tldr") or leading if level == "story" else "[1-sentence summary]"
    )
    impl_text = (
        subs.get("summary") or leading
        if level == "task"
        else "[What this task implements]"
    )

    replacements = {
        "[CODE] [TITLE]": title,
        "[TITLE]": title,
        "[CODE]": "",
        "[STATUS]": "Backlog",
        "[PRIORITY]": priority,
        "[P0/P1/P2]": priority,
        "[P0/P1/P2] — [LABEL]": priority,
        "[SIZE]": size,
        "[TIMEFRAME]": "TBD",
        "[OWNER]": "TBD",
        (
            "[VISION — 1-2 sentences on the end state and the value delivered]"
        ): vision_text,
        (
            "[Why this initiative exists and what problem it solves — 2-4 sentences]"
        ): obj_text,
        "[Why this epic exists — 2-4 sentences]": obj_text,
        "[1-sentence summary of what this story delivers]": summary_text,
        "[1-3 sentences describing exactly what this task implements]": impl_text,
        "[VERSION]": "TBD",
        "[POINTS]": "TBD",
        "[HOURS]": "TBD",
        "[POINTS] pts": "TBD",
        "[HOURS] hrs": "TBD",
        "#[N] [EPIC TITLE]": parent or "TBD",
        "#[N] [STORY TITLE]": parent or "TBD",
        "[Backend/Frontend/Infrastructure/QA]": "Backend",
        # Stage 2: placeholders in child-linkage table rows that no
        # downstream code currently expands.  Substitute neutral text so
        # the P0-4 scanner doesn't flag these cosmetic sample rows.
        "[DATE]": _dt.date.today().isoformat(),
        "[DESCRIPTION]": "_(child linkage populated after creation)_",
    }

    rendered = template_text
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    # ----- phase 2: per-level subsection fillers (Stage 2) -----
    section_fillers: dict[str, Any] = {
        "scope": _fill_scope_subsections,
        "initiative": _fill_initiative_subsections,
        "epic": _fill_epic_subsections,
        "story": _fill_story_subsections,
        "task": _fill_task_subsections,
    }
    filler = section_fillers.get(level)
    if filler is not None:
        rendered = filler(rendered, subs)

    # Inject parent reference for levels that have one
    if parent and level in ("epic", "story", "task"):
        if "> **Parent" not in rendered:
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


# ---------------------------------------------------------------------------
# Stage 2 (FR #34): Per-level subsection fillers.
#
# Each filler function receives the template text after phase-1 metadata
# substitution + the item's structured subsection dict.  It replaces each
# placeholder BLOCK (not just the first keyword) with the subsection
# content when available, or leaves the placeholder intact when the plan
# didn't provide that subsection (Stage 1 scanner catches leaks; Stage 4
# will replace remaining placeholders with a TBD marker).
# ---------------------------------------------------------------------------


def _bullet_lines(items: list[str], checkbox: bool = False) -> str:
    """Format a bullet list as markdown. `checkbox=True` prepends `- [ ]`."""
    prefix = "- [ ] " if checkbox else "- "
    return "\n".join(f"{prefix}{i}" for i in items)


def _replace_block(rendered: str, old_block: str, new_block: str) -> str:
    """Replace a verbatim block; no-op when the block isn't present."""
    if old_block in rendered:
        return rendered.replace(old_block, new_block, 1)
    return rendered


def _moscow_table_rows(moscow: dict[str, list[str]]) -> str:
    """Render MoSCoW groups as table rows.  Empty groups keep placeholder."""
    rows: list[str] = []
    group_order = [
        ("must_have", "Must Have"),
        ("should_have", "Should Have"),
        ("could_have", "Could Have"),
        ("wont_have", "Won't Have"),
    ]
    for key, label in group_order:
        items = moscow.get(key) or []
        if not items:
            rows.append(f"| {label} | [ITEM] |")
        else:
            for item in items:
                rows.append(f"| {label} | {item} |")
    return "\n".join(rows)


def _fill_scope_subsections(rendered: str, subs: dict[str, Any]) -> str:
    # Business Problem
    bp = subs.get("business_problem")
    if isinstance(bp, str) and bp.strip():
        rendered = rendered.replace(
            "[Describe the problem being solved and why the current "
            "approach is insufficient]",
            bp,
        )

    # Success Criteria
    crit = subs.get("success_criteria") or []
    if crit:
        rendered = _replace_block(
            rendered,
            "- [ ] [CRITERION 1]\n- [ ] [CRITERION 2]",
            _bullet_lines(crit, checkbox=True),
        )

    # In-Scope Capabilities
    in_scope = subs.get("in_scope_capabilities")
    if in_scope:
        in_scope_text = (
            in_scope if isinstance(in_scope, str) else _bullet_lines(in_scope)
        )
        rendered = rendered.replace(
            "[List of features or capabilities included, with references "
            "to Initiatives/Epics]",
            in_scope_text,
        )

    # Assumptions
    assumptions = subs.get("assumptions") or []
    if assumptions:
        rendered = _replace_block(
            rendered,
            "- [ASSUMPTION 1]\n- [ASSUMPTION 2]",
            _bullet_lines(assumptions),
        )

    # Out of Scope
    oos = subs.get("out_of_scope") or []
    if oos:
        rendered = _replace_block(
            rendered,
            "- [ITEM 1]\n- [ITEM 2]",
            _bullet_lines(oos),
        )

    # MoSCoW
    moscow = subs.get("moscow") or {}
    if isinstance(moscow, dict) and moscow:
        old_rows = (
            "| Must Have | [ITEM] |\n"
            "| Should Have | [ITEM] |\n"
            "| Could Have | [ITEM] |\n"
            "| Won't Have | [ITEM] |"
        )
        rendered = _replace_block(rendered, old_rows, _moscow_table_rows(moscow))

    # Done When (project-specific criterion)
    done = subs.get("done_when") or []
    if done:
        rendered = _replace_block(
            rendered,
            "- [ ] [PROJECT-SPECIFIC CRITERION]",
            _bullet_lines(done, checkbox=True),
        )

    return rendered


def _fill_initiative_subsections(rendered: str, subs: dict[str, Any]) -> str:
    # Release Value
    rv = subs.get("release_value")
    if isinstance(rv, str) and rv.strip():
        rendered = rendered.replace(
            "[What becomes possible after this initiative ships — 1-2 sentences]",
            rv,
        )

    # Success Criteria
    crit = subs.get("success_criteria") or []
    if crit:
        rendered = _replace_block(
            rendered,
            "- [ ] [CRITERION 1]\n- [ ] [CRITERION 2]",
            _bullet_lines(crit, checkbox=True),
        )

    # Feature Scope (raw paragraph or bullets → single-column rows)
    fs = subs.get("feature_scope")
    if fs:
        fs_block = fs if isinstance(fs, str) else _bullet_lines(fs)
        rendered = _replace_block(
            rendered,
            "| 1 | [FEATURE] | [INCLUDES] | [ENABLES] |",
            fs_block,
        )

    # Assumptions
    assumptions = subs.get("assumptions") or []
    if assumptions:
        rendered = _replace_block(
            rendered,
            "- [ASSUMPTION 1]\n- [ASSUMPTION 2]",
            _bullet_lines(assumptions),
        )

    # Dependencies
    deps = subs.get("dependencies")
    if deps:
        deps_block = deps if isinstance(deps, str) else _bullet_lines(deps)
        rendered = _replace_block(
            rendered,
            "| [DEPENDENCY] | [TYPE] | [OWNER] | [STATUS] |",
            deps_block,
        )

    # Out of Scope
    oos = subs.get("out_of_scope") or []
    if oos:
        rendered = _replace_block(
            rendered,
            "- [ITEM]",
            _bullet_lines(oos),
        )

    # Artifacts
    art = subs.get("artifacts") or []
    if art:
        rendered = _replace_block(
            rendered,
            "- [ ] [ARTIFACT]",
            _bullet_lines(art, checkbox=True),
        )

    # Done When
    done = subs.get("done_when") or []
    if done:
        rendered = _replace_block(
            rendered,
            "- [ ] [PROJECT-SPECIFIC CRITERION]",
            _bullet_lines(done, checkbox=True),
        )

    return rendered


def _fill_epic_subsections(rendered: str, subs: dict[str, Any]) -> str:
    # Release Value
    rv = subs.get("release_value")
    if isinstance(rv, str) and rv.strip():
        rendered = rendered.replace(
            "[What becomes possible after this epic ships — 1-2 sentences]",
            rv,
        )

    # Success Criteria
    crit = subs.get("success_criteria") or []
    if crit:
        rendered = _replace_block(
            rendered,
            "- [ ] [CRITERION 1]\n- [ ] [CRITERION 2]",
            _bullet_lines(crit, checkbox=True),
        )

    # Feature Scope
    fs = subs.get("feature_scope")
    if fs:
        fs_block = fs if isinstance(fs, str) else _bullet_lines(fs)
        rendered = _replace_block(
            rendered,
            "| 1 | [FEATURE] | [INCLUDES] | [ENABLES] |",
            fs_block,
        )

    # Assumptions
    assumptions = subs.get("assumptions") or []
    if assumptions:
        rendered = _replace_block(
            rendered,
            "- [ASSUMPTION 1]",
            _bullet_lines(assumptions),
        )

    # Dependencies
    deps = subs.get("dependencies")
    if deps:
        deps_block = deps if isinstance(deps, str) else _bullet_lines(deps)
        rendered = _replace_block(
            rendered,
            "| [DEPENDENCY] | [TYPE] | [OWNER] | [STATUS] |",
            deps_block,
        )

    # Done When
    done = subs.get("done_when") or []
    if done:
        rendered = _replace_block(
            rendered,
            "- [ ] [PROJECT-SPECIFIC CRITERION]",
            _bullet_lines(done, checkbox=True),
        )

    # Code Areas
    ca = subs.get("code_areas")
    if ca:
        ca_block = ca if isinstance(ca, str) else _bullet_lines(ca)
        rendered = _replace_block(
            rendered,
            "| [TYPE] | [OBJECT] | [LOCATION] | [NOTES] |",
            ca_block,
        )

    # Questions for Tech Lead
    qs = subs.get("questions_tech_lead") or []
    if qs:
        rendered = _replace_block(rendered, "- [QUESTION]", _bullet_lines(qs))

    # Security/Compliance
    sc = subs.get("security_compliance")
    if isinstance(sc, str) and sc.strip():
        rendered = rendered.replace(
            "[Required if this epic involves create/update/delete/"
            "resolve/write operations]",
            sc,
        )

    return rendered


def _fill_story_subsections(rendered: str, subs: dict[str, Any]) -> str:
    # User Story block
    us = subs.get("user_story")
    if isinstance(us, str) and us.strip():
        old = "As a [ROLE],\nI want [WHAT],\nSo that [OUTCOME]."
        rendered = _replace_block(rendered, old, us.strip())

    # Why This Matters
    wtm = subs.get("why_this_matters")
    if isinstance(wtm, str) and wtm.strip():
        rendered = rendered.replace(
            "[Why this story is needed and what breaks without it — 2-3 sentences]",
            wtm,
        )

    # Assumptions (story has a special 3-line format)
    assumptions = subs.get("assumptions") or []
    if assumptions:
        old_block = (
            "- **Roles**: [WHO uses the output of this story]\n"
            "- **Starting point**: [Preconditions that must be true]\n"
            "- **Preconditions**: [What must exist before this story starts]"
        )
        rendered = _replace_block(rendered, old_block, _bullet_lines(assumptions))

    # MoSCoW
    moscow = subs.get("moscow") or {}
    if isinstance(moscow, dict) and moscow:
        old_rows = (
            "| Must Have | [ITEM] |\n"
            "| Should Have | [ITEM] |\n"
            "| Could Have | [ITEM] |\n"
            "| Won't Have | [ITEM] |"
        )
        rendered = _replace_block(rendered, old_rows, _moscow_table_rows(moscow))

    # Dependencies
    deps = subs.get("dependencies")
    if deps:
        deps_block = deps if isinstance(deps, str) else _bullet_lines(deps)
        rendered = _replace_block(
            rendered,
            "| #[N] | [DESCRIPTION] | [STATUS] |",
            deps_block,
        )

    # Done When
    done = subs.get("done_when") or []
    if done:
        rendered = _replace_block(
            rendered,
            "- [ ] [ACCEPTANCE CRITERION 1]\n- [ ] [ACCEPTANCE CRITERION 2]",
            _bullet_lines(done, checkbox=True),
        )

    # Acceptance Criteria (scenarios)
    ac = subs.get("acceptance_criteria")
    if isinstance(ac, str) and ac.strip():
        old_block = (
            "**Scenario 1**: [SCENARIO NAME]\n"
            "- **Given**: [PRECONDITION]\n"
            "- **When**: [ACTION]\n"
            "- **Then**: [EXPECTED OUTCOME]"
        )
        rendered = _replace_block(rendered, old_block, ac.strip())

    # Constraints
    constraints = subs.get("constraints") or []
    if constraints:
        rendered = _replace_block(
            rendered,
            "- [CONSTRAINT]",
            _bullet_lines(constraints),
        )

    # Implementation Notes
    impl = subs.get("implementation_notes")
    if isinstance(impl, str) and impl.strip():
        rendered = rendered.replace(
            "[Technical approach, key considerations]",
            impl,
        )

    # Security/Compliance
    sc = subs.get("security_compliance")
    if isinstance(sc, str) and sc.strip():
        rendered = rendered.replace(
            "[Required if story involves create/update/delete/"
            "resolve/write operations]",
            sc,
        )

    # Subtasks Needed
    st = subs.get("subtasks_needed")
    if st:
        st_block = st if isinstance(st, str) else _bullet_lines(st)
        rendered = _replace_block(
            rendered,
            "| 1 | [TASK] | [PTS] | [YES/NO] |",
            st_block,
        )

    return rendered


def _fill_task_subsections(rendered: str, subs: dict[str, Any]) -> str:
    # Context block
    ctx = subs.get("context") or []
    if ctx:
        old_block = (
            '- **Parent Story AC**: "[The acceptance criterion this '
            'task satisfies]"\n'
            "- **Preceding Task**: [#N task that must complete first, or None]\n"
            "- **Blocking Tasks**: [#N tasks blocked by this one]"
        )
        rendered = _replace_block(rendered, old_block, _bullet_lines(ctx))

    # Done When (technical criteria)
    done = subs.get("done_when") or []
    if done:
        rendered = _replace_block(
            rendered,
            "- [ ] [TECHNICAL CRITERION 1]\n- [ ] [TECHNICAL CRITERION 2]",
            _bullet_lines(done, checkbox=True),
        )

    # Implementation Notes
    impl = subs.get("implementation_notes")
    if isinstance(impl, str) and impl.strip():
        rendered = rendered.replace(
            "[How to implement this — pseudocode or concrete steps]",
            impl,
        )

    # Security/Compliance
    sc = subs.get("security_compliance")
    if isinstance(sc, str) and sc.strip():
        rendered = rendered.replace(
            "[Required if task involves create/update/delete/"
            "resolve/write operations]",
            sc,
        )

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
    initiatives = hierarchy.get("initiatives") or []
    if not initiatives and hierarchy.get("initiative"):
        initiatives = [hierarchy["initiative"]]
    for initiative in initiatives:
        ordered.append(("initiative", initiative))
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


_GET_ISSUE_IDS_MAX_WAIT = 30.0  # seconds — total ceiling for 404 retries
_GET_ISSUE_IDS_INITIAL_DELAY = 2.0  # seconds — first retry delay


def _get_issue_ids(repo: str, number: int) -> dict[str, Any]:
    """Fetch nodeId, databaseId, and number for a newly created issue.

    GitHub can return a transient 404 immediately after issue creation due to
    read-after-write replication lag.  This function retries with exponential
    backoff (capped at 10 s) for up to _GET_ISSUE_IDS_MAX_WAIT seconds before
    re-raising.
    """
    delay = _GET_ISSUE_IDS_INITIAL_DELAY
    elapsed = 0.0
    while True:
        try:
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
        except GitHubAPIError as exc:
            if "404" in str(exc) and elapsed < _GET_ISSUE_IDS_MAX_WAIT:
                print(
                    f"[RETRY] Issue #{number} returned 404 — waiting {delay:.1f}s "
                    f"(elapsed {elapsed:.1f}s/{_GET_ISSUE_IDS_MAX_WAIT}s)",
                    file=sys.stderr,
                )
                time.sleep(delay)
                elapsed += delay
                delay = min(delay * 2, 10.0)
            else:
                raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_parse(args: argparse.Namespace) -> None:
    hierarchy = parse_plan(args.plan)
    counts = {
        "scope": 1 if hierarchy["scope"] else 0,
        "initiatives": len(hierarchy.get("initiatives", [])),
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


# ---------------------------------------------------------------------------
# Refresh mode (FR #34 Stage 5) — in-place update existing backlog without
# creating duplicates.  Intent: patch/upgrade existing issues that were
# created with an older version of the skill + now need the fixes from
# Stages 1-4 applied retroactively.
# ---------------------------------------------------------------------------


def _walk_existing_hierarchy(
    repo: str, scope_issue_number: int
) -> list[dict[str, Any]]:
    """Walk sub-issue tree rooted at scope_issue_number using GH sub-issues REST.

    Returns a flat list of {number, title, level, parent_number} entries for
    every descendant (+ the root scope itself).  Level is inferred from Issue
    Type when available, else defaults to a best-guess by depth from root.

    Fail-soft on API errors: a child whose sub-issues fetch fails is reported
    but traversal continues with siblings.
    """
    from scripts.gh_helpers import run_gh

    results: list[dict[str, Any]] = []

    def _infer_level_by_depth(depth: int) -> str:
        # scope=0, initiative=1, epic=2, story=3, task=4
        return ["scope", "initiative", "epic", "story", "task"][min(depth, 4)]

    def _fetch_issue(number: int) -> dict[str, Any] | None:
        # Fetch title + issue-type via GraphQL.
        #
        # We used to call `gh issue view N --json number,title,issueType` but
        # the `issueType` field is not exposed by every installed gh CLI
        # version (observed broken on gh 2.90.0 2026-04-16: "Unknown JSON
        # field: issueType").  GraphQL via `gh api graphql` is stable across
        # gh versions because the `issueType { name }` subfield is part of
        # the public GitHub API surface, independent of whatever the gh CLI
        # decides to whitelist for `issue view --json`.
        if "/" not in repo:
            return None
        owner, name = repo.split("/", 1)
        query = (
            "query($owner: String!, $name: String!, $number: Int!) { "
            "repository(owner: $owner, name: $name) { "
            "issue(number: $number) { "
            "number title issueType { name } "
            "} } }"
        )
        r = run_gh(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ],
            check=False,
        )
        if r.returncode != 0:
            return None
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
        issue = (payload or {}).get("data", {}).get("repository", {}).get("issue")
        # Shape the result so callers see the same dict shape they got from
        # the old `gh issue view --json` path: {number, title, issueType}.
        if not issue:
            return None
        return issue

    def _fetch_sub_issues(number: int) -> list[int]:
        r = run_gh(
            ["gh", "api", f"/repos/{repo}/issues/{number}/sub_issues"],
            check=False,
        )
        if r.returncode != 0:
            return []
        try:
            data = json.loads(r.stdout)
            return [item["number"] for item in data if "number" in item]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _recurse(number: int, depth: int, parent_number: int | None) -> None:
        issue = _fetch_issue(number)
        if not issue:
            return
        level_by_type = {
            "Project Scope": "scope",
            "Initiative": "initiative",
            "Epic": "epic",
            "User Story": "story",
            "Task": "task",
        }
        issue_type_name = (issue.get("issueType") or {}).get("name", "")
        level = level_by_type.get(issue_type_name) or _infer_level_by_depth(depth)
        results.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "level": level,
                "parent_number": parent_number,
            }
        )
        for child_num in _fetch_sub_issues(number):
            _recurse(child_num, depth + 1, number)

    _recurse(scope_issue_number, 0, None)
    return results


def _flatten_parsed_hierarchy(hierarchy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten parse_plan() output into a {normalized_title: item} map.

    Normalized title is lowercased + stripped of leading level-prefix + meta
    suffixes so matching against existing issue titles is robust.
    """
    items_by_title: dict[str, dict[str, Any]] = {}

    def _normalize(title: str) -> str:
        # Strip common prefixes that may or may not be present in GH titles:
        # "Project Scope: ", "Initiative: ", "Epic: ", "Story: ", "Task: "
        t = title.strip()
        for prefix in [
            "Project Scope:",
            "Scope:",
            "Initiative:",
            "Epic:",
            "Story:",
            "User Story:",
            "Task:",
        ]:
            if t.lower().startswith(prefix.lower()):
                t = t[len(prefix) :].strip()
                break
        return t.lower().strip()

    def _register(item: dict[str, Any], level: str) -> None:
        norm = _normalize(item.get("title", ""))
        if norm:
            items_by_title[norm] = {**item, "level": level}

    if hierarchy.get("scope"):
        _register(hierarchy["scope"], "scope")
    for init in hierarchy.get("initiatives", []) or []:
        _register(init, "initiative")
    for epic in hierarchy.get("epics", []) or []:
        _register(epic, "epic")
    for story in hierarchy.get("stories", []) or []:
        _register(story, "story")
    for task in hierarchy.get("tasks", []) or []:
        _register(task, "task")

    return items_by_title


def _preserve_outside_template_zone(
    existing_body: str, new_body: str
) -> tuple[str, dict[str, str]]:
    """Merge operator-authored prefix/suffix from `existing_body` into `new_body`.

    The skill's templates define a canonical rendered body that starts with
    `# <Level>: <Title>` and (for scope) ends with a `_Created: ... | Owner: ..._`
    footer.  Anything OUTSIDE that zone in the existing body — HTML comments,
    sequence-order blockquotes, trailing signatures, custom tooling markers —
    is operator-authored content the skill does not own.

    This function:
     1. Extracts the prefix (lines before the first `# ` heading) from
        `existing_body`.
     2. Extracts the suffix (lines after the last italic `_Created: ..._`
        footer, if any) from `existing_body`.
     3. Prepends the prefix + appends the suffix to `new_body`, BUT only
        when `new_body` does not already contain that exact content
        (idempotent: re-runs don't duplicate the prefix).

    Returns `(merged_body, {"prefix": <extracted>, "suffix": <extracted>})`.
    The returned dict lets callers log/report what was preserved.
    """
    prefix = ""
    suffix = ""

    # --- prefix: content before the first top-level heading -----------------
    # Using re.MULTILINE so `^# ` matches at the start of any line.
    existing_heading_match = re.search(r"^# [^\n]+$", existing_body, re.MULTILINE)
    if existing_heading_match:
        idx = existing_heading_match.start()
        candidate_prefix = existing_body[:idx].rstrip("\n")
        if candidate_prefix.strip():
            prefix = candidate_prefix

    # --- suffix: content after the last `_Created: ... | Owner: ..._` footer
    # Using a forgiving pattern: italic span that starts with `_Created:` and
    # ends with `_`.  Only matches when present; scope templates have it,
    # other levels typically don't.
    created_footer = re.compile(r"^_Created:[^\n]*_$", re.MULTILINE)
    existing_footers = list(created_footer.finditer(existing_body))
    if existing_footers:
        last = existing_footers[-1]
        candidate_suffix = existing_body[last.end() :].lstrip("\n")
        if candidate_suffix.strip():
            suffix = candidate_suffix

    # --- merge (idempotent) --------------------------------------------------
    merged = new_body
    if prefix and prefix not in merged:
        merged = f"{prefix}\n\n{merged}"
    if suffix and suffix not in merged:
        # Append after the footer of the NEW body if it has one; else just
        # at the end.
        new_footer_match = list(created_footer.finditer(merged))
        if new_footer_match:
            insert_at = new_footer_match[-1].end()
            merged = merged[:insert_at].rstrip() + "\n\n" + suffix.strip() + "\n"
        else:
            merged = merged.rstrip() + "\n\n" + suffix.strip() + "\n"

    return merged, {"prefix": prefix, "suffix": suffix}


def _unified_diff_snippet(
    before: str, after: str, issue_number: int, max_lines: int = 200
) -> str:
    """Return a unified diff between before/after bodies, capped at `max_lines`.

    Keeps the diff short enough to be readable in a report without
    ballooning the JSON.  When truncated, appends a `... [truncated]`
    marker so the operator knows there's more.
    """
    import difflib

    diff = difflib.unified_diff(
        before.splitlines(keepends=False),
        after.splitlines(keepends=False),
        fromfile=f"issue-{issue_number}-before",
        tofile=f"issue-{issue_number}-after",
        lineterm="",
        n=3,
    )
    lines = list(diff)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(f"... [truncated at {max_lines} lines]")
    return "\n".join(lines)


def refresh_backlog(
    plan_path: str,
    repo: str,
    scope_issue_number: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Patch/upgrade existing backlog issues using the current skill's
    template + parser.  Does NOT create or remove any issues.

    FR #34 Stage 5.  Primary operator use case (2026-04-21 direction):
    "correct existing issues created with the older /plan-to-project skill,
    using the corrected /plan-to-project without creating new or duplicated
    issues."

    Algorithm:
      1. Parse the source plan (same parse_plan() used at create time).
      2. Walk the existing GH sub-issue hierarchy rooted at scope_issue_number.
      3. Match each existing issue to a parsed-plan item by normalized title.
      4. Re-render the body via generate_body() using the parsed item.
      5. Compare to existing body; if different, emit (dry_run) or apply
         (via gh issue edit) the update.
      6. Report a summary: matched / unmatched / updated / unchanged.
    """
    from scripts.gh_helpers import get_issue_body, update_issue_body

    report: dict[str, Any] = {
        "scope_issue_number": scope_issue_number,
        "plan_path": plan_path,
        "dry_run": dry_run,
        "summary": {
            "existing_issues": 0,
            "matched": 0,
            "unmatched": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
        },
        "per_issue": [],
    }

    print(
        f"[refresh] walking existing hierarchy rooted at "
        f"#{scope_issue_number} in {repo}"
    )
    existing = _walk_existing_hierarchy(repo, scope_issue_number)
    report["summary"]["existing_issues"] = len(existing)
    print(f"[refresh] found {len(existing)} existing issues")

    hierarchy = parse_plan(plan_path)
    items_by_title = _flatten_parsed_hierarchy(hierarchy)
    print(f"[refresh] parsed {len(items_by_title)} items from {plan_path}")

    for entry in existing:
        number = entry["number"]
        title = entry["title"]
        level = entry["level"]
        # Normalize existing title same way
        norm = title.strip()
        for prefix in [
            "Project Scope:",
            "Scope:",
            "Initiative:",
            "Epic:",
            "Story:",
            "User Story:",
            "Task:",
        ]:
            if norm.lower().startswith(prefix.lower()):
                norm = norm[len(prefix) :].strip()
                break
        norm = norm.lower().strip()

        item = items_by_title.get(norm)
        per_issue_record: dict[str, Any] = {
            "number": number,
            "title": title,
            "level": level,
        }

        if not item:
            per_issue_record["status"] = "unmatched"
            report["summary"]["unmatched"] += 1
            print(
                f"[refresh] #{number} UNMATCHED: '{title}' (no parsed-plan counterpart)"
            )
            report["per_issue"].append(per_issue_record)
            continue

        report["summary"]["matched"] += 1
        per_issue_record["matched_plan_title"] = item.get("title", "")

        try:
            current_body = get_issue_body(repo, number)
        except Exception as exc:  # noqa: BLE001 — we deliberately fail-soft per-issue
            per_issue_record["status"] = "failed"
            per_issue_record["error"] = f"get_issue_body: {exc}"
            report["summary"]["failed"] += 1
            print(f"[refresh] #{number} FAILED to fetch body: {exc}")
            report["per_issue"].append(per_issue_record)
            continue

        new_body = generate_body(item, level)

        # Stage 2.5 (FR #34): preserve operator-authored content that lives
        # OUTSIDE the template zone — anything before the first `# Heading`
        # and anything after the closing `_Created: ..._` footer.  This
        # protects tooling markers (HTML comments), sequence-order
        # blockquotes, signatures, and other annotations that the skill's
        # templates don't own + shouldn't clobber on refresh.
        new_body, preserved = _preserve_outside_template_zone(current_body, new_body)
        if preserved["prefix"] or preserved["suffix"]:
            per_issue_record["preserved"] = {
                "prefix_lines": preserved["prefix"].count("\n")
                + (1 if preserved["prefix"] else 0),
                "suffix_lines": preserved["suffix"].count("\n")
                + (1 if preserved["suffix"] else 0),
            }

        if current_body.strip() == new_body.strip():
            per_issue_record["status"] = "unchanged"
            report["summary"]["unchanged"] += 1
            print(f"[refresh] #{number} unchanged")
            report["per_issue"].append(per_issue_record)
            continue

        per_issue_record["status"] = "updated" if not dry_run else "would-update"
        per_issue_record["diff_chars_before"] = len(current_body)
        per_issue_record["diff_chars_after"] = len(new_body)

        # Stage 2 (FR #34): include a real unified diff in the report so
        # operators can review exactly what would change before running
        # `--apply` instead of trusting a single char-count delta.
        per_issue_record["diff"] = _unified_diff_snippet(current_body, new_body, number)

        if dry_run:
            report["summary"]["updated"] += 1
            print(
                f"[refresh] #{number} WOULD UPDATE "
                f"({len(current_body)}→{len(new_body)} chars) — dry-run"
            )
        else:
            try:
                update_issue_body(repo, number, new_body)
                report["summary"]["updated"] += 1
                print(
                    f"[refresh] #{number} UPDATED "
                    f"({len(current_body)}→{len(new_body)} chars)"
                )
            except Exception as exc:  # noqa: BLE001
                per_issue_record["status"] = "failed"
                per_issue_record["error"] = f"update_issue_body: {exc}"
                report["summary"]["failed"] += 1
                print(f"[refresh] #{number} FAILED to update: {exc}")

        report["per_issue"].append(per_issue_record)

    s = report["summary"]
    print(
        f"[refresh] DONE — {s['existing_issues']} issues | "
        f"{s['matched']} matched / {s['unmatched']} unmatched | "
        f"{s['updated']} {'would-update' if dry_run else 'updated'} / "
        f"{s['unchanged']} unchanged / {s['failed']} failed"
    )
    return report


def _cmd_refresh(args: argparse.Namespace) -> None:
    out = Path(args.output_dir) if args.output_dir else None
    report = refresh_backlog(
        plan_path=args.plan,
        repo=args.repo,
        scope_issue_number=args.scope_issue,
        dry_run=args.dry_run,
    )
    if out:
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / "refresh-report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[refresh] report written to {report_path}")


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

    p_refresh = sub.add_parser(
        "refresh",
        help=(
            "Patch/upgrade an existing backlog in-place using the current "
            "skill's template + parser (FR #34 Stage 5).  Does NOT create "
            "or remove any issues.  Walks the sub-issue tree rooted at "
            "--scope-issue; for each existing issue found, re-renders its "
            "body from the parsed plan + applies via `gh issue edit`."
        ),
    )
    p_refresh.add_argument("--plan", required=True, help="Source plan .md file")
    p_refresh.add_argument("--repo", required=True, help="owner/name")
    p_refresh.add_argument(
        "--scope-issue",
        required=True,
        type=int,
        dest="scope_issue",
        help="GitHub issue number of the Project Scope to refresh (walks sub-issues)",
    )
    p_refresh.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview changes without applying (DEFAULT: on). Pass --apply to disable.",
    )
    p_refresh.add_argument(
        "--apply",
        action="store_false",
        dest="dry_run",
        help="Apply updates via `gh issue edit` (overrides --dry-run default).",
    )
    p_refresh.add_argument(
        "--output-dir", default=None, help="Output directory for refresh-report.json"
    )

    args = parser.parse_args()
    dispatch = {
        "parse": _cmd_parse,
        "preflight": _cmd_preflight,
        "create": _cmd_create,
        "refresh": _cmd_refresh,
    }
    try:
        dispatch[args.command](args)
    except (AuthError, PreflightError, GitHubAPIError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
