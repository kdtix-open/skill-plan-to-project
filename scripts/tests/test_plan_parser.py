"""
RED-phase tests for Task #15: Implement Markdown Plan Parser.

Tests are written FIRST (TDD Red phase). They define the exact contract
for parse_plan() before any implementation exists.

All tests should FAIL until create-issues.py is implemented.
"""

import textwrap
from pathlib import Path

import pytest

# Import the module under test — will fail until scripts/create-issues.py exists
from scripts import create_issues  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PLAN = textwrap.dedent("""\
    # Project Scope: PS-001 Test Project
    Priority: P0
    Size: M

    ## Initiative: INIT-001 Test Initiative
    Priority: P0
    Size: L

    ### Epic: EP-001 First Epic
    Priority: P0
    Size: M

    #### Story: Build the widget
    Priority: P1
    Size: S

    ##### Task: Implement tokenizer
    Priority: P0
    Size: XS
""")

BLOCKING_PLAN = textwrap.dedent("""\
    # Project Scope: PS-001 Blocking Test
    Priority: P0
    Size: M

    ## Initiative: INIT-001 Core
    Priority: P0
    Size: M

    ### Epic: EP-001 Parser Epic
    Priority: P0
    Size: S
    Blocks: Story: Build the widget

    #### Story: Build the widget
    Priority: P0
    Size: S
    Blocks: Task: Implement tokenizer

    ##### Task: Implement tokenizer
    Priority: P0
    Size: XS
""")

DEFAULTS_PLAN = textwrap.dedent("""\
    # Project Scope: PS-001 Defaults Test

    ## Initiative: INIT-001 No Metadata

    ### Epic: EP-001 Plain Epic

    #### Story: Plain story

    ##### Task: Plain task
""")

DOCUMENTED_LEVEL_PLAN = textwrap.dedent("""\
    # Project Scope: PS-001 Documented Levels
    Priority: P0
    Size: M

    ## Initiative: INIT-001 Documented Initiative
    Priority: P0
    Size: L

    ### Epic: EP-001 Parser Epic
    Priority: P0
    Size: M

    ### Story: Parse documented headings
    Priority: P1
    Size: S

    #### Task: Support documented task heading
    Priority: P0
    Size: XS
""")


@pytest.fixture
def tmp_plan(tmp_path: Path) -> callable:
    """Factory: write plan text to a temp file, return the path."""

    def _write(content: str) -> Path:
        p = tmp_path / "plan.md"
        p.write_text(content, encoding="utf-8")
        return p

    return _write


# ---------------------------------------------------------------------------
# Task #15 AC: parse_plan() returns correct top-level structure
# ---------------------------------------------------------------------------


class TestParsePlanStructure:
    def test_returns_dict_with_all_five_keys(self, tmp_plan):
        path = tmp_plan(MINIMAL_PLAN)
        result = create_issues.parse_plan(str(path))
        assert isinstance(result, dict)
        for key in ("scope", "initiative", "epics", "stories", "tasks"):
            assert key in result, f"parse_plan() result must have key '{key}'"

    def test_scope_is_single_dict(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["scope"], dict)

    def test_initiative_is_single_dict(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["initiative"], dict)

    def test_epics_is_list(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["epics"], list)
        assert len(result["epics"]) == 1

    def test_stories_is_list(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["stories"], list)
        assert len(result["stories"]) == 1

    def test_tasks_is_list(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["tasks"], list)
        assert len(result["tasks"]) == 1


# ---------------------------------------------------------------------------
# Task #15 AC: each item has required fields
# ---------------------------------------------------------------------------


