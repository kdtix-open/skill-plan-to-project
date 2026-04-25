"""Tests for `create_issues.py amend` subcommand (FR #33).

Focus: deterministic logic — argument validation, plan-vs-target matching,
duplicate detection, level-gating.  Network-touching pieces (gh issue
create / sub-issue link / GraphQL preflight) are exercised by integration
tests against a real repo, not unit tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.create_issues import (
    _AMEND_TARGET_RULES,
    AmendError,
    _validate_amend_plan,
    amend_backlog,
)

# ---------------------------------------------------------------------------
# _validate_amend_plan — returns the right top-level items per target kind
# ---------------------------------------------------------------------------


def test_validate_amend_plan_target_scope_returns_initiatives():
    hierarchy = {
        "scope": {"title": "Test Scope"},
        "initiatives": [{"title": "Init A"}, {"title": "Init B"}],
        "epics": [{"title": "Epic 1"}],
        "stories": [],
        "tasks": [],
    }
    out = _validate_amend_plan(hierarchy, "scope")
    assert len(out) == 2
    assert out[0]["title"] == "Init A"


def test_validate_amend_plan_target_initiative_returns_epics():
    hierarchy = {
        "scope": None,
        "initiatives": [],
        "epics": [{"title": "Epic 1"}, {"title": "Epic 2"}, {"title": "Epic 3"}],
        "stories": [],
        "tasks": [],
    }
    out = _validate_amend_plan(hierarchy, "initiative")
    assert len(out) == 3


def test_validate_amend_plan_target_epic_returns_stories():
    hierarchy = {
        "scope": None,
        "initiatives": [],
        "epics": [],
        "stories": [{"title": "Story A"}, {"title": "Story B"}],
        "tasks": [],
    }
    out = _validate_amend_plan(hierarchy, "epic")
    assert len(out) == 2


def test_validate_amend_plan_target_story_returns_tasks():
    hierarchy = {
        "scope": None,
        "initiatives": [],
        "epics": [],
        "stories": [],
        "tasks": [{"title": "Task X"}, {"title": "Task Y"}, {"title": "Task Z"}],
    }
    out = _validate_amend_plan(hierarchy, "story")
    assert len(out) == 3


def test_validate_amend_plan_singular_initiative_field():
    """Some plans set hierarchy['initiative'] (singular) instead of plural."""
    hierarchy = {
        "scope": None,
        "initiative": {"title": "Single Init"},
        "initiatives": [],
        "epics": [],
        "stories": [],
        "tasks": [],
    }
    out = _validate_amend_plan(hierarchy, "scope")
    assert len(out) == 1
    assert out[0]["title"] == "Single Init"


def test_validate_amend_plan_unknown_kind_raises():
    with pytest.raises(AmendError, match="Unknown target kind"):
        _validate_amend_plan({}, "unknown_kind")


def test_validate_amend_plan_empty_at_target_level_raises():
    """target=epic but plan has no stories — fail clearly."""
    hierarchy = {
        "scope": None,
        "initiatives": [],
        "epics": [{"title": "Epic 1"}],  # epics ignored when target is epic
        "stories": [],  # nothing to amend
        "tasks": [],
    }
    with pytest.raises(AmendError, match="no story-level items"):
        _validate_amend_plan(hierarchy, "epic")


# ---------------------------------------------------------------------------
# _AMEND_TARGET_RULES sanity
# ---------------------------------------------------------------------------


def test_amend_target_rules_cover_all_kinds():
    assert set(_AMEND_TARGET_RULES) == {"scope", "initiative", "epic", "story"}


def test_amend_target_rules_ignore_chain_is_consistent():
    """Each lower target should ignore strictly more levels than the higher one."""
    chain = ["scope", "initiative", "epic", "story"]
    for higher, lower in zip(chain, chain[1:]):
        higher_ignores = _AMEND_TARGET_RULES[higher]["ignore_levels"]
        lower_ignores = _AMEND_TARGET_RULES[lower]["ignore_levels"]
        assert higher_ignores < lower_ignores, (
            f"Expected {lower} to ignore strictly more levels than {higher}, "
            f"but {higher}={higher_ignores} and {lower}={lower_ignores}"
        )


def test_amend_target_rules_child_level_is_one_below_target():
    expected = {
        "scope": "initiative",
        "initiative": "epic",
        "epic": "story",
        "story": "task",
    }
    for kind, child in expected.items():
        assert _AMEND_TARGET_RULES[kind]["child_level"] == child


# ---------------------------------------------------------------------------
# amend_backlog — orchestration logic with mocked network
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config():
    return {
        "issue_type_ids": {
            "scope": "IT_scope",
            "initiative": "IT_init",
            "epic": "IT_epic",
            "story": "IT_story",
            "task": "IT_task",
        },
        "field_ids": {
            "Status": {"id": "FID_status", "options": {"Backlog": "OID_backlog"}},
            "Priority": {"id": "FID_pri", "options": {"P0": "P0_id", "P1": "P1_id"}},
            "Size": {"id": "FID_size", "options": {"S": "S_id", "M": "M_id"}},
        },
        "project_id": "PVT_test",
    }


def test_amend_backlog_idempotent_skips_existing_titles(fake_config, tmp_path):
    """When all plan epics match existing target sub-issues, nothing is created."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nVision blurb.\n\n"
        "## Initiative: ignored\n\nObjective blurb.\n\n"
        "### Epic: Existing One\n\nObjective text.\n\n"
        "### Epic: Existing Two\n\nObjective text.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 100,
            "title": "Initiative: Test Init",
            "level": "initiative",
            "parent_number": None,
        },
        {
            "number": 101,
            "title": "Epic: Existing One",
            "level": "epic",
            "parent_number": 100,
        },
        {
            "number": 102,
            "title": "Epic: Existing Two",
            "level": "epic",
            "parent_number": 100,
        },
    ]

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch("scripts.create_issues.create_all_issues") as create_mock,
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 9999}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        manifest = amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="initiative",
            target_number=100,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    # Both plan epics matched existing children → no creates
    assert manifest == {}
    create_mock.assert_not_called()
    # Report file should still be written for audit
    assert (tmp_path / "amend-report.json").exists()


