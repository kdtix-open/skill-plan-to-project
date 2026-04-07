"""Shared test fixtures for plan-to-project test suite."""

from __future__ import annotations

import json
import textwrap
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_ok(stdout: str = "") -> MagicMock:
    """Create a mock subprocess result with returncode=0."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def issue_url(number: int, repo: str = "kdtix-open/skill-plan-to-project") -> str:
    """Return a mock GitHub issue URL."""
    return f"https://github.com/{repo}/issues/{number}"


def issue_ids_json(number: int) -> str:
    """Return JSON string for mock issue IDs."""
    return json.dumps(
        {
            "nodeId": f"I_node_{number}",
            "databaseId": number * 100,
            "number": number,
        }
    )


# ---------------------------------------------------------------------------
# Mock API responses
# ---------------------------------------------------------------------------

MOCK_ISSUE_TYPES_NODES = [
    {"id": "IT_scope_id", "name": "Project Scope"},
    {"id": "IT_init_id", "name": "Initiative"},
    {"id": "IT_epic_id", "name": "Epic"},
    {"id": "IT_story_id", "name": "User Story"},
    {"id": "IT_task_id", "name": "Task"},
]

MOCK_ISSUE_TYPES_RESPONSE = json.dumps(
    {
        "data": {
            "organization": {
                "issueTypes": {
                    "nodes": MOCK_ISSUE_TYPES_NODES,
                }
            }
        }
    }
)

MOCK_PROJECT_FIELDS_RESPONSE = json.dumps(
    {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_project_id",
                    "fields": {
                        "nodes": [
                            {
                                "id": "field_priority_id",
                                "name": "Priority",
                                "options": [
                                    {"id": "opt_p0", "name": "P0"},
                                    {"id": "opt_p1", "name": "P1"},
                                    {"id": "opt_p2", "name": "P2"},
                                ],
                            },
                            {
                                "id": "field_size_id",
                                "name": "Size",
                                "options": [
                                    {"id": "opt_xs", "name": "XS"},
                                    {"id": "opt_s", "name": "S"},
                                    {"id": "opt_m", "name": "M"},
                                ],
                            },
                            {
                                "id": "field_status_id",
                                "name": "Status",
                                "options": [
                                    {"id": "opt_backlog", "name": "Backlog"},
                                    {"id": "opt_done", "name": "Done"},
                                ],
                            },
                        ]
                    },
                }
            }
        }
    }
)


# ---------------------------------------------------------------------------
# Sample manifests and configs
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST = {
    "scope-1": {
        "number": 1,
        "nodeId": "I_node_1",
        "databaseId": 10001,
        "level": "scope",
        "title": "Test Project",
        "parent_ref": None,
        "priority": "P0",
        "size": "M",
        "blocking": [],
    },
    "initiative-1": {
        "number": 2,
        "nodeId": "I_node_2",
        "databaseId": 10002,
        "level": "initiative",
        "title": "Core Initiative",
        "parent_ref": "Test Project",
        "priority": "P0",
        "size": "L",
        "blocking": [],
    },
    "epic-1": {
        "number": 3,
        "nodeId": "I_node_3",
        "databaseId": 10003,
        "level": "epic",
        "title": "First Epic",
        "parent_ref": "Core Initiative",
        "priority": "P0",
        "size": "M",
        "blocking": [],
    },
    "story-1": {
        "number": 4,
        "nodeId": "I_node_4",
        "databaseId": 10004,
        "level": "story",
        "title": "Build the widget",
        "parent_ref": "First Epic",
        "priority": "P1",
        "size": "S",
        "blocking": ["Implement tokenizer"],
    },
    "task-1": {
        "number": 5,
        "nodeId": "I_node_5",
        "databaseId": 10005,
        "level": "task",
        "title": "Implement tokenizer",
        "parent_ref": "Build the widget",
        "priority": "P0",
        "size": "XS",
        "blocking": [],
    },
}

SAMPLE_CONFIG = {
    "project_id": "PVT_test",
    "org": "kdtix-open",
    "repo": "kdtix-open/test",
    "project_number": 8,
    "issue_type_ids": {
        "scope": "IT_scope",
        "initiative": "IT_init",
        "epic": "IT_epic",
        "story": "IT_story",
        "task": "IT_task",
    },
    "field_ids": {
        "Priority": {
            "id": "field_priority",
            "options": {"P0": "opt_p0", "P1": "opt_p1", "P2": "opt_p2"},
        },
        "Size": {
            "id": "field_size",
            "options": {"XS": "opt_xs", "S": "opt_s", "M": "opt_m"},
        },
        "Status": {
            "id": "field_status",
            "options": {"Backlog": "opt_backlog", "Done": "opt_done"},
        },
    },
}

MINIMAL_HIERARCHY = {
    "scope": {
        "title": "Test Project",
        "description": "A test.",
        "priority": "P0",
        "size": "M",
        "blocking": [],
    },
    "initiative": {
        "title": "Core Initiative",
        "description": "The init.",
        "priority": "P0",
        "size": "L",
        "blocking": [],
    },
    "epics": [
        {
            "title": "First Epic",
            "description": "An epic.",
            "priority": "P0",
            "size": "M",
            "blocking": [],
            "parent_ref": "Core Initiative",
        }
    ],
    "stories": [
        {
            "title": "Build the widget",
            "description": "A story.",
            "priority": "P1",
            "size": "S",
            "blocking": [],
            "parent_ref": "First Epic",
        }
    ],
    "tasks": [
        {
            "title": "Implement tokenizer",
            "description": "A task.",
            "priority": "P0",
            "size": "XS",
            "blocking": [],
            "parent_ref": "Build the widget",
        }
    ],
}

# Minimal compliant body — passes all P0 checks
GOOD_BODY = textwrap.dedent(
    """\
    ## Assumptions
    - None known.

    ## MoSCoW Classification
    | Must | This story is required |

    ## I Know I Am Done When
    TDD followed: failing test written BEFORE implementation

    ### Subtasks Needed
    | # | Task | Est | Done? |
    |---|------|-----|-------|
    | 1 | Impl | 1h  | No    |

    ### Release Value
    Enables widget creation.

    ### Why This Matters
    Core feature.

    ### TL;DR
    Build it.

    ## Security and Compliance
    - No PII handled.
    """
)

TDD_DONE_WHEN = (
    "## I Know I Am Done When\n"
    "TDD followed: failing test written BEFORE implementation\n"
)

SAMPLE_PLAN = textwrap.dedent(
    """\
    # Project Scope: Build the Widget Platform

    ## Initiative: Widget Core

    ### Epic: Widget API
    Priority: P0
    Size: M

    #### User Story: Create widget endpoint
    Priority: P0
    Size: S

    #### User Story: Read widget list
    Priority: P1
    Size: M

    ##### Task: Write OpenAPI spec
    Priority: P0
    Size: XS

    ##### Task: Implement handler
    Priority: P1
    Size: S
    """
)
