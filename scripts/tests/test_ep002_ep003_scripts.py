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


class TestWalkExistingHierarchy:
    """FR #34 Stage 5 walker: fetch title + issue-type via GraphQL.

    Historical bug (fix/walker-graphql-issuetype): the walker used
    `gh issue view --json issueType` which is not supported by all installed
    gh CLI versions (e.g. gh 2.90.0 returns "Unknown JSON field: issueType").
    The walker now issues a GraphQL query via `gh api graphql` which is
    stable across gh versions.
    """

    @patch("scripts.gh_helpers.subprocess.run")
    def test_fetches_issue_via_graphql_not_issue_view(self, mock_run):
        """Walker must use `gh api graphql` so it works on all gh versions."""
        from scripts import create_issues

        # First call: GraphQL fetch of root issue #182.
        # Second call: REST fetch of sub_issues (empty → terminates recursion).
        mock_run.side_effect = [
            make_ok(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "issue": {
                                    "number": 182,
                                    "title": "Project Scope: Test",
                                    "issueType": {"name": "Project Scope"},
                                }
                            }
                        }
                    }
                )
            ),
            make_ok("[]"),
        ]
        results = create_issues._walk_existing_hierarchy("owner/repo", 182)
        assert len(results) == 1
        assert results[0]["number"] == 182
        assert results[0]["level"] == "scope"

        # Walker must call `gh api graphql` — NOT `gh issue view --json issueType`
        all_calls_str = str(mock_run.call_args_list)
        assert (
            "gh' 'api' 'graphql" in all_calls_str
            or "gh', 'api', 'graphql" in all_calls_str
        )
        assert (
            "--json" not in all_calls_str
            or "issueType" not in all_calls_str.split("--json")[1]
            if "--json" in all_calls_str
            else True
        )

    @patch("scripts.gh_helpers.subprocess.run")
    def test_walks_recursively_into_sub_issues(self, mock_run):
        from scripts import create_issues

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            # GraphQL call carries "query" text — detect by arg ordering
            if "graphql" in cmd:
                # Extract the number from the -F number=N arg
                num_arg = next((a for a in cmd if a.startswith("number=")), None)
                if not num_arg:
                    return make_ok("{}")
                n = int(num_arg.split("=", 1)[1])
                titles = {
                    182: ("Project Scope: Root", "Project Scope"),
                    183: ("Initiative: Child A", "Initiative"),
                    184: ("Epic: Grandchild", "Epic"),
                }
                title, itype = titles.get(n, (f"Issue {n}", "Task"))
                return make_ok(
                    json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "issue": {
                                        "number": n,
                                        "title": title,
                                        "issueType": {"name": itype},
                                    }
                                }
                            }
                        }
                    )
                )
            if "sub_issues" in cmd_str:
                # Map parent→children
                if "182" in cmd_str:
                    return make_ok(json.dumps([{"number": 183}]))
                if "183" in cmd_str:
                    return make_ok(json.dumps([{"number": 184}]))
                return make_ok("[]")
            return make_ok("{}")

        mock_run.side_effect = side_effect

        results = create_issues._walk_existing_hierarchy("owner/repo", 182)
        assert [r["number"] for r in results] == [182, 183, 184]
        assert [r["level"] for r in results] == ["scope", "initiative", "epic"]
        assert results[1]["parent_number"] == 182
        assert results[2]["parent_number"] == 183

    @patch("scripts.gh_helpers.subprocess.run")
    def test_fails_soft_on_graphql_error(self, mock_run):
        """If GraphQL fails for a node, walker returns [] without crashing."""
        from scripts import create_issues

        err = make_ok("")
        err.returncode = 1
        err.stderr = "HTTP 502"
        mock_run.return_value = err

        results = create_issues._walk_existing_hierarchy("owner/repo", 182)
        assert results == []

    @patch("scripts.gh_helpers.subprocess.run")
    def test_falls_back_to_depth_inference_when_issue_type_missing(self, mock_run):
        """If issueType is null, walker falls back to depth-based level."""
        from scripts import create_issues

        mock_run.side_effect = [
            make_ok(
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "issue": {
                                    "number": 182,
                                    "title": "Unlabeled issue",
                                    "issueType": None,  # no type set
                                }
                            }
                        }
                    }
                )
            ),
            make_ok("[]"),
        ]
        results = create_issues._walk_existing_hierarchy("owner/repo", 182)
        assert len(results) == 1
        # depth=0 → scope by fallback
        assert results[0]["level"] == "scope"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_rejects_malformed_repo_string(self, mock_run):
        """A repo with no '/' must not hit the network (defensive check)."""
        from scripts import create_issues

        results = create_issues._walk_existing_hierarchy("no-slash-here", 1)
        assert results == []
        mock_run.assert_not_called()


