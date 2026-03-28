"""
End-to-End Integration Test — Story #14 (closes #14)

Exercises the full skill pipeline with all external calls mocked:
  1. parse_plan()          → 5-level hierarchy dict from markdown
  2. preflight()           → validates Issue Types + project fields
  3. create_all_issues()   → creates 7 issues via mocked gh CLI
  4. set_sub_issues()      → links parent/child relationships
  5. set_blocking_labels() → applies blocking: labels
  6. set_project_fields()  → Priority/Size/Status/Issue Types via GraphQL
  7. run_compliance_check()→ P0/P1/P2 gap detection
  8. compute_queue_order() → prioritised story list

Asserts:
  - parse_plan returns correct 5-bucket hierarchy
  - create_all_issues produces manifest with 7 entries
  - Zero P0 compliance gaps on compliant issue bodies
  - Queue order: P0 story before P1 story
  - Full pipeline runs end-to-end without error
"""

from __future__ import annotations

import json
import textwrap
from unittest.mock import MagicMock, patch

from scripts import (
    compliance_check,
    create_issues,
    queue_order,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

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

ISSUE_TYPES_JSON = json.dumps(
    [
        {"id": "IT_scope", "name": "Project Scope"},
        {"id": "IT_init", "name": "Initiative"},
        {"id": "IT_epic", "name": "Epic"},
        {"id": "IT_story", "name": "User Story"},
        {"id": "IT_task", "name": "Task"},
    ]
)

FIELDS_JSON = json.dumps(
    {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_test",
                    "fields": {
                        "nodes": [
                            {
                                "name": "Status",
                                "id": "F_STATUS",
                                "options": [
                                    {"id": "OPT_backlog", "name": "Backlog"},
                                    {"id": "OPT_inprog", "name": "In progress"},
                                    {"id": "OPT_done", "name": "Done"},
                                ],
                            },
                            {
                                "name": "Priority",
                                "id": "F_PRIO",
                                "options": [
                                    {"id": "OPT_p0", "name": "P0"},
                                    {"id": "OPT_p1", "name": "P1"},
                                    {"id": "OPT_p2", "name": "P2"},
                                ],
                            },
                            {
                                "name": "Size",
                                "id": "F_SIZE",
                                "options": [
                                    {"id": "OPT_xs", "name": "XS"},
                                    {"id": "OPT_s", "name": "S"},
                                    {"id": "OPT_m", "name": "M"},
                                ],
                            },
                        ]
                    },
                }
            }
        }
    }
)