class TestParsePlanItemFields:
    REQUIRED_FIELDS = ("title", "description", "priority", "size", "blocking")

    def test_scope_has_all_required_fields(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        for field in self.REQUIRED_FIELDS:
            assert field in result["scope"], f"scope must have field '{field}'"

    def test_initiative_has_all_required_fields(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        for field in self.REQUIRED_FIELDS:
            assert (
                field in result["initiative"]
            ), f"initiative must have field '{field}'"

    def test_epic_has_all_required_fields_plus_parent_ref(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        epic = result["epics"][0]
        for field in (*self.REQUIRED_FIELDS, "parent_ref"):
            assert field in epic, f"epic must have field '{field}'"

    def test_story_has_all_required_fields_plus_parent_ref(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        story = result["stories"][0]
        for field in (*self.REQUIRED_FIELDS, "parent_ref"):
            assert field in story, f"story must have field '{field}'"

    def test_task_has_all_required_fields_plus_parent_ref(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        task = result["tasks"][0]
        for field in (*self.REQUIRED_FIELDS, "parent_ref"):
            assert field in task, f"task must have field '{field}'"

    def test_blocking_is_list(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert isinstance(result["scope"]["blocking"], list)
        assert isinstance(result["epics"][0]["blocking"], list)


# ---------------------------------------------------------------------------
# Task #15 AC: correct title extraction
# ---------------------------------------------------------------------------


class TestParsePlanTitles:
    def test_scope_title_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert "Test Project" in result["scope"]["title"]

    def test_initiative_title_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert "Test Initiative" in result["initiative"]["title"]

    def test_epic_title_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert "First Epic" in result["epics"][0]["title"]

    def test_story_title_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert "Build the widget" in result["stories"][0]["title"]

    def test_task_title_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert "Implement tokenizer" in result["tasks"][0]["title"]


# ---------------------------------------------------------------------------
# Task #15 AC: priority and size extraction
# ---------------------------------------------------------------------------


class TestParsePlanPriorityAndSize:
    def test_explicit_priority_p0_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["scope"]["priority"] == "P0"

    def test_explicit_size_m_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["scope"]["size"] == "M"

    def test_story_priority_p1_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["stories"][0]["priority"] == "P1"

    def test_task_size_xs_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["tasks"][0]["size"] == "XS"

    def test_missing_priority_defaults_to_p1(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(DEFAULTS_PLAN)))
        assert result["scope"]["priority"] == "P1"

    def test_missing_size_defaults_to_m(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(DEFAULTS_PLAN)))
        assert result["scope"]["size"] == "M"


# ---------------------------------------------------------------------------
# Task #15 AC: blocking relationships
# ---------------------------------------------------------------------------


class TestParsePlanBlocking:
    def test_no_blocking_gives_empty_list(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["scope"]["blocking"] == []

    def test_blocks_keyword_extracted(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(BLOCKING_PLAN)))
        epic = result["epics"][0]
        assert len(epic["blocking"]) > 0

    def test_story_blocks_task(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(BLOCKING_PLAN)))
        story = result["stories"][0]
        assert any("tokenizer" in b.lower() for b in story["blocking"])


# ---------------------------------------------------------------------------
# Task #15 AC: parent_ref correctness
# ---------------------------------------------------------------------------


class TestParsePlanParentRef:
    def test_initiative_parent_ref_is_scope(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["initiative"]["parent_ref"] is not None
        assert "Project" in result["initiative"]["parent_ref"]

    def test_epic_parent_ref_is_initiative(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["epics"][0]["parent_ref"] is not None

    def test_story_parent_ref_is_epic(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["stories"][0]["parent_ref"] is not None
        assert (
            "Epic" in result["stories"][0]["parent_ref"]
            or "EP-" in result["stories"][0]["parent_ref"]
            or "First Epic" in result["stories"][0]["parent_ref"]
        )

    def test_task_parent_ref_is_story(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(MINIMAL_PLAN)))
        assert result["tasks"][0]["parent_ref"] is not None
        assert "widget" in result["tasks"][0]["parent_ref"].lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestParsePlanEdgeCases:
    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            create_issues.parse_plan("/nonexistent/path/plan.md")

    def test_multiple_epics_all_parsed(self, tmp_plan):
        plan = textwrap.dedent("""\
            # Project Scope: PS-001 Multi
            ## Initiative: INIT-001 Multi
            ### Epic: EP-001 Alpha
            ### Epic: EP-002 Beta
            ### Epic: EP-003 Gamma
        """)
        result = create_issues.parse_plan(str(tmp_plan(plan)))
        assert len(result["epics"]) == 3

    def test_multiple_stories_under_different_epics(self, tmp_plan):
        plan = textwrap.dedent("""\
            # Project Scope: PS-001 Multi
            ## Initiative: INIT-001 Core
            ### Epic: EP-001 Alpha
            #### Story: Story A1
            #### Story: Story A2
            ### Epic: EP-002 Beta
            #### Story: Story B1
        """)
        result = create_issues.parse_plan(str(tmp_plan(plan)))
        assert len(result["stories"]) == 3

    def test_documented_story_and_task_heading_levels_are_supported(self, tmp_plan):
        result = create_issues.parse_plan(str(tmp_plan(DOCUMENTED_LEVEL_PLAN)))

        assert len(result["stories"]) == 1
        assert result["stories"][0]["title"] == "Parse documented headings"
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["title"] == "Support documented task heading"