def test_amend_backlog_force_creates_despite_match(fake_config, tmp_path):
    """--force overrides duplicate-skip behavior."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nVision blurb.\n\n"
        "## Initiative: ignored\n\nObjective blurb.\n\n"
        "### Epic: Existing One\n\nObjective text.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 100,
            "title": "Initiative: Test",
            "level": "initiative",
            "parent_number": None,
        },
        {
            "number": 101,
            "title": "Epic: Existing One",
            "level": "epic",
            "parent_number": 100,
        },
    ]

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            return_value={
                "epic-1": {
                    "number": 200,
                    "level": "epic",
                    "title": "Existing One",
                    "databaseId": 5000,
                    "parent_ref": None,
                }
            },
        ) as create_mock,
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 9999}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        manifest = amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="initiative",
            target_number=100,
            config=fake_config,
            output_dir=tmp_path,
            force=True,
            allow_shallow_subsections=True,
        )

    assert len(manifest) == 1
    create_mock.assert_called_once()


def test_amend_backlog_creates_only_below_target_level(fake_config, tmp_path):
    """target=scope: ignores plan's scope but creates init+epic+story."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored top\n\nVision blurb.\n\n"
        "## Initiative: New Init\n\nObjective.\n\n"
        "### Epic: New Epic\n\nObjective.\n\n"
        "#### Story: New Story\n\nTL;DR.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 50,
            "title": "Project Scope: Real PS",
            "level": "scope",
            "parent_number": None,
        },
    ]

    captured_hierarchy = {}

    def _capture_create(hierarchy, *args, **kwargs):
        captured_hierarchy.update(hierarchy)
        return {
            "initiative-1": {
                "number": 60,
                "level": "initiative",
                "title": "New Init",
                "databaseId": 6000,
                "parent_ref": None,
            },
            "epic-1": {
                "number": 61,
                "level": "epic",
                "title": "New Epic",
                "databaseId": 6001,
                "parent_ref": "New Init",
            },
            "story-1": {
                "number": 62,
                "level": "story",
                "title": "New Story",
                "databaseId": 6002,
                "parent_ref": "New Epic",
            },
        }

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            side_effect=_capture_create,
        ),
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 5000}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        manifest = amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="scope",
            target_number=50,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    # Synthetic hierarchy passed to create_all_issues should NOT include the
    # plan's scope (since target=scope means we IGNORE plan's scope).
    assert captured_hierarchy["scope"] is None
    assert len(captured_hierarchy["initiatives"]) == 1
    assert captured_hierarchy["initiatives"][0]["title"] == "New Init"
    assert len(manifest) == 3