class TestRefreshMode:
    """FR #34 Stage 5: refresh existing backlog in-place without duplicates."""

    def test_normalize_title_strips_markdown_markers(self):
        """Backticks, bold, italic in plan titles must match plain-text GitHub titles."""
        from scripts import create_issues

        plan_title = (
            "Story: Self-Heal R-10 — Bridge credential allow-list must "
            "accept generic `GITHUB_TOKEN` / `GH_TOKEN`"
        )
        gh_title = (
            "Story: Self-Heal R-10 — Bridge credential allow-list must "
            "accept generic GITHUB_TOKEN / GH_TOKEN"
        )
        assert create_issues._normalize_title_for_match(
            plan_title
        ) == create_issues._normalize_title_for_match(gh_title)

    def test_normalize_title_strips_bold_and_italics(self):
        from scripts import create_issues

        assert create_issues._normalize_title_for_match(
            "Story: **Critical** auth fix"
        ) == create_issues._normalize_title_for_match("Story: Critical auth fix")

    def test_normalize_title_collapses_whitespace(self):
        from scripts import create_issues

        assert (
            create_issues._normalize_title_for_match("Story:  double  space  inside")
            == "double space inside"
        )

    def test_flatten_parsed_hierarchy_normalizes_prefixes(self):
        from scripts import create_issues

        hierarchy = {
            "scope": {"title": "Project Scope: PS-XXX Foo Bar", "description": "desc"},
            "initiatives": [{"title": "Initiative: INIT-001 Baz", "description": "x"}],
            "epics": [{"title": "Epic: EP-001 Quux", "description": "y"}],
            "stories": [{"title": "Story: Add widget", "description": "z"}],
            "tasks": [],
        }
        flat = create_issues._flatten_parsed_hierarchy(hierarchy)
        # All normalized to lowercase + prefix-stripped
        assert "ps-xxx foo bar" in flat
        assert "init-001 baz" in flat
        assert "ep-001 quux" in flat
        assert "add widget" in flat
        assert flat["ps-xxx foo bar"]["level"] == "scope"
        assert flat["init-001 baz"]["level"] == "initiative"
        assert flat["add widget"]["level"] == "story"

    def test_refresh_backlog_dry_run_reports_would_update(self, mocker, tmp_path):
        """Dry-run path: report shows would-update for mismatched bodies."""
        from scripts import create_issues

        # Mock plan parse
        mocker.patch(
            "scripts.create_issues.parse_plan",
            return_value={
                "scope": {
                    "title": "Project Scope: PS-X Test",
                    "description": "desc",
                    "priority": "P0",
                    "size": "M",
                },
                "initiatives": [],
                "epics": [],
                "stories": [],
                "tasks": [],
            },
        )
        # Mock hierarchy walk
        mocker.patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 182,
                    "title": "Project Scope: PS-X Test",
                    "level": "scope",
                    "parent_number": None,
                },
            ],
        )
        # Mock body fetch (different from what generate_body would produce)
        mocker.patch(
            "scripts.gh_helpers.get_issue_body",
            return_value="OLD BODY with [CRITERION 1] placeholder",
        )
        update_mock = mocker.patch("scripts.gh_helpers.update_issue_body")

        report = create_issues.refresh_backlog(
            plan_path="dummy.md",
            repo="owner/repo",
            scope_issue_number=182,
            dry_run=True,
        )

        assert report["summary"]["existing_issues"] == 1
        assert report["summary"]["matched"] == 1
        assert report["summary"]["updated"] == 1  # would-update counted here
        assert report["summary"]["unmatched"] == 0
        # dry_run → update_issue_body NEVER called
        update_mock.assert_not_called()
        assert report["per_issue"][0]["status"] == "would-update"
        # Stage 2d: report now includes a unified diff (not just char counts)
        assert "diff" in report["per_issue"][0]
        diff_text = report["per_issue"][0]["diff"]
        assert "issue-182-before" in diff_text
        assert "issue-182-after" in diff_text
        assert "OLD BODY" in diff_text  # the removed line shows in the diff

    def test_refresh_skip_issues_excludes_from_apply(self, mocker):
        """Issues in skip_issues are reported as 'skipped', never body-fetched."""
        from scripts import create_issues

        mocker.patch(
            "scripts.create_issues.parse_plan",
            return_value={
                "scope": {
                    "title": "Project Scope: PS-X Test",
                    "description": "desc",
                    "priority": "P0",
                    "size": "M",
                },
                "initiatives": [],
                "epics": [],
                "stories": [],
                "tasks": [],
            },
        )
        mocker.patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 182,
                    "title": "Project Scope: PS-X Test",
                    "level": "scope",
                    "parent_number": None,
                },
                {
                    "number": 266,
                    "title": "Story: Preserve me",
                    "level": "story",
                    "parent_number": 182,
                },
            ],
        )
        get_body_mock = mocker.patch(
            "scripts.gh_helpers.get_issue_body", return_value="# Old scope body"
        )
        update_mock = mocker.patch("scripts.gh_helpers.update_issue_body")

        report = create_issues.refresh_backlog(
            plan_path="dummy.md",
            repo="owner/repo",
            scope_issue_number=182,
            dry_run=False,
            skip_issues={266},
        )

        # Skipped issue is reported as 'skipped' + never body-fetched
        statuses = {i["number"]: i["status"] for i in report["per_issue"]}
        assert statuses[266] == "skipped"
        assert report["summary"]["skipped"] == 1
        # get_issue_body was called for #182 but NOT #266
        fetched_numbers = [call.args[1] for call in get_body_mock.call_args_list]
        assert 266 not in fetched_numbers
        # update_issue_body never called with #266
        for call in update_mock.call_args_list:
            args = call.args
            assert args[1] != 266

    def test_preserve_outside_zone_keeps_html_comment_prefix(self):
        """Stage 2.5: HTML comment + blockquote before `# Heading` survive refresh."""
        from scripts import create_issues

        existing = (
            "<!-- scope-sequence-order: 1 -->\n\n"
            "> **Sequence Order: 1** — operator-declared ship-order.  "
            "Read by Story #250 parser.\n\n"
            "# Project Scope: Test\n\n"
            "Body content here.\n"
        )
        new = "# Project Scope: Test\n\nFreshly rendered body.\n"
        merged, preserved = create_issues._preserve_outside_template_zone(existing, new)
        assert "<!-- scope-sequence-order: 1 -->" in merged
        assert "**Sequence Order: 1**" in merged
        assert "Read by Story #250 parser." in merged
        assert "Freshly rendered body." in merged
        assert "scope-sequence-order" in preserved["prefix"]

    def test_preserve_outside_zone_is_idempotent(self):
        """Running the merge twice doesn't duplicate the prefix."""
        from scripts import create_issues

        existing = "<!-- marker -->\n\n# Project Scope: Test\n\nA\n"
        new = "# Project Scope: Test\n\nB\n"
        once, _ = create_issues._preserve_outside_template_zone(existing, new)
        twice, _ = create_issues._preserve_outside_template_zone(once, once)
        assert once.count("<!-- marker -->") == 1
        assert twice.count("<!-- marker -->") == 1

    def test_preserve_outside_zone_keeps_trailing_signature(self):
        """Content after the `_Created: ..._` footer survives refresh."""
        from scripts import create_issues

        existing = (
            "# Project Scope: Test\n\n"
            "Body.\n\n"
            "_Created: 2026-01-01 | Owner: Someone_\n\n"
            "---\n\n"
            "_Operator note: this scope tracks Q1 commitment._\n"
        )
        new = (
            "# Project Scope: Test\n\n"
            "Body refreshed.\n\n"
            "_Created: 2026-04-21 | Owner: TBD_\n"
        )
        merged, preserved = create_issues._preserve_outside_template_zone(existing, new)
        assert "Operator note: this scope tracks Q1 commitment." in merged
        assert "_Created: 2026-04-21" in merged
        assert "Operator note" in preserved["suffix"]

    def test_preserve_outside_zone_no_op_when_no_outside_content(self):
        """When the existing body has no prefix/suffix, the merge is a no-op."""
        from scripts import create_issues

        existing = "# Project Scope: Test\n\nBody.\n"
        new = "# Project Scope: Test\n\nNew body.\n"
        merged, preserved = create_issues._preserve_outside_template_zone(existing, new)
        assert merged == new
        assert preserved["prefix"] == ""
        assert preserved["suffix"] == ""

    def test_unified_diff_snippet_truncates_long_diffs(self):
        """Diffs longer than max_lines are capped with a truncation marker."""
        from scripts import create_issues

        before = "\n".join(f"line {i}" for i in range(500))
        after = "\n".join(f"changed {i}" for i in range(500))
        diff = create_issues._unified_diff_snippet(before, after, 42, max_lines=50)
        assert "[truncated at 50 lines]" in diff
        # Sanity: the truncated diff is still a valid unified-diff prefix.
        assert diff.startswith("--- issue-42-before")

    def test_refresh_backlog_apply_mode_calls_update(self, mocker, tmp_path):
        """apply mode (dry_run=False) invokes update_issue_body."""
        from scripts import create_issues

        mocker.patch(
            "scripts.create_issues.parse_plan",
            return_value={
                "scope": {
                    "title": "Project Scope: PS-X Test",
                    "description": "desc",
                    "priority": "P0",
                    "size": "M",
                },
                "initiatives": [],
                "epics": [],
                "stories": [],
                "tasks": [],
            },
        )
        mocker.patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 182,
                    "title": "Project Scope: PS-X Test",
                    "level": "scope",
                    "parent_number": None,
                },
            ],
        )
        mocker.patch(
            "scripts.gh_helpers.get_issue_body",
            return_value="OLD BODY",
        )
        update_mock = mocker.patch("scripts.gh_helpers.update_issue_body")

        report = create_issues.refresh_backlog(
            plan_path="dummy.md",
            repo="owner/repo",
            scope_issue_number=182,
            dry_run=False,
        )

        assert report["summary"]["updated"] == 1
        update_mock.assert_called_once()
        args, _ = update_mock.call_args
        assert args[0] == "owner/repo"
        assert args[1] == 182
        # new body is generated by generate_body — not "OLD BODY"
        assert "OLD BODY" not in args[2]
        assert report["per_issue"][0]["status"] == "updated"

    def test_refresh_backlog_reports_unmatched(self, mocker, tmp_path):
        """Existing issues without a parsed-plan counterpart → unmatched."""
        from scripts import create_issues

        mocker.patch(
            "scripts.create_issues.parse_plan",
            return_value={
                "scope": {
                    "title": "Project Scope: PS-X Test",
                    "description": "d",
                    "priority": "P0",
                    "size": "M",
                },
                "initiatives": [],
                "epics": [],
                "stories": [],
                "tasks": [],
            },
        )
        mocker.patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 999,
                    "title": "Some Orphan Issue Not In Plan",
                    "level": "story",
                    "parent_number": None,
                },
            ],
        )
        get_body_mock = mocker.patch("scripts.gh_helpers.get_issue_body")
        update_mock = mocker.patch("scripts.gh_helpers.update_issue_body")

        report = create_issues.refresh_backlog(
            plan_path="dummy.md",
            repo="owner/repo",
            scope_issue_number=999,
            dry_run=True,
        )

        assert report["summary"]["unmatched"] == 1
        assert report["summary"]["matched"] == 0
        # Unmatched issues shouldn't have their bodies fetched or updated
        get_body_mock.assert_not_called()
        update_mock.assert_not_called()

    def test_refresh_backlog_unchanged_when_body_matches(self, mocker, tmp_path):
        """When existing body exactly matches re-rendered body → unchanged."""
        from scripts import create_issues

        mocker.patch(
            "scripts.create_issues.parse_plan",
            return_value={
                "scope": {
                    "title": "Project Scope: PS-X Test",
                    "description": "d",
                    "priority": "P0",
                    "size": "M",
                },
                "initiatives": [],
                "epics": [],
                "stories": [],
                "tasks": [],
            },
        )
        mocker.patch(
            "scripts.create_issues._walk_existing_hierarchy",
            return_value=[
                {
                    "number": 182,
                    "title": "Project Scope: PS-X Test",
                    "level": "scope",
                    "parent_number": None,
                },
            ],
        )
        # Generate what the skill would produce, then mock get_issue_body to return that
        expected_body = create_issues.generate_body(
            {
                "title": "Project Scope: PS-X Test",
                "description": "d",
                "priority": "P0",
                "size": "M",
            },
            "scope",
        )
        mocker.patch("scripts.gh_helpers.get_issue_body", return_value=expected_body)
        update_mock = mocker.patch("scripts.gh_helpers.update_issue_body")

        report = create_issues.refresh_backlog(
            plan_path="dummy.md",
            repo="owner/repo",
            scope_issue_number=182,
            dry_run=True,
        )

        assert report["summary"]["unchanged"] == 1
        assert report["summary"]["updated"] == 0
        update_mock.assert_not_called()


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
            "[Describe the problem being solved and why the "
            "current approach is insufficient]\n"
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

    def test_gate_fails_when_placeholders_present_and_not_allowed(
        self, mocker, tmp_path
    ):
        # Mock the GH API calls
        mocker.patch(
            "scripts.compliance_check.get_issue_body", return_value="[CRITERION 1]"
        )
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
        mocker.patch(
            "scripts.compliance_check.get_issue_body", return_value="[CRITERION 1]"
        )
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