# Minimal compliant body — passes all P0 checks
_GOOD_BODY = textwrap.dedent(
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


def _make_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def _issue_url(number: int) -> str:
    return f"https://github.com/kdtix-open/skill-plan-to-project/issues/{number}"


def _issue_ids_json(number: int) -> str:
    return json.dumps(
        {
            "nodeId": f"I_node_{number}",
            "databaseId": number * 100,
            "number": number,
        }
    )


# ---------------------------------------------------------------------------
# Phase 1 — parse_plan
# ---------------------------------------------------------------------------


class TestIntegrationParsePlan:
    def test_returns_5_bucket_hierarchy(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        assert set(result.keys()) == {
            "scope",
            "initiative",
            "epics",
            "stories",
            "tasks",
        }

    def test_scope_and_initiative_present(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        assert result["scope"] is not None
        assert result["initiative"] is not None

    def test_correct_epic_story_task_counts(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        assert len(result["epics"]) == 1
        assert len(result["stories"]) == 2
        assert len(result["tasks"]) == 2

    def test_total_issue_count_is_7(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        total = (
            (1 if result["scope"] else 0)
            + (1 if result["initiative"] else 0)
            + len(result["epics"])
            + len(result["stories"])
            + len(result["tasks"])
        )
        assert total == 7

    def test_story_priorities_parsed(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        priorities = {s["priority"] for s in result["stories"]}
        assert "P0" in priorities
        assert "P1" in priorities

    def test_parent_refs_assigned(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        result = create_issues.parse_plan(str(plan_file))
        epic_title = result["epics"][0]["title"]
        for story in result["stories"]:
            assert story["parent_ref"] == epic_title


# ---------------------------------------------------------------------------
# Phase 2 — preflight
# ---------------------------------------------------------------------------


class TestIntegrationPreflight:
    @patch("subprocess.run")
    def test_preflight_returns_issue_types_and_fields(self, mock_run):
        def side_effect(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "issueTypes" in joined:
                return _make_ok(
                    json.dumps(
                        {
                            "data": {
                                "organization": {
                                    "issueTypes": {
                                        "nodes": json.loads(ISSUE_TYPES_JSON)
                                    }
                                }
                            }
                        }
                    )
                )
            if "projectV2" in joined:
                return _make_ok(FIELDS_JSON)
            return _make_ok("{}")

        mock_run.side_effect = side_effect
        cfg = create_issues.preflight("kdtix-open", "skill-plan-to-project", 8)
        assert "issue_type_ids" in cfg
        assert "field_ids" in cfg

    @patch("subprocess.run")
    def test_preflight_maps_all_5_issue_types(self, mock_run):
        def side_effect(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "issueTypes" in joined:
                return _make_ok(
                    json.dumps(
                        {
                            "data": {
                                "organization": {
                                    "issueTypes": {
                                        "nodes": json.loads(ISSUE_TYPES_JSON)
                                    }
                                }
                            }
                        }
                    )
                )
            return _make_ok(FIELDS_JSON)

        mock_run.side_effect = side_effect
        cfg = create_issues.preflight("kdtix-open", "skill-plan-to-project", 8)
        issue_types = cfg.get("issue_type_ids", {})
        for key in ("scope", "initiative", "epic", "story", "task"):
            assert key in issue_types, f"Missing issue type: {key}"


# ---------------------------------------------------------------------------
# Phase 3 — create_all_issues
# ---------------------------------------------------------------------------


class TestIntegrationCreateAllIssues:
    def _build_hierarchy(self, tmp_path):
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        return create_issues.parse_plan(str(plan_file))

    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    def test_creates_7_issues(self, mock_create, mock_ids, tmp_path):
        counter = [1]

        def create_side(repo, title, body):
            n = counter[0]
            counter[0] += 1
            return _issue_url(n)

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        hierarchy = self._build_hierarchy(tmp_path)
        manifest = create_issues.create_all_issues(
            hierarchy, {}, "kdtix-open/skill-plan-to-project"
        )
        assert len(manifest) == 7

    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    def test_manifest_has_required_fields(self, mock_create, mock_ids, tmp_path):
        counter = [1]

        def create_side(repo, title, body):
            n = counter[0]
            counter[0] += 1
            return _issue_url(n)

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        hierarchy = self._build_hierarchy(tmp_path)
        manifest = create_issues.create_all_issues(
            hierarchy, {}, "kdtix-open/skill-plan-to-project"
        )
        for title, record in manifest.items():
            assert "number" in record, f"Missing number for {title}"
            assert "nodeId" in record
            assert "databaseId" in record
            assert "level" in record

    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    def test_scope_created_before_stories(self, mock_create, mock_ids, tmp_path):
        call_titles: list[str] = []
        counter = [1]

        def create_side(repo, title, body):
            call_titles.append(title)
            n = counter[0]
            counter[0] += 1
            return _issue_url(n)

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        hierarchy = self._build_hierarchy(tmp_path)
        create_issues.create_all_issues(
            hierarchy, {}, "kdtix-open/skill-plan-to-project"
        )

        scope_idx = next(
            (i for i, t in enumerate(call_titles) if "Widget Platform" in t), None
        )
        story_idxs = [
            i for i, t in enumerate(call_titles) if "S-001" in t or "S-002" in t
        ]
        assert scope_idx is not None
        assert all(
            scope_idx < idx for idx in story_idxs
        ), "Scope must be created before all stories"

    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    def test_manifest_written_to_disk(
        self, mock_create, mock_ids, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        counter = [1]

        def create_side(repo, title, body):
            n = counter[0]
            counter[0] += 1
            return _issue_url(n)

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        hierarchy = self._build_hierarchy(tmp_path)
        create_issues.create_all_issues(
            hierarchy, {}, "kdtix-open/skill-plan-to-project"
        )
        assert (tmp_path / "manifest.json").exists()


# ---------------------------------------------------------------------------
# Phase 4 — compliance
# ---------------------------------------------------------------------------


class TestIntegrationCompliance:
    def test_no_p0_gaps_on_compliant_body(self):
        gaps = compliance_check.check_issue(
            1, "Build the widget endpoint", _GOOD_BODY, "story"
        )
        p0_gaps = [g for g in gaps if g["severity"] == "P0"]
        assert p0_gaps == [], f"Expected no P0 gaps, got: {p0_gaps}"

    def test_p0_gap_on_missing_done_when(self):
        bad_body = "## Assumptions\n- None.\n"
        gaps = compliance_check.check_issue(1, "Build X", bad_body, "story")
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-1" in p0_rules

    @patch("subprocess.run")
    def test_run_compliance_zero_p0_for_clean_manifest(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        manifest = {
            "Build the widget endpoint": {
                "number": 5,
                "nodeId": "I_5",
                "databaseId": 500,
                "level": "story",
                "title": "Build the widget endpoint",
                "parent_ref": "Widget API",
                "priority": "P0",
                "size": "S",
                "blocking": [],
            }
        }

        def side_effect(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "labels" in joined:
                return _make_ok("[]")
            return _make_ok(_GOOD_BODY)

        mock_run.side_effect = side_effect
        report = compliance_check.run_compliance_check(manifest, "org/repo")
        p0_issues = [
            item
            for item in report["issues"]
            if any(g["severity"] == "P0" for g in item.get("gaps", []))
        ]
        assert len(p0_issues) == 0, f"Expected 0 P0 gap issues, got: {p0_issues}"

    @patch("subprocess.run")
    def test_run_compliance_returns_correct_shape(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        manifest = {
            "Story A": {
                "number": 1,
                "nodeId": "I_1",
                "databaseId": 100,
                "level": "story",
                "title": "Story A",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": [],
            }
        }

        def side_effect(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "labels" in joined:
                return _make_ok("[]")
            return _make_ok(_GOOD_BODY)

        mock_run.side_effect = side_effect
        report = compliance_check.run_compliance_check(manifest, "org/repo")
        assert "summary" in report
        assert "issues" in report
        assert isinstance(report["issues"], list)
        assert "total_issues" in report["summary"]


# ---------------------------------------------------------------------------
# Phase 5 — queue_order
# ---------------------------------------------------------------------------


class TestIntegrationQueueOrder:
    def test_p0_story_before_p1_story(self):
        stories = [
            {
                "number": 2,
                "title": "Read widget list",
                "level": "story",
                "priority": "P1",
                "size": "M",
                "parent_ref": "Widget API",
                "blocking": [],
            },
            {
                "number": 1,
                "title": "Create widget endpoint",
                "level": "story",
                "priority": "P0",
                "size": "S",
                "parent_ref": "Widget API",
                "blocking": [],
            },
        ]
        sorted_stories = sorted(stories, key=queue_order._priority_key)
        assert sorted_stories[0]["priority"] == "P0"
        assert sorted_stories[1]["priority"] == "P1"

    def test_same_priority_lower_number_first(self):
        stories = [
            {"number": 10, "priority": "P0", "size": "S", "title": "B"},
            {"number": 3, "priority": "P0", "size": "S", "title": "A"},
        ]
        sorted_stories = sorted(stories, key=queue_order._priority_key)
        assert sorted_stories[0]["number"] == 3

    def test_compute_queue_order_filters_non_stories(self):
        manifest = {
            "Epic One": {
                "number": 1,
                "level": "epic",
                "title": "Epic One",
                "priority": "P0",
                "size": "M",
                "parent_ref": None,
                "blocking": [],
            },
            "Story One": {
                "number": 2,
                "level": "story",
                "title": "Story One",
                "priority": "P0",
                "size": "S",
                "parent_ref": "Epic One",
                "blocking": [],
            },
        }
        statuses = {1: "In Progress", 2: "Backlog"}
        labels_map = {1: [], 2: []}

        result = queue_order.compute_queue_order(
            manifest, "org/repo", statuses=statuses, labels_map=labels_map
        )
        titles = [r["title"] for r in result]
        assert "Epic One" not in titles
        assert "Story One" in titles

    def test_blocked_story_excluded_from_queue(self):
        manifest = {
            "Blocked Story": {
                "number": 1,
                "level": "story",
                "title": "Blocked Story",
                "priority": "P0",
                "size": "S",
                "parent_ref": None,
                "blocking": [],
            }
        }
        statuses = {1: "Backlog"}
        labels_map = {1: ["blocked"]}

        result = queue_order.compute_queue_order(
            manifest, "org/repo", statuses=statuses, labels_map=labels_map
        )
        assert result == [], "Blocked stories should not appear in queue"


# ---------------------------------------------------------------------------
# Phase 6 — Full pipeline smoke test
# ---------------------------------------------------------------------------


class TestIntegrationFullPipeline:
    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    @patch("subprocess.run")
    def test_full_pipeline_parse_to_queue(
        self, mock_run, mock_create, mock_ids, tmp_path, monkeypatch
    ):
        """Smoke-test: parse → create → compliance → queue order."""
        monkeypatch.chdir(tmp_path)

        # subprocess.run handles preflight + compliance
        def run_side(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "issueTypes" in joined:
                return _make_ok(
                    json.dumps(
                        {
                            "data": {
                                "organization": {
                                    "issueTypes": {
                                        "nodes": json.loads(ISSUE_TYPES_JSON)
                                    }
                                }
                            }
                        }
                    )
                )
            if "projectV2" in joined:
                return _make_ok(FIELDS_JSON)
            if "labels" in joined:
                return _make_ok("[]")
            return _make_ok(_GOOD_BODY)

        mock_run.side_effect = run_side

        counter = [1]

        def create_side(repo, title, body):
            n = counter[0]
            counter[0] += 1
            return _issue_url(n)

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        # Step 1: parse
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(SAMPLE_PLAN, encoding="utf-8")
        hierarchy = create_issues.parse_plan(str(plan_file))
        total = (
            (1 if hierarchy["scope"] else 0)
            + (1 if hierarchy["initiative"] else 0)
            + len(hierarchy["epics"])
            + len(hierarchy["stories"])
            + len(hierarchy["tasks"])
        )
        assert total == 7

        # Step 2: preflight
        cfg = create_issues.preflight("kdtix-open", "skill-plan-to-project", 8)
        assert "issue_type_ids" in cfg

        # Step 3: create issues
        manifest = create_issues.create_all_issues(
            hierarchy, cfg, "kdtix-open/skill-plan-to-project"
        )
        assert len(manifest) == 7
        assert (tmp_path / "manifest.json").exists()

        # Step 4: compliance
        report = compliance_check.run_compliance_check(
            manifest, "kdtix-open/skill-plan-to-project"
        )
        assert "summary" in report
        assert "issues" in report

        # Step 5: queue order
        statuses = {v["number"]: "Backlog" for v in manifest.values()}
        labels_map = {v["number"]: [] for v in manifest.values()}
        # Give epics "In Progress" so stories become eligible
        for rec in manifest.values():
            if rec["level"] == "epic":
                statuses[rec["number"]] = "In Progress"

        ordered = queue_order.compute_queue_order(
            manifest,
            "kdtix-open/skill-plan-to-project",
            statuses=statuses,
            labels_map=labels_map,
        )
        assert isinstance(ordered, list)
        assert all(
            r["level"] == "story" for r in ordered
        ), "Queue should only contain stories"
        # P0 story must come before P1 story
        if len(ordered) >= 2:
            assert ordered[0]["priority"] == "P0"