def test_amend_backlog_target_epic_ignores_plan_init_and_epic(fake_config, tmp_path):
    """target=epic should attach new stories directly under the target epic."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nVision.\n\n"
        "## Initiative: ignored\n\nObjective.\n\n"
        "### Epic: ignored top epic\n\nObjective.\n\n"
        "#### Story: New Story 1\n\nTL;DR.\n\n"
        "#### Story: New Story 2\n\nTL;DR.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 184,
            "title": "Epic: Real Epic",
            "level": "epic",
            "parent_number": None,
        },
    ]

    captured = {}

    def _capture_create(hierarchy, *args, **kwargs):
        captured.update(hierarchy)
        return {
            "story-1": {
                "number": 200,
                "level": "story",
                "title": "New Story 1",
                "databaseId": 7000,
                "parent_ref": None,
            },
            "story-2": {
                "number": 201,
                "level": "story",
                "title": "New Story 2",
                "databaseId": 7001,
                "parent_ref": None,
            },
        }

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            side_effect=_capture_create,
        ),
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 5500}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        manifest = amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="epic",
            target_number=184,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    # No initiative/epic in synthetic hierarchy
    assert not captured.get("initiatives")
    assert not captured.get("epics")
    assert len(captured.get("stories") or []) == 2
    assert len(manifest) == 2


def test_amend_backlog_writes_amend_report_with_skipped(fake_config, tmp_path):
    """The amend-report.json file captures both created and skipped items."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nV.\n\n"
        "## Initiative: ignored\n\nO.\n\n"
        "### Epic: Existing\n\nO.\n\n"
        "### Epic: New\n\nO.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 100,
            "title": "Initiative: Test",
            "level": "initiative",
            "parent_number": None,
        },
        {
            "number": 101,
            "title": "Epic: Existing",
            "level": "epic",
            "parent_number": 100,
        },
    ]

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            return_value={
                "epic-1": {
                    "number": 300,
                    "level": "epic",
                    "title": "New",
                    "databaseId": 8000,
                    "parent_ref": None,
                }
            },
        ),
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 9999}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="initiative",
            target_number=100,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    import json

    report_path = tmp_path / "amend-report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["target"] == {"kind": "initiative", "number": 100}
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["matches_existing"] == 101
    assert len(data["created"]) == 1
    assert data["created"][0]["number"] == 300


# ---------------------------------------------------------------------------
# _cmd_amend — CLI dispatcher target-flag validation
# ---------------------------------------------------------------------------


def test_cmd_amend_no_target_raises():
    """No --target-* flag set → clear error."""
    from argparse import Namespace

    from scripts.create_issues import _cmd_amend

    args = Namespace(
        target_scope=None,
        target_initiative=None,
        target_epic=None,
        target_story=None,
        plan="ignored",
        org="o",
        repo="o/r",
        project=1,
        output_dir=None,
    )
    with pytest.raises(AmendError, match="Must pass exactly one"):
        _cmd_amend(args)