# ===========================================================================
# FR #34 Stage 2: Structured subsection parser + per-level renderers
# ===========================================================================


class TestSubsectionParser:
    """Parser: extract `#### Section Name` subsections from item bodies."""

    def test_returns_empty_dict_for_blank_body(self):
        from scripts import create_issues

        assert create_issues._parse_subsections("", "scope") == {}

    def test_stores_leading_text_when_no_subsections_present(self):
        from scripts import create_issues

        subs = create_issues._parse_subsections(
            "Just a prose description.\nNo subsections.",
            "scope",
        )
        assert subs == {
            "_leading_text": "Just a prose description.\nNo subsections.",
        }

    def test_extracts_single_paragraph_subsection(self):
        from scripts import create_issues

        body = (
            "Intro paragraph.\n\n"
            "#### Business Problem\n\n"
            "Legacy approach uses N+1 queries and burns 3x the CPU.\n"
        )
        subs = create_issues._parse_subsections(body, "scope")
        assert subs["_leading_text"] == "Intro paragraph."
        assert (
            subs["business_problem"]
            == "Legacy approach uses N+1 queries and burns 3x the CPU."
        )

    def test_extracts_bullet_subsection_as_list(self):
        from scripts import create_issues

        body = (
            "#### Success Criteria\n\n"
            "- [ ] Metric A hits target\n"
            "- [ ] Metric B unchanged\n"
            "- Metric C captured in dashboard\n"
        )
        subs = create_issues._parse_subsections(body, "scope")
        assert subs["success_criteria"] == [
            "Metric A hits target",
            "Metric B unchanged",
            "Metric C captured in dashboard",
        ]

    def test_extracts_moscow_nested_groups(self):
        from scripts import create_issues

        body = (
            "#### MoSCoW\n\n"
            "**Must Have**:\n"
            "- Token metering\n"
            "- Admin dashboard\n\n"
            "**Should Have**:\n"
            "- Realtime alerts\n\n"
            "**Could Have**:\n"
            "- Slack integration\n\n"
            "**Won't Have**:\n"
            "- Per-user throttling (this release)\n"
        )
        subs = create_issues._parse_subsections(body, "scope")
        assert subs["moscow"] == {
            "must_have": ["Token metering", "Admin dashboard"],
            "should_have": ["Realtime alerts"],
            "could_have": ["Slack integration"],
            "wont_have": ["Per-user throttling (this release)"],
        }

    def test_heading_aliases_are_case_insensitive(self):
        from scripts import create_issues

        body = "### BUSINESS PROBLEM & CURRENT STATE\n\nfoo\n"
        subs = create_issues._parse_subsections(body, "scope")
        assert subs.get("business_problem") == "foo"

    def test_unrecognized_heading_stays_as_content(self):
        from scripts import create_issues

        body = "#### Implementation Notes\n\n" "### Approach\n\n" "Do X then Y.\n"
        subs = create_issues._parse_subsections(body, "task")
        # Unrecognized `### Approach` is not a new subsection — it remains
        # inside Implementation Notes as content.
        assert "### Approach" in subs["implementation_notes"]
        assert "Do X then Y." in subs["implementation_notes"]

    def test_bullet_variants_all_recognized(self):
        from scripts import create_issues

        body = (
            "#### Assumptions\n\n"
            "- dash bullet\n"
            "* star bullet\n"
            "- [ ] checkbox unchecked\n"
            "- [x] checkbox checked\n"
        )
        subs = create_issues._parse_subsections(body, "scope")
        assert subs["assumptions"] == [
            "dash bullet",
            "star bullet",
            "checkbox unchecked",
            "checkbox checked",
        ]

    def test_paragraph_in_bullet_section_wraps_as_single_item(self):
        from scripts import create_issues

        # If the author wrote prose under a bullet-expected subsection,
        # the parser should still capture something rather than drop it.
        body = (
            "#### Out of Scope\n\n"
            "Anything involving the legacy API is explicitly out.\n"
        )
        subs = create_issues._parse_subsections(body, "scope")
        assert subs["out_of_scope"] == [
            "Anything involving the legacy API is explicitly out.",
        ]

    def test_parses_initiative_specific_keys(self):
        from scripts import create_issues

        body = (
            "#### Objective\nWhy it exists.\n\n"
            "#### Release Value\nWhat ships.\n\n"
            "#### Artifacts\n- Runbook\n- Dashboard\n"
        )
        subs = create_issues._parse_subsections(body, "initiative")
        assert subs["objective"] == "Why it exists."
        assert subs["release_value"] == "What ships."
        assert subs["artifacts"] == ["Runbook", "Dashboard"]

    def test_parses_task_specific_keys(self):
        from scripts import create_issues

        body = (
            "#### Summary\nImplement the thing.\n\n"
            "#### Context\n"
            "- Parent AC: X\n"
            "- Preceding: #5\n"
        )
        subs = create_issues._parse_subsections(body, "task")
        assert subs["summary"] == "Implement the thing."
        assert subs["context"] == ["Parent AC: X", "Preceding: #5"]


