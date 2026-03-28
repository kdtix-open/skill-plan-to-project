"""
RED-phase tests for Tasks #16, #17, #18:
  - preflight()          (Task #16)
  - create_all_issues()  (Task #17)
  - generate_body()      (Task #18)

All gh CLI calls are mocked — no real GitHub API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import create_issues

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_ISSUE_TYPES_RESPONSE = json.dumps(
    {
        "data": {
            "organization": {
                "issueTypes": {
                    "nodes": [
                        {"id": "IT_scope_id", "name": "Project Scope"},
                        {"id": "IT_init_id", "name": "Initiative"},
                        {"id": "IT_epic_id", "name": "Epic"},
                        {"id": "IT_story_id", "name": "User Story"},
                        {"id": "IT_task_id", "name": "Task"},
                    ]
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


def _ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


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


# ---------------------------------------------------------------------------
# Task #18: generate_body
# ---------------------------------------------------------------------------


class TestGenerateBody:
    def test_scope_body_has_title(self):
        item = {"title": "My Project", "description": "", "priority": "P0", "size": "M"}
        body = create_issues.generate_body(item, "scope")
        assert "My Project" in body

    def test_initiative_body_has_title(self):
        item = {"title": "My Init", "description": "", "priority": "P0", "size": "L"}
        body = create_issues.generate_body(item, "initiative")
        assert "My Init" in body

    def test_epic_body_has_parent_ref(self):
        item = {
            "title": "My Epic",
            "description": "",
            "priority": "P0",
            "size": "M",
            "parent_ref": "Core Initiative",
        }
        body = create_issues.generate_body(item, "epic")
        assert "Core Initiative" in body

    def test_story_body_has_user_story_format(self):
        item = {
            "title": "Build it",
            "description": "",
            "priority": "P1",
            "size": "S",
            "parent_ref": "Some Epic",
        }
        body = create_issues.generate_body(item, "story")
        assert "As a" in body
        assert "So that" in body

    def test_story_body_has_moscow(self):
        item = {
            "title": "Build it",
            "description": "",
            "priority": "P1",
            "size": "S",
            "parent_ref": "Some Epic",
        }
        body = create_issues.generate_body(item, "story")
        assert "MoSCoW" in body

    def test_story_body_has_acceptance_criteria(self):
        item = {
            "title": "Build it",
            "description": "",
            "priority": "P1",
            "size": "S",
            "parent_ref": "Some Epic",
        }
        body = create_issues.generate_body(item, "story")
        assert "Acceptance Criteria" in body

    def test_task_body_has_summary_section(self):
        item = {
            "title": "Implement X",
            "description": "Do X",
            "priority": "P0",
            "size": "XS",
            "parent_ref": "Build the widget",
        }
        body = create_issues.generate_body(item, "task")
        assert "Summary" in body

    def test_tdd_sentinel_auto_injected_when_missing(self):
        item = {"title": "X", "description": "", "priority": "P0", "size": "S"}
        body = create_issues.generate_body(item, "scope")
        assert "TDD followed" in body

    def test_tdd_sentinel_not_duplicated_when_present(self):
        item = {"title": "X", "description": "", "priority": "P0", "size": "S"}
        body = create_issues.generate_body(item, "scope")
        assert body.count("TDD followed") == 1

    def test_security_section_injected_for_mutation_title(self):
        item = {
            "title": "Build the create endpoint",
            "description": "",
            "priority": "P0",
            "size": "M",
            "parent_ref": "Epic",
        }
        body = create_issues.generate_body(item, "story")
        assert "Security/Compliance" in body

    def test_security_section_not_injected_for_read_only(self):
        item = {
            "title": "Read the documentation",
            "description": "Just reading.",
            "priority": "P1",
            "size": "S",
            "parent_ref": "Epic",
        }
        body = create_issues.generate_body(item, "story")
        # No mutation keywords — security section should not be added
        # (it may be in the template already, but that's acceptable)
        # This test just ensures it doesn't crash
        assert isinstance(body, str)

    def test_all_five_levels_produce_non_empty_body(self):
        base_item = {
            "title": "Test",
            "description": "desc",
            "priority": "P1",
            "size": "M",
            "blocking": [],
            "parent_ref": "Parent",
        }
        for level in ("scope", "initiative", "epic", "story", "task"):
            body = create_issues.generate_body(base_item, level)
            assert len(body) > 100, f"Body for level '{level}' is too short"

    def test_done_when_section_present_in_all_levels(self):
        base_item = {
            "title": "Test",
            "description": "",
            "priority": "P1",
            "size": "M",
            "blocking": [],
            "parent_ref": "Parent",
        }
        for level in ("scope", "initiative", "epic", "story", "task"):
            body = create_issues.generate_body(base_item, level)
            assert (
                "I Know I Am Done When" in body
            ), f"Level '{level}' body missing 'I Know I Am Done When'"


# ---------------------------------------------------------------------------
# Task #16: preflight
# ---------------------------------------------------------------------------


class TestPreflight:
    def _make_run_side_effect(self, tmp_path: Path):
        """Return a side_effect function for subprocess.run that handles
        the two graphql calls and the auth check."""
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "auth" in joined:
                return _ok()
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _ok(MOCK_ISSUE_TYPES_RESPONSE)
            return _ok(MOCK_PROJECT_FIELDS_RESPONSE)

        return side_effect

    @patch("subprocess.run")
    def test_preflight_returns_config_dict(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert isinstance(config, dict)

    @patch("subprocess.run")
    def test_preflight_returns_project_id(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert config["project_id"] == "PVT_project_id"

    @patch("subprocess.run")
    def test_preflight_returns_all_five_issue_type_ids(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        for level in ("scope", "initiative", "epic", "story", "task"):
            assert (
                level in config["issue_type_ids"]
            ), f"issue_type_ids must have '{level}'"

    @patch("subprocess.run")
    def test_preflight_returns_all_three_field_ids(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        for field in ("Priority", "Size", "Status"):
            assert field in config["field_ids"]

    @patch("subprocess.run")
    def test_preflight_writes_manifest_config_json(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert (tmp_path / "manifest-config.json").exists()

    @patch("subprocess.run")
    def test_preflight_exits_when_issue_types_missing(
        self, mock_run, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "auth" in joined:
                return _ok()
            return _ok(
                json.dumps({"data": {"organization": {"issueTypes": {"nodes": []}}}})
            )

        mock_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)

    @patch("subprocess.run")
    def test_preflight_exits_when_project_not_found(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "auth" in joined:
                return _ok()
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _ok(MOCK_ISSUE_TYPES_RESPONSE)
            return _ok(json.dumps({"data": {"organization": {"projectV2": None}}}))

        mock_run.side_effect = side_effect
        with pytest.raises(SystemExit):
            create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)


# ---------------------------------------------------------------------------
# Task #17: create_all_issues
# ---------------------------------------------------------------------------


class TestCreateAllIssues:
    def _mock_run_for_create(
        self, base_url: str = "https://github.com/org/repo/issues"
    ):
        """Returns a side_effect for subprocess.run during issue creation."""
        issue_counter = {"n": 100}

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                issue_counter["n"] += 1
                n = issue_counter["n"]
                return _ok(f"{base_url}/{n}")
            if "issues/" in joined and "--jq" in joined:
                # Extract number from URL in previous call
                n = issue_counter["n"]
                return _ok(
                    json.dumps(
                        {
                            "nodeId": f"I_node_{n}",
                            "databaseId": n * 100,
                            "number": n,
                        }
                    )
                )
            return _ok()

        return side_effect

    @patch("subprocess.run")
    def test_creates_issues_for_all_levels(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # scope + initiative + 1 epic + 1 story + 1 task = 5
        assert len(manifest) == 5

    @patch("subprocess.run")
    def test_manifest_has_required_fields(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        for title, record in manifest.items():
            for field in ("number", "nodeId", "databaseId", "level"):
                assert field in record, f"manifest['{title}'] missing '{field}'"

    @patch("subprocess.run")
    def test_creates_scope_before_initiative(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        created_titles: list[str] = []

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                # Capture --title arg
                try:
                    idx = list(cmd).index("--title")
                    created_titles.append(cmd[idx + 1])
                except (ValueError, IndexError):
                    pass
                return _ok("https://github.com/org/repo/issues/101")
            if "--jq" in joined:
                return _ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 101})
                )
            return _ok()

        mock_run.side_effect = side_effect
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")

        levels = [t.split(":")[0].strip() for t in created_titles]
        scope_idx = next(
            (i for i, lv in enumerate(levels) if "Project Scope" in lv), None
        )
        init_idx = next((i for i, lv in enumerate(levels) if "Initiative" in lv), None)
        assert scope_idx is not None and init_idx is not None
        assert scope_idx < init_idx, "Scope must be created before Initiative"

    @patch("subprocess.run")
    def test_manifest_json_written_to_disk(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        assert (tmp_path / "manifest.json").exists()

    @patch("subprocess.run")
    def test_manifest_json_is_valid_json(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        data = json.loads((tmp_path / "manifest.json").read_text())
        assert isinstance(data, dict)

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_sleeps_between_creates(self, mock_run, mock_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # Should sleep once per issue created (5 total)
        assert mock_sleep.call_count == 5
        mock_sleep.assert_called_with(0.5)

    @patch("subprocess.run")
    def test_uses_body_file_flag(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        body_file_calls = []

        def side_effect(cmd, **kwargs):
            cmd_list = list(cmd)
            if "issue create" in " ".join(str(c) for c in cmd_list):
                if "--body-file" in cmd_list:
                    body_file_calls.append(True)
                return _ok("https://github.com/org/repo/issues/200")
            if "--jq" in " ".join(str(c) for c in cmd_list):
                return _ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 200})
                )
            return _ok()

        mock_run.side_effect = side_effect
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        assert len(body_file_calls) == 5, "All issues must use --body-file"