def test_cmd_amend_multiple_targets_raises():
    """Two --target-* flags set → clear error."""
    from argparse import Namespace

    from scripts.create_issues import _cmd_amend

    args = Namespace(
        target_scope=10,
        target_initiative=20,  # second one — should fail
        target_epic=None,
        target_story=None,
        plan="ignored",
        org="o",
        repo="o/r",
        project=1,
        output_dir=None,
    )
    with pytest.raises(AmendError, match="exactly one"):
        _cmd_amend(args)


def test_cmd_amend_target_story_routes_to_amend_backlog(fake_config, tmp_path):
    """--target-story 190 dispatches with target_kind='story'."""
    from argparse import Namespace

    from scripts.create_issues import _cmd_amend

    args = Namespace(
        target_scope=None,
        target_initiative=None,
        target_epic=None,
        target_story=190,
        plan=str(tmp_path / "noop.md"),
        org="kdtix-open",
        repo="kdtix-open/test",
        project=7,
        output_dir=None,
        force=False,
        allow_shallow_subsections=True,
        auto_create_issue_types=False,
    )
    # Don't actually run amend_backlog; just verify dispatch routing.
    with (
        patch("scripts.create_issues.preflight", return_value=fake_config),
        patch("scripts.create_issues.amend_backlog", return_value={}) as amend_mock,
    ):
        _cmd_amend(args)

    amend_mock.assert_called_once()
    call_kwargs = amend_mock.call_args.kwargs
    assert call_kwargs["target_kind"] == "story"
    assert call_kwargs["target_number"] == 190


def test_amend_backlog_fallback_resolves_top_level_via_ignored_titles(
    fake_config, tmp_path
):
    """When create_all_issues preserves parent_ref pointing at an ignored
    level (e.g. plan's scope title), the fallback uses ignored_titles to
    still find the right top-level new items for sub-issue linkage."""
    # Use target=story so all 4 ignored levels (scope/initiative/epic/story)
    # are exercised in the fallback's ignored-titles enumeration.
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: Plan Scope Title\n\nVision.\n\n"
        "## Initiative: Plan Init Title\n\nObjective.\n\n"
        "### Epic: Plan Epic Title\n\nObjective.\n\n"
        "#### Story: Plan Story Title\n\nTL;DR.\n\n"
        "##### Task: New Task A\n\nSummary.\n",
        encoding="utf-8",
    )

    fake_existing = [
        {
            "number": 190,
            "title": "Story: Real Story",
            "level": "story",
            "parent_number": None,
        },
    ]

    # create_all_issues returns parent_ref pointing at the plan's
    # ignored-story title — fallback path must still pick this up.
    fake_manifest = {
        "task-1": {
            "number": 800,
            "level": "task",
            "title": "New Task A",
            "databaseId": 9000,
            # parent_ref points to plan's IGNORED story title
            "parent_ref": "Plan Story Title",
        },
    }

    sub_issue_calls = []

    def _capture_run_gh(cmd, **kwargs):
        # Capture sub_issue link calls for assertion
        if isinstance(cmd, list) and "sub_issues" in " ".join(cmd):
            sub_issue_calls.append(cmd)
        # Mimic a successful CompletedProcess
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=fake_existing,
        ),
        patch("scripts.create_issues.create_all_issues", return_value=fake_manifest),
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 8500}
        ),
        patch("scripts.create_issues.run_gh", side_effect=_capture_run_gh),
    ):
        amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="story",
            target_number=190,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    # Expect exactly ONE sub-issue link call (for task #800)
    assert len(sub_issue_calls) == 1
    cmd = sub_issue_calls[0]
    # Verify it's linking #800 → target #190
    assert any("/repos/kdtix-open/test/issues/190/sub_issues" in arg for arg in cmd)
    assert any("sub_issue_id=9000" in arg for arg in cmd)