class TestRenderScopeSubsections:
    """Renderer: scope template placeholders filled from subsections."""

    def _scope_item(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test scope",
            "description": body,
            "priority": "P1",
            "size": "M",
            "subsections": create_issues._parse_subsections(body, "scope"),
        }

    def test_success_criteria_replaces_placeholder_block(self):
        from scripts import create_issues

        item = self._scope_item(
            "#### Success Criteria\n- All providers reach parity\n- CI stays green\n"
        )
        body = create_issues.generate_body(item, "scope")
        assert "[CRITERION 1]" not in body
        assert "[CRITERION 2]" not in body
        assert "- [ ] All providers reach parity" in body
        assert "- [ ] CI stays green" in body

    def test_business_problem_replaces_placeholder(self):
        from scripts import create_issues

        item = self._scope_item("#### Business Problem\nLegacy path cannot meet SLA.\n")
        body = create_issues.generate_body(item, "scope")
        assert "[Describe the problem" not in body
        assert "Legacy path cannot meet SLA." in body

    def test_assumptions_replaces_placeholder_block(self):
        from scripts import create_issues

        item = self._scope_item(
            "#### Assumptions\n- gh CLI is available\n- Tokens are valid\n"
        )
        body = create_issues.generate_body(item, "scope")
        assert "[ASSUMPTION 1]" not in body
        assert "[ASSUMPTION 2]" not in body
        assert "- gh CLI is available" in body

    def test_out_of_scope_replaces_placeholder_block(self):
        from scripts import create_issues

        item = self._scope_item("#### Out of Scope\n- Windows supervisor\n")
        body = create_issues.generate_body(item, "scope")
        assert "[ITEM 1]" not in body
        assert "[ITEM 2]" not in body
        assert "- Windows supervisor" in body

    def test_moscow_replaces_table_rows(self):
        from scripts import create_issues

        item = self._scope_item(
            "#### MoSCoW\n\n"
            "**Must Have**:\n- Token metering\n\n"
            "**Should Have**:\n- Alerts\n\n"
            "**Could Have**:\n- Slack bot\n\n"
            "**Won't Have**:\n- Throttling\n"
        )
        body = create_issues.generate_body(item, "scope")
        assert "| Must Have | [ITEM] |" not in body
        assert "| Must Have | Token metering |" in body
        assert "| Should Have | Alerts |" in body
        assert "| Could Have | Slack bot |" in body
        assert "| Won't Have | Throttling |" in body

    def test_done_when_replaces_project_specific_criterion(self):
        from scripts import create_issues

        item = self._scope_item(
            "#### I Know I Am Done When\n- Dashboard live in prod\n"
        )
        body = create_issues.generate_body(item, "scope")
        assert "[PROJECT-SPECIFIC CRITERION]" not in body
        assert "- [ ] Dashboard live in prod" in body

    def test_backward_compat_no_subsections_keeps_placeholders(self):
        """Plans without subsections get the same behavior as pre-Stage-2."""
        from scripts import create_issues

        item = self._scope_item("Just a prose description with no subsections.")
        body = create_issues.generate_body(item, "scope")
        # Placeholders should still be present (Stage 1 scanner catches them).
        assert "[CRITERION 1]" in body or "- [ ] [CRITERION" in body
        assert "[ASSUMPTION 1]" in body or "- [ASSUMPTION" in body
        # But vision/leading text should be populated from the description.
        assert "Just a prose description" in body


