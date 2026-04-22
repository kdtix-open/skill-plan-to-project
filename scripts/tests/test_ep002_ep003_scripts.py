"""
Tests for set_relationships.py, set_project_fields.py,
compliance_check.py, and queue_order.py.
All gh CLI calls mocked.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts import (
    compliance_check,
    queue_order,
    set_project_fields,
    set_relationships,
)
from scripts.tests.conftest import (
    SAMPLE_CONFIG,
    SAMPLE_MANIFEST,
    TDD_DONE_WHEN,
    make_ok,
)

# ===========================================================================
# set_relationships.py
# ===========================================================================


class TestSetSubIssues:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_calls_sub_issue_api_for_each_child(self, mock_run):
        mock_run.return_value = make_ok("{}")
        set_relationships.set_sub_issues(SAMPLE_MANIFEST, "org/repo")
        # Expect POST calls for each parent-child pair (4 children)
        post_calls = [c for c in mock_run.call_args_list if "POST" in str(c)]
        assert len(post_calls) == 4

    @patch("scripts.gh_helpers.subprocess.run")
    def test_uses_database_id_not_node_id(self, mock_run):
        mock_run.return_value = make_ok("{}")
        set_relationships.set_sub_issues(SAMPLE_MANIFEST, "org/repo")
        for call in mock_run.call_args_list:
            args = call[0][0]
            cmd_str = " ".join(str(a) for a in args)
            if "sub_issues" in cmd_str:
                assert "sub_issue_id=" in cmd_str
                # Must use -F flag (typed integer) not -f
                assert "-F" in args

    @patch("scripts.gh_helpers.subprocess.run")
    def test_uses_capital_f_flag_for_sub_issue_id(self, mock_run):
        mock_run.return_value = make_ok("{}")
        set_relationships.set_sub_issues(SAMPLE_MANIFEST, "org/repo")
        for call in mock_run.call_args_list:
            args = list(call[0][0])
            if "sub_issues" in " ".join(str(a) for a in args):
                # -F must appear before sub_issue_id=
                try:
                    f_idx = args.index("-F")
                    assert "sub_issue_id" in str(args[f_idx + 1])
                except (ValueError, IndexError):
                    pytest.fail("-F flag not found before sub_issue_id")


class TestSetBlockingLabels:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_adds_blocks_label_to_blocker(self, mock_run):
        mock_run.side_effect = self._subprocess_run_for_blocking_labels()
        set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")
        label_calls = [
            c
            for c in mock_run.call_args_list
            if "blocks" in str(c) and "add-label" in str(c)
        ]
        assert len(label_calls) >= 1

    @patch("scripts.gh_helpers.subprocess.run")
    def test_adds_blocked_label_to_blocked_issue(self, mock_run):
        mock_run.side_effect = self._subprocess_run_for_blocking_labels()
        set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")
        blocked_calls = [
            c
            for c in mock_run.call_args_list
            if "blocked" in str(c) and "add-label" in str(c)
        ]
        assert len(blocked_calls) >= 1

    def test_creates_native_blocked_by_relationship(self):
        calls: list[list[str]] = []

        def run_gh_side_effect(cmd, **kwargs):
            calls.append(cmd)
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=(
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| None | No blocking dependencies | N/A |\n"
                ),
            ),
            patch("scripts.set_relationships.update_issue_body"),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        relationship_calls = [
            cmd
            for cmd in calls
            if any("/dependencies/blocked_by" in str(part) for part in cmd)
            and "--method" in cmd
        ]
        assert len(relationship_calls) == 1
        call = relationship_calls[0]
        # story-1 (#4) has blocking=["Implement tokenizer"], so #4 is the blocker
        # and task-1 (#5) is the blocked issue.  The native relationship is POSTed
        # against the blocked issue (#5), referencing the blocker's databaseId.
        assert "/repos/org/repo/issues/5/dependencies/blocked_by" in " ".join(call)
        assert "-F" in call
        assert "issue_id=10004" in call

    def test_creates_missing_labels_before_applying(self):
        calls: list[list[str]] = []

        def run_gh_side_effect(cmd, **kwargs):
            calls.append(cmd)
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok("[]")
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=(
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| None | No blocking dependencies | N/A |\n"
                ),
            ),
            patch("scripts.set_relationships.update_issue_body"),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        create_calls = [
            " ".join(str(part) for part in cmd)
            for cmd in calls
            if cmd[:3] == ["gh", "label", "create"]
        ]
        assert any("gh label create blocks" in call for call in create_calls)
        assert any("gh label create blocked" in call for call in create_calls)

    def test_writes_single_blocker_line_and_dependency_table(self):
        updated_bodies: list[str] = []

        with (
            patch(
                "scripts.set_relationships.run_gh",
                side_effect=self._run_gh_for_blocking_labels(),
            ),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=(
                    "# User Story: Build the widget\n\n"
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| None | No blocking dependencies | N/A |\n"
                ),
            ),
            patch(
                "scripts.set_relationships.update_issue_body",
                side_effect=lambda repo, number, body: updated_bodies.append(body),
            ),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        assert len(updated_bodies) == 1
        body = updated_bodies[0]
        # story-1 (#4) blocks task-1 (#5), so the blocked issue (#5) body is
        # updated to say "Blocked by: #4".
        assert "Blocked by: #4" in body
        assert body.count("Blocked by:") == 1
        assert "| #4 | Build the widget | Open |" in body
        assert "| None | No blocking dependencies | N/A |" not in body

    def test_handles_multiple_blockers_without_duplicate_rows(self):
        # Two separate issues each declare "Blocks: Queue orchestration".
        # Correct semantics: Token-lease (#147) and Worker-drain (#160) are the
        # blockers; Queue-orchestration (#168) is the blocked issue.
        manifest = {
            "blocker-1": {
                "number": 147,
                "nodeId": "I_node_147",
                "databaseId": 14700,
                "level": "task",
                "title": "Token lease",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["Queue orchestration"],
            },
            "blocker-2": {
                "number": 160,
                "nodeId": "I_node_160",
                "databaseId": 16000,
                "level": "task",
                "title": "Worker drain",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["Queue orchestration"],
            },
            "blocked-story": {
                "number": 168,
                "nodeId": "I_node_168",
                "databaseId": 16800,
                "level": "story",
                "title": "Queue orchestration",
                "parent_ref": None,
                "priority": "P1",
                "size": "M",
                "blocking": [],
            },
        }
        updated_bodies: list[str] = []
        relationship_posts: list[str] = []

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            if "/dependencies/blocked_by" in joined and "--method" in joined:
                relationship_posts.append(joined)
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=(
                    "# User Story: Queue orchestration\n\n"
                    "Blocked by: #147\n\n"
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| #147 | Token lease | Open |\n"
                ),
            ),
            patch(
                "scripts.set_relationships.update_issue_body",
                side_effect=lambda repo, number, body: updated_bodies.append(body),
            ),
        ):
            set_relationships.set_blocking_labels(manifest, "org/repo")

        assert len(relationship_posts) == 2
        assert len(updated_bodies) == 1
        body = updated_bodies[0]
        assert "Blocked by: #147, #160" in body
        assert body.count("Blocked by:") == 1
        assert body.count("| #147 | Token lease | Open |") == 1
        assert body.count("| #160 | Worker drain | Open |") == 1

    def test_numeric_issue_ref_uses_github_issue_number_lookup(self):
        manifest = {
            "blocker-task": {
                "number": 41,
                "nodeId": "I_node_41",
                "databaseId": 4100,
                "level": "task",
                "title": "Parser handoff",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["#182"],
            },
            "local-lookalike": {
                "number": 77,
                "nodeId": "I_node_77",
                "databaseId": 7700,
                "level": "story",
                "title": "Sprint 182 planning",
                "parent_ref": None,
                "priority": "P1",
                "size": "M",
                "blocking": [],
            },
        }
        updated_bodies: list[str] = []
        relationship_posts: list[str] = []
        labeled: dict[int, list[str]] = {}

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if joined.endswith("/repos/org/repo/issues/182"):
                return make_ok(
                    json.dumps(
                        {
                            "number": 182,
                            "title": "Queue orchestration",
                            "id": 18200,
                            "node_id": "I_node_182",
                        }
                    )
                )
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            if "/dependencies/blocked_by" in joined and "--method" in joined:
                relationship_posts.append(joined)
            if "add-label" in joined:
                args = list(cmd)
                num = int(args[args.index("edit") + 1])
                lbl = args[args.index("--add-label") + 1]
                labeled.setdefault(num, []).append(lbl)
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=(
                    "# Story: Queue orchestration\n\n"
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| None | No blocking dependencies | N/A |\n"
                ),
            ),
            patch(
                "scripts.set_relationships.update_issue_body",
                side_effect=lambda repo, number, body: updated_bodies.append(body),
            ),
        ):
            set_relationships.set_blocking_labels(manifest, "org/repo")

        assert len(relationship_posts) == 1
        assert (
            "/repos/org/repo/issues/182/dependencies/blocked_by"
            in relationship_posts[0]
        )
        assert "issue_id=4100" in relationship_posts[0]
        assert "blocks" in labeled.get(41, [])
        assert "blocked" in labeled.get(182, [])
        assert "blocked" not in labeled.get(77, [])
        assert len(updated_bodies) == 1
        assert "Blocked by: #41" in updated_bodies[0]
        assert "| #41 | Parser handoff | Open |" in updated_bodies[0]

    def test_idempotent_rerun_skips_existing_relationship_and_body_duplicates(self):
        updated_bodies: list[str] = []
        calls: list[str] = []
        # story-1 (#4) blocks task-1 (#5) — existing body is already correct.
        existing_body = (
            "# Task: Implement tokenizer\n\n"
            "Blocked by: #4\n\n"
            "### Dependencies\n\n"
            "| Ticket | Description | Status |\n"
            "|--------|-------------|--------|\n"
            "| #4 | Build the widget | Open |\n"
        )

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            calls.append(joined)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(
                    json.dumps(
                        {
                            "dependencies": [
                                {
                                    "issue": {
                                        "number": 4,
                                        "id": 10004,
                                    }
                                }
                            ]
                        }
                    )
                )
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch(
                "scripts.set_relationships.get_issue_body",
                return_value=existing_body,
            ),
            patch(
                "scripts.set_relationships.update_issue_body",
                side_effect=lambda repo, number, body: updated_bodies.append(body),
            ),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        assert not any(
            "/dependencies/blocked_by" in call and "--method POST" in call
            for call in calls
        )
        assert len(updated_bodies) == 1
        body = updated_bodies[0]
        assert body.count("Blocked by: #4") == 1
        assert body.count("| #4 | Build the widget | Open |") == 1

    def test_blocker_direction_blocker_gets_blocks_label(self):
        """Regression: issue with blocking=[...] must get 'blocks', not 'blocked'."""
        labeled: dict[int, list[str]] = {}

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            if "add-label" in joined:
                # Extract the issue number and label from the command
                args = list(cmd)
                try:
                    num = int(args[args.index("edit") + 1])
                    lbl = args[args.index("--add-label") + 1]
                    labeled.setdefault(num, []).append(lbl)
                except (ValueError, IndexError):
                    pass
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch("scripts.set_relationships.get_issue_body", return_value=""),
            patch("scripts.set_relationships.update_issue_body"),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        # story-1 (#4) has blocking=["Implement tokenizer"], so #4 is the blocker
        assert "blocks" in labeled.get(4, []), "#4 (blocker) must get 'blocks' label"
        assert "blocked" not in labeled.get(4, []), "#4 must NOT get 'blocked' label"

    def test_blocker_direction_blocked_gets_blocked_label(self):
        """Regression: the referenced issue must get 'blocked', not 'blocks'."""
        labeled: dict[int, list[str]] = {}

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            if "add-label" in joined:
                args = list(cmd)
                try:
                    num = int(args[args.index("edit") + 1])
                    lbl = args[args.index("--add-label") + 1]
                    labeled.setdefault(num, []).append(lbl)
                except (ValueError, IndexError):
                    pass
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch("scripts.set_relationships.get_issue_body", return_value=""),
            patch("scripts.set_relationships.update_issue_body"),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        # task-1 (#5, "Implement tokenizer") is referenced by story-1's blocking list,
        # so #5 is the blocked issue.
        assert "blocked" in labeled.get(5, []), "#5 (blocked) must get 'blocked' label"
        assert "blocks" not in labeled.get(5, []), "#5 must NOT get 'blocks' label"

    def test_native_relationship_posted_against_blocked_not_blocker(self):
        """Regression: POST /blocked_by goes to the *blocked* issue endpoint."""
        calls: list[list[str]] = []

        def run_gh_side_effect(cmd, **kwargs):
            calls.append(cmd)
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            return make_ok("{}")

        with (
            patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect),
            patch("scripts.set_relationships.get_issue_body", return_value=""),
            patch("scripts.set_relationships.update_issue_body"),
        ):
            set_relationships.set_blocking_labels(SAMPLE_MANIFEST, "org/repo")

        post_calls = [
            " ".join(str(p) for p in cmd)
            for cmd in calls
            if "/dependencies/blocked_by" in " ".join(str(p) for p in cmd)
            and "--method" in cmd
        ]
        assert len(post_calls) == 1
        # The POST must target the blocked issue (#5), not the blocker (#4)
        assert "issues/5/dependencies/blocked_by" in post_calls[0]
        assert "issues/4/dependencies/blocked_by" not in post_calls[0]

    @patch("scripts.gh_helpers.subprocess.run")
    def test_set_blocking_labels_warns_on_unresolvable_ref(self, mock_run, capsys):
        mock_run.return_value = make_ok("[]")
        manifest_bad_ref = {
            "story-1": {
                "number": 10,
                "nodeId": "N10",
                "databaseId": 1000,
                "level": "story",
                "title": "Blocker",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["NonExistentTarget"],
            }
        }

        set_relationships.set_blocking_labels(manifest_bad_ref, "org/repo")

        captured = capsys.readouterr()
        assert "Blocking ref 'NonExistentTarget' not found in manifest" in captured.err

    def test_numeric_issue_ref_warns_when_repo_issue_not_found(self, capsys):
        manifest = {
            "story-1": {
                "number": 10,
                "nodeId": "N10",
                "databaseId": 1000,
                "level": "story",
                "title": "Blocker",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["#999"],
            }
        }

        def run_gh_side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok("[]")
            if joined.endswith("/repos/org/repo/issues/999"):
                raise set_relationships.GitHubAPIError(cmd, 1, "404 Not Found")
            return make_ok("{}")

        with patch("scripts.set_relationships.run_gh", side_effect=run_gh_side_effect):
            set_relationships.set_blocking_labels(manifest, "org/repo")

        captured = capsys.readouterr()
        assert "Blocking ref '#999' not found in manifest or repo" in captured.err

    @staticmethod
    def _run_gh_for_blocking_labels():
        def _side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            return make_ok("{}")

        return _side_effect

    @staticmethod
    def _subprocess_run_for_blocking_labels():
        def _side_effect(cmd, **kwargs):
            joined = " ".join(str(part) for part in cmd)
            if "label list" in joined:
                return make_ok(json.dumps([{"name": "blocks"}, {"name": "blocked"}]))
            if "issue view" in joined and "--json body" in joined:
                return make_ok(
                    "# User Story: Build the widget\n\n"
                    "### Dependencies\n\n"
                    "| Ticket | Description | Status |\n"
                    "|--------|-------------|--------|\n"
                    "| None | No blocking dependencies | N/A |\n"
                )
            if "/dependencies/blocked_by" in joined and "--method" not in joined:
                return make_ok(json.dumps({"dependencies": []}))
            return make_ok("{}")

        return _side_effect


# ===========================================================================
# set_project_fields.py
# ===========================================================================


class TestSetProjectFields:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_calls_graphql_for_each_issue(self, mock_run):
        mock_run.return_value = make_ok(
            json.dumps(
                {"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_test"}}}}
            )
        )
        set_project_fields.set_project_fields(SAMPLE_MANIFEST, SAMPLE_CONFIG)
        graphql_calls = [c for c in mock_run.call_args_list if "graphql" in str(c)]
        # At least one graphql call per issue
        assert len(graphql_calls) >= len(SAMPLE_MANIFEST)

    @patch("scripts.gh_helpers.subprocess.run")
    def test_sets_priority_field(self, mock_run):
        mock_run.return_value = make_ok(
            json.dumps(
                {
                    "data": {
                        "addProjectV2ItemById": {"item": {"id": "PVTI_x"}},
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": "PVTI_x"}
                        },
                        "updateIssue": {
                            "issue": {"id": "I_x", "issueType": {"name": "Epic"}}
                        },
                    }
                }
            )
        )
        set_project_fields.set_project_fields(SAMPLE_MANIFEST, SAMPLE_CONFIG)
        all_calls = " ".join(str(c) for c in mock_run.call_args_list)
        assert "updateProjectV2ItemFieldValue" in all_calls

    @patch("scripts.gh_helpers.subprocess.run")
    def test_sets_issue_types(self, mock_run):
        mock_run.return_value = make_ok(
            json.dumps(
                {
                    "data": {
                        "addProjectV2ItemById": {"item": {"id": "PVTI_x"}},
                        "updateIssue": {
                            "issue": {"id": "I_x", "issueType": {"name": "Epic"}}
                        },
                    }
                }
            )
        )
        set_project_fields.set_project_fields(SAMPLE_MANIFEST, SAMPLE_CONFIG)
        all_calls = " ".join(str(c) for c in mock_run.call_args_list)
        assert "updateIssue" in all_calls

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_sleeps_between_mutations(self, mock_run, mock_sleep):
        mock_run.return_value = make_ok(
            json.dumps({"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_x"}}}})
        )
        set_project_fields.set_project_fields(SAMPLE_MANIFEST, SAMPLE_CONFIG)
        assert mock_sleep.call_count >= len(SAMPLE_MANIFEST)


# ===========================================================================
# compliance_check.py
# ===========================================================================


class TestCheckIssue:
    def test_detects_missing_tdd_sentinel(self):
        body = "## I Know I Am Done When\n\n- [ ] Something done\n"
        gaps = compliance_check.check_issue(1, "Build X", body, "story")
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-1" in p0_rules

    def test_no_p0_1_when_tdd_present(self):
        body = (
            "## I Know I Am Done When\n\n"
            "- [ ] TDD followed: failing test written BEFORE implementation "
            "(Red phase confirmed before writing any production code)\n"
        )
        gaps = compliance_check.check_issue(1, "Read docs", body, "story")
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-1" not in p0_rules

    def test_detects_missing_security_on_mutation_issue(self):
        body = TDD_DONE_WHEN
        gaps = compliance_check.check_issue(
            1, "Build the create endpoint", body, "story"
        )
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-2" in p0_rules

    def test_no_security_gap_for_read_only_issue(self):
        body = TDD_DONE_WHEN
        gaps = compliance_check.check_issue(1, "Read the docs", body, "story")
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-2" not in p0_rules

    def test_detects_p0_3_missing_deps_on_blocked(self):
        body = TDD_DONE_WHEN
        gaps = compliance_check.check_issue(
            1, "Some story", body, "story", has_blocked_label=True
        )
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-3" in p0_rules

    def test_detects_p1_missing_assumptions(self):
        body = TDD_DONE_WHEN
        gaps = compliance_check.check_issue(1, "Build X", body, "story")
        p1_rules = [g["rule"] for g in gaps if g["severity"] == "P1"]
        assert "P1-1" in p1_rules

    def test_detects_p1_missing_moscow(self):
        body = "## Assumptions\n- something\n" + TDD_DONE_WHEN
        gaps = compliance_check.check_issue(1, "Build X", body, "story")
        p1_rules = [g["rule"] for g in gaps if g["severity"] == "P1"]
        assert "P1-2" in p1_rules

    def test_clean_body_has_no_p0_gaps(self):
        body = (
            "## Assumptions\n- something\n\n"
            "## MoSCoW Classification\n| Must Have | X |\n\n"
            "## I Know I Am Done When\n\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "### Security/Compliance\n- [ ] Input validated\n\n"
            "### Dependencies\n| None |\n\n"
            "### Subtasks Needed\n| 1 | task | 1 | No |\n"
        )
        gaps = compliance_check.check_issue(
            1, "Read the docs only", body, "story", has_blocked_label=True
        )
        p0_gaps = [g for g in gaps if g["severity"] == "P0"]
        assert len(p0_gaps) == 0


class TestP0_4PlaceholderScanner:
    """FR #34 Stage 1: detect unreplaced template [PLACEHOLDER] strings."""

    def test_detects_uppercase_placeholder_criterion(self):
        body = "## Success Criteria\n\n- [ ] [CRITERION 1]\n- [ ] [CRITERION 2]\n"
        gaps = compliance_check.check_issue(1, "Test", body, "story")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 1
        assert "[CRITERION 1]" in p0_4[0]["placeholders"]
        assert "[CRITERION 2]" in p0_4[0]["placeholders"]

    def test_detects_item_placeholder(self):
        body = "## Out of Scope\n\n- [ITEM 1]\n- [ITEM 2]\n"
        gaps = compliance_check.check_issue(1, "Test", body, "story")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 1
        assert "[ITEM 1]" in p0_4[0]["placeholders"]

    def test_detects_descriptive_placeholder(self):
        body = (
            "## Business Problem & Current State\n\n"
            "[Describe the problem being solved and why the current approach is insufficient]\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 1
        placeholders = p0_4[0]["placeholders"]
        assert any("Describe the problem" in p for p in placeholders)

    def test_detects_project_specific_criterion_placeholder(self):
        body = (
            "## I Know I Am Done When\n\n"
            "- [ ] [PROJECT-SPECIFIC CRITERION]\n"
            "- [ ] TDD followed: failing test written BEFORE implementation\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 1

    def test_no_false_positive_on_checkbox(self):
        body = "## Done When\n- [ ] Task 1\n- [x] Task 2\n- [X] Task 3\n"
        gaps = compliance_check.check_issue(1, "Test", body, "story")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 0, f"False positive: {p0_4}"

    def test_no_false_positive_on_clean_body(self):
        # A body with real content + no placeholders should not trip P0-4
        body = (
            "## Assumptions\n"
            "- We assume the operator has gh CLI configured\n\n"
            "## MoSCoW Classification\n"
            "| Priority | Item |\n"
            "|----------|------|\n"
            "| Must Have | Label-set creation |\n\n"
            "## I Know I Am Done When\n\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "### Security/Compliance\n- [ ] Input validated\n"
        )
        gaps = compliance_check.check_issue(1, "Story", body, "story")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 0, f"False positive on clean body: {p0_4}"

    def test_placeholder_gap_description_includes_count(self):
        body = (
            "[CRITERION 1]\n[CRITERION 2]\n[ITEM 1]\n"
            "[ASSUMPTION 1]\n[ASSUMPTION 2]\n[DESCRIPTION]\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "story")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert len(p0_4) == 1
        # 6 distinct placeholders → description should say "6 unreplaced"
        assert "6 unreplaced" in p0_4[0]["description"]


class TestPlaceholderGate:
    """FR #34 Stage 1: --allow-placeholders gate for run_compliance_check."""

    def test_gate_fails_when_placeholders_present_and_not_allowed(self, mocker, tmp_path):
        # Mock the GH API calls
        mocker.patch("scripts.compliance_check.get_issue_body", return_value="[CRITERION 1]")
        mocker.patch("scripts.compliance_check.get_issue_labels", return_value=[])
        mocker.patch("scripts.compliance_check.update_issue_body")
        manifest = {
            "Test Scope": {"number": 1, "level": "scope", "title": "Test Scope"},
        }
        report = compliance_check.run_compliance_check(
            manifest, "owner/repo", output_dir=tmp_path, allow_placeholders=False
        )
        assert report["placeholder_gate"] == "failed"
        assert report["summary"]["p0_placeholders"] >= 1

    def test_gate_passes_when_placeholders_allowed(self, mocker, tmp_path):
        mocker.patch("scripts.compliance_check.get_issue_body", return_value="[CRITERION 1]")
        mocker.patch("scripts.compliance_check.get_issue_labels", return_value=[])
        mocker.patch("scripts.compliance_check.update_issue_body")
        manifest = {
            "Test Scope": {"number": 1, "level": "scope", "title": "Test Scope"},
        }
        report = compliance_check.run_compliance_check(
            manifest, "owner/repo", output_dir=tmp_path, allow_placeholders=True
        )
        assert report["placeholder_gate"] == "passed"

    def test_gate_passes_when_no_placeholders_present(self, mocker, tmp_path):
        clean_body = (
            "## Assumptions\n- Assumption\n\n"
            "## MoSCoW Classification\n| Must Have | X |\n\n"
            "## I Know I Am Done When\n\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "### Security/Compliance\n- [ ] OK\n"
        )
        mocker.patch("scripts.compliance_check.get_issue_body", return_value=clean_body)
        mocker.patch("scripts.compliance_check.get_issue_labels", return_value=[])
        mocker.patch("scripts.compliance_check.update_issue_body")
        manifest = {
            "Test Story": {"number": 1, "level": "story", "title": "Read the docs"},
        }
        report = compliance_check.run_compliance_check(
            manifest, "owner/repo", output_dir=tmp_path, allow_placeholders=False
        )
        assert report["placeholder_gate"] == "passed"
        assert report["summary"]["p0_placeholders"] == 0


class TestAutofixBody:
    def test_autofix_injects_tdd_after_done_when(self):
        body = "## I Know I Am Done When\n\n- [ ] Something\n"
        gaps = [{"severity": "P0", "rule": "P0-1", "fixed": False}]
        fixed = compliance_check.autofix_body(body, gaps)
        assert "TDD followed" in fixed

    def test_autofix_tdd_marks_gap_as_fixed(self):
        body = "## I Know I Am Done When\n\n- [ ] Something\n"
        gaps = [{"severity": "P0", "rule": "P0-1", "fixed": False}]
        compliance_check.autofix_body(body, gaps)
        assert gaps[0]["fixed"] is True

    def test_autofix_security_appends_to_body(self):
        body = "Some body content\n"
        gaps = [{"severity": "P0", "rule": "P0-2", "fixed": False}]
        fixed = compliance_check.autofix_body(body, gaps)
        assert "Security/Compliance" in fixed
        assert "Some body content" in fixed  # preserve existing

    def test_autofix_deps_appends_to_body(self):
        body = "Some body content\n"
        gaps = [{"severity": "P0", "rule": "P0-3", "fixed": False}]
        fixed = compliance_check.autofix_body(body, gaps)
        assert "Dependencies" in fixed
        assert "Some body content" in fixed  # preserve existing

    def test_autofix_does_not_modify_p1_gaps(self):
        body = "Some body content\n"
        gaps = [{"severity": "P1", "rule": "P1-1", "fixed": False}]
        fixed = compliance_check.autofix_body(body, gaps)
        assert fixed == body  # unchanged


# ===========================================================================
# queue_order.py
# ===========================================================================


class TestComputeQueueOrder:
    def test_eligible_story_included(self):
        statuses = {4: "Backlog", 3: "In Progress"}
        labels_map = {4: []}
        ordered = queue_order.compute_queue_order(
            SAMPLE_MANIFEST,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        numbers = [r["number"] for r in ordered]
        assert 4 in numbers

    def test_blocked_story_excluded(self):
        statuses = {4: "Backlog", 3: "In Progress"}
        labels_map = {4: ["blocked"]}
        ordered = queue_order.compute_queue_order(
            SAMPLE_MANIFEST,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        numbers = [r["number"] for r in ordered]
        assert 4 not in numbers

    def test_p0_before_p1_in_order(self):
        manifest = {
            "epic-1": {
                "number": 10,
                "nodeId": "N10",
                "databaseId": 1010,
                "level": "epic",
                "title": "Epic A",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 11,
                "nodeId": "N11",
                "databaseId": 1011,
                "level": "story",
                "title": "Story P0",
                "parent_ref": "Epic A",
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-2": {
                "number": 12,
                "nodeId": "N12",
                "databaseId": 1012,
                "level": "story",
                "title": "Story P1",
                "parent_ref": "Epic A",
                "priority": "P1",
                "size": "M",
                "blocking": [],
            },
        }
        statuses = {11: "Backlog", 12: "Backlog", 10: "In Progress"}
        labels_map = {11: [], 12: []}
        ordered = queue_order.compute_queue_order(
            manifest,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        titles = [r["title"] for r in ordered]
        assert titles.index("Story P0") < titles.index("Story P1")

    def test_smaller_size_before_larger_same_priority(self):
        manifest = {
            "epic-1": {
                "number": 20,
                "nodeId": "N20",
                "databaseId": 2000,
                "level": "epic",
                "title": "Epic",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 21,
                "nodeId": "N21",
                "databaseId": 2001,
                "level": "story",
                "title": "Story Small",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "S",
                "blocking": [],
            },
            "story-2": {
                "number": 22,
                "nodeId": "N22",
                "databaseId": 2002,
                "level": "story",
                "title": "Story Large",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "L",
                "blocking": [],
            },
        }
        statuses = {21: "Backlog", 22: "Backlog", 20: "In Progress"}
        labels_map = {21: [], 22: []}
        ordered = queue_order.compute_queue_order(
            manifest,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        titles = [r["title"] for r in ordered]
        assert titles.index("Story Small") < titles.index("Story Large")

    def test_lower_issue_number_tiebreaker(self):
        manifest = {
            "epic-1": {
                "number": 30,
                "nodeId": "N30",
                "databaseId": 3000,
                "level": "epic",
                "title": "Epic",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 31,
                "nodeId": "N31",
                "databaseId": 3001,
                "level": "story",
                "title": "Story A",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-2": {
                "number": 35,
                "nodeId": "N35",
                "databaseId": 3002,
                "level": "story",
                "title": "Story B",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
        }
        statuses = {31: "Backlog", 35: "Backlog", 30: "In Progress"}
        labels_map = {31: [], 35: []}
        ordered = queue_order.compute_queue_order(
            manifest,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        assert ordered[0]["number"] == 31  # lower # first

    def test_only_stories_in_output(self):
        statuses = {4: "Backlog", 3: "In Progress"}
        labels_map = {4: []}
        ordered = queue_order.compute_queue_order(
            SAMPLE_MANIFEST,
            "org/repo",
            statuses=statuses,
            labels_map=labels_map,
        )
        for r in ordered:
            assert r["level"] == "story"

    def test_empty_manifest_returns_empty_list(self):
        ordered = queue_order.compute_queue_order(
            {},
            "org/repo",
            statuses={},
            labels_map={},
        )
        assert ordered == []

    def test_writes_queue_order_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(
            queue_order,
            "compute_queue_order",
            return_value=[SAMPLE_MANIFEST["story-1"]],
        ):
            queue_order.run_queue_order(SAMPLE_MANIFEST, "org/repo")
        assert (tmp_path / "queue-order.json").exists()


# ===========================================================================
# Coverage: CLI main() entry points and run_ wrappers
# ===========================================================================


class TestRunComplianceCheck:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_run_compliance_check_writes_report(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        body_with_tdd = (
            "## Assumptions\n- a\n## MoSCoW Classification\n| M | x |\n"
            "## I Know I Am Done When\nTDD followed: failing test written "
            "BEFORE implementation\n### Subtasks Needed\n| 1 | t | 1 | No |\n"
            "### Release Value\nv\n### Why This Matters\nw\n### TL;DR\nt\n"
        )

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "--json" in joined and "labels" in joined:
                return make_ok("[]")
            return make_ok(body_with_tdd)

        mock_run.side_effect = side_effect
        compliance_check.run_compliance_check(SAMPLE_MANIFEST, "org/repo")
        assert (tmp_path / "compliance-report.json").exists()

    @patch("scripts.gh_helpers.subprocess.run")
    def test_run_compliance_check_returns_report_dict(
        self, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "labels" in joined:
                return make_ok("[]")
            return make_ok(
                '"clean body with TDD followed: failing test '
                'written BEFORE implementation"'
            )

        mock_run.side_effect = side_effect
        report = compliance_check.run_compliance_check(SAMPLE_MANIFEST, "org/repo")
        assert "summary" in report
        assert "issues" in report


class TestSetRelationshipsCLI:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_set_sub_issues_skips_missing_parent(self, mock_run):
        mock_run.return_value = make_ok("{}")
        manifest_no_parent = {
            "story-1": {
                "number": 99,
                "nodeId": "N99",
                "databaseId": 9999,
                "level": "story",
                "title": "Orphan",
                "parent_ref": "NonExistentParent",
                "priority": "P1",
                "size": "M",
                "blocking": [],
            }
        }
        # Should not crash, just warn
        set_relationships.set_sub_issues(manifest_no_parent, "org/repo")

    @patch("scripts.gh_helpers.subprocess.run")
    def test_set_blocking_labels_skips_unresolvable_ref(self, mock_run):
        mock_run.return_value = make_ok("[]")
        manifest_bad_ref = {
            "story-1": {
                "number": 10,
                "nodeId": "N10",
                "databaseId": 1000,
                "level": "story",
                "title": "Blocker",
                "parent_ref": None,
                "priority": "P0",
                "size": "S",
                "blocking": ["NonExistentTarget"],
            }
        }
        # Should not crash, just warn
        set_relationships.set_blocking_labels(manifest_bad_ref, "org/repo")


class TestPriorityKey:
    def test_p0_sorts_before_p1(self):
        r0 = {"priority": "P0", "size": "M", "number": 5}
        r1 = {"priority": "P1", "size": "M", "number": 4}
        assert queue_order._priority_key(r0) < queue_order._priority_key(r1)

    def test_unknown_priority_treated_as_p1(self):
        r = {"priority": "PX", "size": "M", "number": 1}
        key = queue_order._priority_key(r)
        assert key[0] == 1  # P1 default

    def test_unknown_size_treated_as_m(self):
        r = {"priority": "P0", "size": "ZZ", "number": 1}
        key = queue_order._priority_key(r)
        assert key[1] == 2  # M default


class TestSetProjectFieldsIssueTypesOnly:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_issue_types_only_skips_field_mutations(self, mock_run):
        mock_run.return_value = make_ok(
            json.dumps(
                {
                    "data": {
                        "addProjectV2ItemById": {"item": {"id": "PVTI_x"}},
                        "updateIssue": {
                            "issue": {"id": "I_x", "issueType": {"name": "Epic"}}
                        },
                    }
                }
            )
        )
        set_project_fields.set_project_fields(
            SAMPLE_MANIFEST, SAMPLE_CONFIG, issue_types_only=True
        )
        all_calls = " ".join(str(c) for c in mock_run.call_args_list)
        # updateProjectV2ItemFieldValue should NOT be called
        assert "updateProjectV2ItemFieldValue" not in all_calls


class TestQueueOrderWritesJson:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_run_queue_order_writes_file(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        test_manifest = {
            "epic-1": {
                "number": 50,
                "nodeId": "N50",
                "databaseId": 5000,
                "level": "epic",
                "title": "Epic",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 51,
                "nodeId": "N51",
                "databaseId": 5001,
                "level": "story",
                "title": "Story One",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "S",
                "blocking": [],
            },
        }

        def side_effect(cmd, **kwargs):
            joined = " ".join(str(c) for c in cmd)
            if "labels" in joined:
                return make_ok("[]")
            if "projectItems" in joined:
                return make_ok('"Backlog"')
            return make_ok('"In Progress"')

        mock_run.side_effect = side_effect
        with patch.object(
            queue_order,
            "compute_queue_order",
            return_value=[test_manifest["story-1"]],
        ):
            queue_order.run_queue_order(test_manifest, "org/repo")
        assert (tmp_path / "queue-order.json").exists()
        data = json.loads((tmp_path / "queue-order.json").read_text())
        assert isinstance(data, list)


class TestComplianceCheckHelpers:
    def test_check_issue_scope_no_security_gap(self):
        """Scope-level issues don't require Security/Compliance section."""
        body = TDD_DONE_WHEN
        gaps = compliance_check.check_issue(1, "Build something", body, "scope")
        p0_rules = [g["rule"] for g in gaps if g["severity"] == "P0"]
        assert "P0-2" not in p0_rules  # scope exempt from security requirement

    def test_autofix_creates_done_when_if_missing(self):
        body = "Some content with no done-when section\n"
        gaps = [{"severity": "P0", "rule": "P0-1", "fixed": False}]
        fixed = compliance_check.autofix_body(body, gaps)
        assert "I Know I Am Done When" in fixed
        assert "TDD followed" in fixed


# More CLI path tests to hit 80% threshold
class TestSetRelationshipsCLIMain:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_main_with_manifest_file(self, mock_run, tmp_path):
        mock_run.return_value = make_ok("{}")
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(SAMPLE_MANIFEST), encoding="utf-8")
        with patch(
            "sys.argv",
            [
                "set_relationships.py",
                "--manifest",
                str(manifest_file),
                "--repo",
                "org/repo",
            ],
        ):
            set_relationships.main()

    @patch("scripts.gh_helpers.subprocess.run")
    def test_main_labels_only_flag(self, mock_run, tmp_path):
        mock_run.return_value = make_ok("[]")
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(SAMPLE_MANIFEST), encoding="utf-8")
        with patch(
            "sys.argv",
            [
                "set_relationships.py",
                "--manifest",
                str(manifest_file),
                "--repo",
                "org/repo",
                "--labels-only",
            ],
        ):
            set_relationships.main()


class TestSetProjectFieldsCLIMain:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_main_runs_without_error(self, mock_run, tmp_path):
        mock_run.return_value = make_ok(
            json.dumps(
                {
                    "data": {
                        "addProjectV2ItemById": {"item": {"id": "PVTI_x"}},
                        "updateIssue": {
                            "issue": {"id": "I_x", "issueType": {"name": "Story"}}
                        },
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": "PVTI_x"}
                        },
                    }
                }
            )
        )
        manifest_file = tmp_path / "manifest.json"
        config_file = tmp_path / "config.json"
        manifest_file.write_text(json.dumps(SAMPLE_MANIFEST), encoding="utf-8")
        config_file.write_text(json.dumps(SAMPLE_CONFIG), encoding="utf-8")
        with patch(
            "sys.argv",
            [
                "set_project_fields.py",
                "--manifest",
                str(manifest_file),
                "--config",
                str(config_file),
                "--org",
                "kdtix-open",
                "--project",
                "8",
            ],
        ):
            set_project_fields.main()


class TestFindByRef:
    def test_exact_match_preferred(self):
        by_title = {
            "API": {"title": "API", "number": 1},
            "API Gateway": {"title": "API Gateway", "number": 2},
            "API v2": {"title": "API v2", "number": 3},
        }
        result = set_relationships._find_by_ref("API", by_title)
        assert result["number"] == 1

    def test_substring_match_when_no_exact(self):
        by_title = {
            "Widget API Gateway": {"title": "Widget API Gateway", "number": 1},
        }
        result = set_relationships._find_by_ref("API", by_title)
        assert result["number"] == 1

    def test_returns_none_when_no_match(self):
        by_title = {
            "Widget": {"title": "Widget", "number": 1},
        }
        result = set_relationships._find_by_ref("Nonexistent", by_title)
        assert result is None