def test_amend_backlog_target_story_creates_tasks_only(fake_config, tmp_path):
    """target=story should attach new Tasks under the target Story."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nVision.\n\n"
        "## Initiative: ignored\n\nObjective.\n\n"
        "### Epic: ignored\n\nObjective.\n\n"
        "#### Story: ignored\n\nTL;DR.\n\n"
        "##### Task: New Task A\n\nSummary.\n\n"
        "##### Task: New Task B\n\nSummary.\n",
        encoding="utf-8",
    )

    captured = {}

    def _capture_create(hierarchy, *args, **kwargs):
        captured.update(hierarchy)
        return {
            "task-1": {
                "number": 800,
                "level": "task",
                "title": "New Task A",
                "databaseId": 9000,
                "parent_ref": None,
            },
            "task-2": {
                "number": 801,
                "level": "task",
                "title": "New Task B",
                "databaseId": 9001,
                "parent_ref": None,
            },
        }

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 190,
                    "title": "Story: Real Story",
                    "level": "story",
                    "parent_number": None,
                }
            ],
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            side_effect=_capture_create,
        ),
        patch(
            "scripts.create_issues._get_issue_ids", return_value={"databaseId": 8500}
        ),
        patch("scripts.create_issues.run_gh"),
    ):
        manifest = amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="story",
            target_number=190,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    # Synthetic hierarchy for target=story should have ONLY tasks
    assert not captured.get("initiatives")
    assert not captured.get("epics")
    assert not captured.get("stories")
    assert len(captured.get("tasks") or []) == 2
    assert len(manifest) == 2


def test_main_argparser_recognizes_amend_subcommand():
    """Smoke-test: the `amend` subcommand is registered on `main()`."""
    import sys
    from unittest.mock import patch

    from scripts.create_issues import main

    test_args = ["create_issues.py", "amend", "--help"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc:
        main()
    # --help exits 0
    assert exc.value.code == 0


def test_main_argparser_amend_requires_target_at_parse_or_dispatch_time():
    """Without any --target-* flag, dispatch raises AmendError."""
    import sys
    from unittest.mock import patch

    from scripts.create_issues import main

    test_args = [
        "create_issues.py",
        "amend",
        "--plan",
        "/dev/null",
        "--org",
        "kdtix-open",
        "--repo",
        "kdtix-open/test",
        "--project",
        "1",
    ]
    with (
        patch.object(sys, "argv", test_args),
        patch("scripts.create_issues.preflight") as preflight_mock,
        pytest.raises(SystemExit) as exc,
    ):
        main()
    # AmendError caught + sys.exit(1) by main's exception handler
    assert exc.value.code == 1
    # preflight should NOT have been called — failure happened before
    preflight_mock.assert_not_called()


def test_amend_backlog_target_initiative_emits_correct_log_line(
    fake_config, tmp_path, capsys
):
    """Verify the [amend] log header reflects target kind + number + counts."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Project Scope: ignored\n\nVision.\n\n"
        "## Initiative: ignored\n\nObjective.\n\n"
        "### Epic: New Epic A\n\nObjective.\n\n"
        "### Epic: New Epic B\n\nObjective.\n",
        encoding="utf-8",
    )

    with (
        patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 100,
                    "title": "Initiative: Test",
                    "level": "initiative",
                    "parent_number": None,
                }
            ],
        ),
        patch(
            "scripts.create_issues.create_all_issues",
            return_value={
                "epic-1": {
                    "number": 200,
                    "level": "epic",
                    "title": "New Epic A",
                    "databaseId": 1,
                    "parent_ref": None,
                },
                "epic-2": {
                    "number": 201,
                    "level": "epic",
                    "title": "New Epic B",
                    "databaseId": 2,
                    "parent_ref": None,
                },
            },
        ),
        patch("scripts.create_issues._get_issue_ids", return_value={"databaseId": 99}),
        patch("scripts.create_issues.run_gh"),
    ):
        amend_backlog(
            plan_path=str(plan),
            repo="kdtix-open/test",
            target_kind="initiative",
            target_number=100,
            config=fake_config,
            output_dir=tmp_path,
            allow_shallow_subsections=True,
        )

    out = capsys.readouterr().out
    assert "[amend] target=initiative #100 plan-top-level=epic top-items=2" in out
    assert "linking 2 new epic(s)" in out