class TestRenderInitiativeSubsections:
    def _init_item(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test initiative",
            "description": body,
            "priority": "P1",
            "size": "M",
            "subsections": create_issues._parse_subsections(body, "initiative"),
        }

    def test_release_value_replaces_placeholder(self):
        from scripts import create_issues

        item = self._init_item(
            "#### Release Value\nTeams can bill per-user for inference.\n"
        )
        body = create_issues.generate_body(item, "initiative")
        assert "Teams can bill per-user for inference." in body
        assert "[What becomes possible after this initiative ships" not in body

    def test_artifacts_replaces_placeholder(self):
        from scripts import create_issues

        item = self._init_item("#### Artifacts\n- Runbook\n- Dashboard\n")
        body = create_issues.generate_body(item, "initiative")
        assert "[ARTIFACT]" not in body
        assert "- [ ] Runbook" in body
        assert "- [ ] Dashboard" in body


class TestRenderEpicSubsections:
    def _epic_item(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test epic",
            "description": body,
            "priority": "P1",
            "size": "M",
            "parent_ref": "Test initiative",
            "subsections": create_issues._parse_subsections(body, "epic"),
        }

    def test_release_value_uses_epic_specific_placeholder(self):
        from scripts import create_issues

        item = self._epic_item("#### Release Value\nNew dashboard page.\n")
        body = create_issues.generate_body(item, "epic")
        assert "New dashboard page." in body
        assert "[What becomes possible after this epic ships" not in body

    def test_questions_for_tech_lead_replaces_placeholder(self):
        from scripts import create_issues

        item = self._epic_item(
            "#### Questions for Tech Lead\n- Sync or async cache?\n- HTTP/2?\n"
        )
        body = create_issues.generate_body(item, "epic")
        assert "- [QUESTION]" not in body
        assert "- Sync or async cache?" in body


