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
from unittest.mock import patch

import pytest

from scripts import create_issues
from scripts.gh_helpers import PreflightError
from scripts.tests.conftest import (
    MINIMAL_HIERARCHY,
    MOCK_ISSUE_TYPES_RESPONSE,
    MOCK_PROJECT_FIELDS_RESPONSE,
    make_ok,
)

MULTI_INITIATIVE_HIERARCHY = {
    "scope": {
        "title": "Test Project",
        "description": "A test.",
        "priority": "P0",
        "size": "L",
        "blocking": [],
    },
    "initiative": {
        "title": "Core Initiative",
        "description": "The primary initiative.",
        "priority": "P0",
        "size": "L",
        "blocking": [],
    },
    "initiatives": [
        {
            "title": "Core Initiative",
            "description": "The primary initiative.",
            "priority": "P0",
            "size": "L",
            "blocking": [],
            "parent_ref": "Test Project",
        },
        {
            "title": "Expansion Initiative",
            "description": "The secondary initiative.",
            "priority": "P1",
            "size": "M",
            "blocking": [],
            "parent_ref": "Test Project",
        },
    ],
    "epics": [
        {
            "title": "First Epic",
            "description": "An epic.",
            "priority": "P0",
            "size": "M",
            "blocking": [],
            "parent_ref": "Core Initiative",
        },
        {
            "title": "Second Epic",
            "description": "Another epic.",
            "priority": "P1",
            "size": "M",
            "blocking": [],
            "parent_ref": "Expansion Initiative",
        },
    ],
    "stories": [],
    "tasks": [],
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
        """Verify generate_body doesn't inject extra TDD sentinels."""
        item = {"title": "X", "description": "", "priority": "P0", "size": "S"}
        body = create_issues.generate_body(item, "scope")
        template_count = create_issues._load_template("scope").count("TDD followed")
        assert body.count("TDD followed") == template_count

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
            assert "I Know I Am Done When" in body, (
                f"Level '{level}' body missing 'I Know I Am Done When'"
            )


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
                return make_ok()
            call_count["n"] += 1
            if call_count["n"] == 1:
                return make_ok(MOCK_ISSUE_TYPES_RESPONSE)
            return make_ok(MOCK_PROJECT_FIELDS_RESPONSE)

        return side_effect

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_returns_config_dict(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert isinstance(config, dict)

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_returns_project_id(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert config["project_id"] == "PVT_project_id"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_returns_all_five_issue_type_ids(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        for level in ("scope", "initiative", "epic", "story", "task"):
            assert level in config["issue_type_ids"], (
                f"issue_type_ids must have '{level}'"
            )

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_returns_all_three_field_ids(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        config = create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        for field in ("Priority", "Size", "Status"):
            assert field in config["field_ids"]

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_writes_manifest_config_json(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_run_side_effect(tmp_path)
        create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)
        assert (tmp_path / "manifest-config.json").exists()

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_raises_when_issue_types_missing(
        self, mock_run, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "auth" in joined:
                return make_ok()
            return make_ok(
                json.dumps({"data": {"organization": {"issueTypes": {"nodes": []}}}})
            )

        mock_run.side_effect = side_effect
        with pytest.raises(PreflightError):
            create_issues.preflight("kdtix-open", "kdtix-open/test-repo", 8)

    @patch("scripts.gh_helpers.subprocess.run")
    def test_preflight_raises_when_project_not_found(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "auth" in joined:
                return make_ok()
            call_count["n"] += 1
            if call_count["n"] == 1:
                return make_ok(MOCK_ISSUE_TYPES_RESPONSE)
            return make_ok(json.dumps({"data": {"organization": {"projectV2": None}}}))

        mock_run.side_effect = side_effect
        with pytest.raises(PreflightError):
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
                return make_ok(f"{base_url}/{n}")
            if "issues/" in joined and "--jq" in joined:
                n = issue_counter["n"]
                return make_ok(
                    json.dumps(
                        {
                            "nodeId": f"I_node_{n}",
                            "databaseId": n * 100,
                            "number": n,
                        }
                    )
                )
            return make_ok()

        return side_effect

    @patch("scripts.gh_helpers.subprocess.run")
    def test_creates_issues_for_all_levels(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # scope + initiative + 1 epic + 1 story + 1 task = 5
        assert len(manifest) == 5

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_creates_all_initiatives_in_ordered_manifest(
        self, mock_run, _mock_sleep, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()

        manifest = create_issues.create_all_issues(
            MULTI_INITIATIVE_HIERARCHY, {}, "org/repo"
        )

        initiative_titles = [
            record["title"]
            for record in manifest.values()
            if record["level"] == "initiative"
        ]
        assert initiative_titles == [
            "Core Initiative",
            "Expansion Initiative",
        ]
        assert len(manifest) == 5

    @patch("scripts.gh_helpers.subprocess.run")
    def test_manifest_has_required_fields(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        for key, record in manifest.items():
            for field in ("number", "nodeId", "databaseId", "level"):
                assert field in record, f"manifest['{key}'] missing '{field}'"

    @patch("scripts.gh_helpers.subprocess.run")
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
                return make_ok("https://github.com/org/repo/issues/101")
            if "--jq" in joined:
                return make_ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 101})
                )
            return make_ok()

        mock_run.side_effect = side_effect
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")

        levels = [t.split(":")[0].strip() for t in created_titles]
        scope_idx = next(
            (i for i, lv in enumerate(levels) if "Project Scope" in lv), None
        )
        init_idx = next((i for i, lv in enumerate(levels) if "Initiative" in lv), None)
        assert scope_idx is not None and init_idx is not None
        assert scope_idx < init_idx, "Scope must be created before Initiative"

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_creates_both_initiatives_before_epics(
        self, mock_run, _mock_sleep, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        created_titles: list[str] = []

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                try:
                    idx = list(cmd).index("--title")
                    created_titles.append(cmd[idx + 1])
                except (ValueError, IndexError):
                    pass
                return make_ok("https://github.com/org/repo/issues/101")
            if "--jq" in joined:
                return make_ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 101})
                )
            return make_ok()

        mock_run.side_effect = side_effect
        create_issues.create_all_issues(MULTI_INITIATIVE_HIERARCHY, {}, "org/repo")

        first_epic_idx = next(
            (i for i, title in enumerate(created_titles) if title.startswith("Epic:")),
            None,
        )
        initiative_indices = [
            i
            for i, title in enumerate(created_titles)
            if title.startswith("Initiative:")
        ]

        assert first_epic_idx is not None
        assert len(initiative_indices) == 2
        assert all(idx < first_epic_idx for idx in initiative_indices)

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_uses_legacy_initiative_alias_when_initiatives_list_is_empty(
        self, mock_run, _mock_sleep, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        created_titles: list[str] = []
        hierarchy = {**MINIMAL_HIERARCHY, "initiatives": []}

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                try:
                    idx = list(cmd).index("--title")
                    created_titles.append(cmd[idx + 1])
                except (ValueError, IndexError):
                    pass
                return make_ok("https://github.com/org/repo/issues/101")
            if "--jq" in joined:
                return make_ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 101})
                )
            return make_ok()

        mock_run.side_effect = side_effect
        manifest = create_issues.create_all_issues(hierarchy, {}, "org/repo")

        initiative_titles = [
            title for title in created_titles if title.startswith("Initiative:")
        ]
        assert initiative_titles == ["Initiative: Core Initiative"]
        assert len(manifest) == 5

    @patch("scripts.gh_helpers.subprocess.run")
    def test_manifest_json_written_to_disk(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        assert (tmp_path / "manifest.json").exists()

    @patch("scripts.gh_helpers.subprocess.run")
    def test_manifest_json_is_valid_json(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        data = json.loads((tmp_path / "manifest.json").read_text())
        assert isinstance(data, dict)

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_sleeps_between_creates(self, mock_run, mock_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # Should sleep once per issue created (5 total)
        assert mock_sleep.call_count == 5
        mock_sleep.assert_called_with(0.5)

    @patch("scripts.gh_helpers.subprocess.run")
    def test_uses_body_file_flag(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        body_file_calls = []

        def side_effect(cmd, **kwargs):
            cmd_list = list(cmd)
            if "issue create" in " ".join(str(c) for c in cmd_list):
                if "--body-file" in cmd_list:
                    body_file_calls.append(True)
                return make_ok("https://github.com/org/repo/issues/200")
            if "--jq" in " ".join(str(c) for c in cmd_list):
                return make_ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 200})
                )
            return make_ok()

        mock_run.side_effect = side_effect
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        assert len(body_file_calls) == 5, "All issues must use --body-file"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_manifest_uses_unique_keys(self, mock_run, tmp_path, monkeypatch):
        """Issue 5: manifest keys should be level-index, not title."""
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._mock_run_for_create()
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # Keys should be like scope-1, initiative-1, epic-1, etc.
        for key in manifest:
            assert "-" in key, f"Key '{key}' should be level-index format"
            parts = key.rsplit("-", 1)
            assert parts[1].isdigit(), f"Key '{key}' should end with a number"


# ---------------------------------------------------------------------------
# _get_issue_ids transient-404 retry
# ---------------------------------------------------------------------------


class TestGetIssueIdsRetry:
    """Regression tests for the read-after-write 404 retry in _get_issue_ids."""

    def _make_404_result(self) -> object:
        from unittest.mock import MagicMock

        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = "HTTP 404: Not Found"
        return m

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_retries_on_transient_404_and_succeeds(
        self, mock_run, mock_sleep, tmp_path, monkeypatch
    ):
        """_get_issue_ids must retry on 404 and succeed when GitHub catches up."""
        import json

        monkeypatch.chdir(tmp_path)
        call_count = {"n": 0}

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issues/" in joined and "--jq" in joined:
                call_count["n"] += 1
                if call_count["n"] < 3:
                    return self._make_404_result()
                return make_ok(
                    json.dumps({"nodeId": "N1", "databaseId": 9999, "number": 101})
                )
            if "issue create" in joined:
                return make_ok("https://github.com/org/repo/issues/101")
            return make_ok()

        mock_run.side_effect = side_effect
        manifest = create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        assert len(manifest) == 5
        # sleep was called for the 404 retries (at least twice — once per failure)
        retry_sleeps = [c for c in mock_sleep.call_args_list if c.args[0] != 0.5]
        assert len(retry_sleeps) >= 2

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_raises_after_max_wait_exceeded(
        self, mock_run, mock_sleep, tmp_path, monkeypatch
    ):
        """_get_issue_ids must raise GitHubAPIError when max wait is exceeded."""
        from scripts.gh_helpers import GitHubAPIError

        monkeypatch.chdir(tmp_path)

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                return make_ok("https://github.com/org/repo/issues/101")
            # Always 404
            return self._make_404_result()

        mock_run.side_effect = side_effect
        # Patch max-wait to 0 so we exhaust budget on first retry attempt
        with patch.object(create_issues, "_GET_ISSUE_IDS_MAX_WAIT", 0.0):
            with pytest.raises(GitHubAPIError):
                create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_no_extra_sleep_on_immediate_success(
        self, mock_run, mock_sleep, tmp_path, monkeypatch
    ):
        """When GitHub responds immediately, no retry sleep should occur."""
        monkeypatch.chdir(tmp_path)
        mock_run.side_effect = self._make_ok_create_side_effect()
        create_issues.create_all_issues(MINIMAL_HIERARCHY, {}, "org/repo")
        # Only the 0.5 s pacing sleeps should fire — no retry sleeps
        assert all(c.args[0] == 0.5 for c in mock_sleep.call_args_list)

    @staticmethod
    def _make_ok_create_side_effect():
        import json
        from unittest.mock import MagicMock

        counter = {"n": 100}

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "issue create" in joined:
                counter["n"] += 1
                m = MagicMock()
                m.returncode = 0
                m.stdout = f"https://github.com/org/repo/issues/{counter['n']}"
                m.stderr = ""
                return m
            if "issues/" in joined and "--jq" in joined:
                n = counter["n"]
                m = MagicMock()
                m.returncode = 0
                m.stdout = json.dumps(
                    {"nodeId": f"N{n}", "databaseId": n * 100, "number": n}
                )
                m.stderr = ""
                return m
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        return side_effect