class TestRenderStorySubsections:
    def _story_item(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test story",
            "description": body,
            "priority": "P1",
            "size": "M",
            "parent_ref": "Test epic",
            "subsections": create_issues._parse_subsections(body, "story"),
        }

    def test_user_story_block_replaces_as_a_template(self):
        from scripts import create_issues

        item = self._story_item(
            "#### User Story\n"
            "As a finance lead,\n"
            "I want monthly cost reports,\n"
            "So that I can chargeback.\n"
        )
        body = create_issues.generate_body(item, "story")
        assert "As a [ROLE]" not in body
        assert "As a finance lead" in body

    def test_why_this_matters_replaces_placeholder(self):
        from scripts import create_issues

        item = self._story_item("#### Why This Matters\nCosts are untracked today.\n")
        body = create_issues.generate_body(item, "story")
        assert "[Why this story is needed" not in body
        assert "Costs are untracked today." in body

    def test_constraints_replaces_placeholder(self):
        from scripts import create_issues

        item = self._story_item("#### Constraints\n- Must ship before Q2\n")
        body = create_issues.generate_body(item, "story")
        assert "- [CONSTRAINT]" not in body
        assert "- Must ship before Q2" in body


class TestRenderTaskSubsections:
    def _task_item(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test task",
            "description": body,
            "priority": "P1",
            "size": "S",
            "parent_ref": "Test story",
            "subsections": create_issues._parse_subsections(body, "task"),
        }

    def test_context_replaces_placeholder_block(self):
        from scripts import create_issues

        item = self._task_item(
            "#### Context\n"
            '- Parent AC: "cost report generates on schedule"\n'
            "- Preceding: #12\n"
            "- Blocks: #15\n"
        )
        body = create_issues.generate_body(item, "task")
        assert "[The acceptance criterion this task satisfies]" not in body
        assert '- Parent AC: "cost report generates on schedule"' in body

    def test_implementation_notes_replaces_placeholder(self):
        from scripts import create_issues

        item = self._task_item(
            "#### Implementation Notes\n\n### Approach\n" "Use regex then pandas.\n"
        )
        body = create_issues.generate_body(item, "task")
        assert "[How to implement this" not in body
        assert "Use regex then pandas." in body


class TestEndToEndPlanRender:
    """End-to-end: parse a full structured plan and verify no placeholder leaks."""

    def test_fully_structured_plan_has_no_placeholder_strings(self, tmp_path):
        """Given a plan with every subsection populated, the rendered
        scope body should contain no unreplaced bracket placeholders.
        """
        from scripts import compliance_check, create_issues

        plan_body = (
            "# Project Scope: PS-TEST — End-to-end verification\n\n"
            "Priority: P0\nSize: M\n\n"
            "The end state is a fully-populated scope body with zero placeholders.\n\n"
            "#### Business Problem\n\n"
            "Today, scopes ship with template placeholder strings.\n\n"
            "#### Success Criteria\n\n"
            "- All subsections populate from plan\n"
            "- Scanner reports zero P0-4 gaps\n\n"
            "#### In-Scope Capabilities\n\n"
            "- Subsection parser\n"
            "- Per-level renderers\n\n"
            "#### Assumptions\n\n"
            "- gh CLI is available\n"
            "- Tokens are valid\n\n"
            "#### Out of Scope\n\n"
            "- Windows supervisor\n\n"
            "#### MoSCoW\n\n"
            "**Must Have**:\n- Token metering\n\n"
            "**Should Have**:\n- Realtime alerts\n\n"
            "**Could Have**:\n- Slack bot\n\n"
            "**Won't Have**:\n- Per-user throttling\n\n"
            "#### I Know I Am Done When\n\n"
            "- Scope renders with no placeholders\n"
        )
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(plan_body, encoding="utf-8")

        hierarchy = create_issues.parse_plan(str(plan_path))
        scope = hierarchy["scope"]
        body = create_issues.generate_body(scope, "scope")

        # Per Stage 1: the P0-4 scanner must not flag this as incomplete
        gaps = compliance_check.check_issue(1, scope["title"], body, "scope")
        p0_4 = [g for g in gaps if g["rule"] == "P0-4"]
        assert not p0_4, (
            f"Unexpected placeholder gaps after full render: "
            f"{[g.get('placeholders') for g in p0_4]}"
        )


# ===========================================================================
# FR #40: Mermaid diagram subsections
# ===========================================================================


class TestMermaidDiagramParser:
    def test_parses_single_sequence_diagram(self):
        from scripts import create_issues

        body = (
            "#### Sequence Diagram\n\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    Alice->>Bob: Hi\n"
            "```\n"
        )
        subs = create_issues._parse_subsections(body, "story")
        assert "diagrams" in subs
        assert len(subs["diagrams"]) == 1
        d = subs["diagrams"][0]
        assert d["type"] == "sequenceDiagram"
        assert "Alice->>Bob: Hi" in d["source"]

    def test_parses_multiple_diagram_subsections(self):
        """Two subsection headings → two diagrams in the list."""
        from scripts import create_issues

        body = (
            "#### Sequence Diagram\n\n"
            "```mermaid\n"
            "sequenceDiagram\n    A->>B: x\n"
            "```\n\n"
            "#### State Diagram\n\n"
            "```mermaid\n"
            "stateDiagram-v2\n    [*] --> Idle\n"
            "```\n"
        )
        subs = create_issues._parse_subsections(body, "story")
        assert len(subs["diagrams"]) == 2
        types = [d["type"] for d in subs["diagrams"]]
        assert "sequenceDiagram" in types
        assert "stateDiagram-v2" in types

    def test_infers_type_from_block_when_subsection_is_generic(self):
        """`#### Diagram` (generic key) gets type from the block's directive."""
        from scripts import create_issues

        body = (
            "#### Diagram\n\n"
            "```mermaid\n"
            "erDiagram\n"
            "    USER ||--o{ ORDER : places\n"
            "```\n"
        )
        subs = create_issues._parse_subsections(body, "initiative")
        assert subs["diagrams"][0]["type"] == "erDiagram"

    def test_unfenced_mermaid_source_accepted_if_has_directive(self):
        """If operator forgets fences but writes a valid directive,
        the parser still captures the content."""
        from scripts import create_issues

        body = "#### Flowchart\n\n" "flowchart LR\n    A-->B\n    B-->C\n"
        subs = create_issues._parse_subsections(body, "epic")
        assert len(subs["diagrams"]) == 1
        assert subs["diagrams"][0]["type"] == "flowchart"

    def test_diagram_subsection_recognized_at_every_level(self):
        """Every level accepts diagram subsection aliases."""
        from scripts import create_issues

        body_template = (
            "#### Sequence Diagram\n\n"
            "```mermaid\nsequenceDiagram\n    A->>B: x\n```\n"
        )
        for level in ("scope", "initiative", "epic", "story", "task"):
            subs = create_issues._parse_subsections(body_template, level)
            assert subs.get("diagrams"), f"Level {level} missed diagram"


class TestMermaidDiagramRenderer:
    def _scope_item_with_body(self, body: str) -> dict:
        from scripts import create_issues

        return {
            "title": "Test scope",
            "description": body,
            "priority": "P1",
            "size": "M",
            "subsections": create_issues._parse_subsections(body, "scope"),
        }

    def test_scope_renders_architecture_diagrams_section(self):
        from scripts import create_issues

        item = self._scope_item_with_body(
            "#### Architecture Diagram\n\n"
            "```mermaid\n"
            "C4Context\n    title System\n    Person(u, 'User')\n"
            "```\n"
        )
        body = create_issues.generate_body(item, "scope")
        assert "## Architecture & Diagrams" in body
        assert "C4Context" in body
        assert "```mermaid" in body

    def test_story_renders_workflow_and_diagrams_section(self):
        from scripts import create_issues

        body = (
            "#### Sequence Diagram\n\n"
            "```mermaid\nsequenceDiagram\n    A->>B: x\n```\n"
        )
        item = {
            "title": "Test",
            "description": body,
            "priority": "P1",
            "size": "M",
            "parent_ref": "Test epic",
            "subsections": create_issues._parse_subsections(body, "story"),
        }
        rendered = create_issues.generate_body(item, "story")
        assert "## Workflow & Diagrams" in rendered
        assert "sequenceDiagram" in rendered

    def test_no_diagrams_elides_hook_section(self):
        """When the plan has no diagrams, the template hook is removed
        (no empty 'Architecture & Diagrams' section)."""
        from scripts import create_issues

        item = self._scope_item_with_body("Just a Vision paragraph.")
        body = create_issues.generate_body(item, "scope")
        assert "[DIAGRAMS_HOOK_SCOPE]" not in body
        assert "## Architecture & Diagrams" not in body

    def test_multiple_diagrams_get_sub_headings(self):
        from scripts import create_issues

        body = (
            "#### Sequence Diagram\n\n"
            "```mermaid\nsequenceDiagram\n    A->>B: 1\n```\n\n"
            "#### State Diagram\n\n"
            "```mermaid\nstateDiagram-v2\n    [*] --> Idle\n```\n"
        )
        item = {
            "title": "Test",
            "description": body,
            "priority": "P1",
            "size": "M",
            "parent_ref": "Test epic",
            "subsections": create_issues._parse_subsections(body, "story"),
        }
        rendered = create_issues.generate_body(item, "story")
        # Both diagrams should render with their type labels
        assert "### Sequence Diagram" in rendered
        assert "### State Diagram" in rendered
        assert "sequenceDiagram" in rendered
        assert "stateDiagram-v2" in rendered

    def test_task_level_has_no_diagram_hook_by_default(self):
        """Task template has no [DIAGRAMS_HOOK_TASK] placeholder."""
        from scripts import create_issues

        body = "#### Flowchart\n\n" "```mermaid\nflowchart LR\n    A-->B\n```\n"
        item = {
            "title": "Test",
            "description": body,
            "priority": "P1",
            "size": "S",
            "parent_ref": "Test story",
            "subsections": create_issues._parse_subsections(body, "task"),
        }
        rendered = create_issues.generate_body(item, "task")
        # The diagram subsection is parsed but the task template has no
        # conventional hook.  Operators who want a task-level diagram can
        # add the section manually.  This asserts the render doesn't
        # crash and doesn't pollute the body with a stray [DIAGRAMS_*]
        # placeholder string.
        assert "[DIAGRAMS_HOOK" not in rendered


class TestP0_5MermaidValidation:
    def test_valid_mermaid_block_not_flagged(self):
        from scripts import compliance_check

        body = (
            "# Scope: Test\n\n"
            "## Architecture & Diagrams\n\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    Alice->>Bob: Hello\n"
            "```\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        assert not [g for g in gaps if g.get("rule") == "P0-5"]

    def test_invalid_mermaid_block_flagged(self):
        from scripts import compliance_check

        body = (
            "# Scope: Test\n\n"
            "## Architecture & Diagrams\n\n"
            "```mermaid\n"
            "This is not actually mermaid syntax\n"
            "just prose someone pasted in\n"
            "```\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        p0_5 = [g for g in gaps if g.get("rule") == "P0-5"]
        assert len(p0_5) == 1
        assert "invalid Mermaid" in p0_5[0]["description"]

    def test_empty_mermaid_block_flagged(self):
        from scripts import compliance_check

        body = "```mermaid\n\n```\n"
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        p0_5 = [g for g in gaps if g.get("rule") == "P0-5"]
        assert len(p0_5) == 1

    def test_comments_in_mermaid_block_are_skipped(self):
        """`%%` comment lines don't count as the 'first line'."""
        from scripts import compliance_check

        body = (
            "```mermaid\n"
            "%% top comment\n"
            "%% another comment\n"
            "sequenceDiagram\n"
            "    A->>B: x\n"
            "```\n"
        )
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        assert not [g for g in gaps if g.get("rule") == "P0-5"]

    def test_mermaid_validation_case_insensitive(self):
        """`FlowChart LR` (mixed case) is still recognized as valid."""
        from scripts import compliance_check

        body = "```mermaid\n" "FlowChart LR\n" "    A --> B\n" "```\n"
        gaps = compliance_check.check_issue(1, "Test", body, "scope")
        assert not [g for g in gaps if g.get("rule") == "P0-5"]
