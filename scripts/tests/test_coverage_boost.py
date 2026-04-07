"""Additional tests to ensure 80%+ coverage threshold."""

from __future__ import annotations

from unittest.mock import patch

from scripts import create_issues, queue_order
from scripts.tests.conftest import SAMPLE_MANIFEST, make_ok


class TestQueueOrderHelpers:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_get_project_status_returns_backlog_on_failure(self, mock_run):
        m = make_ok("")
        m.returncode = 1
        mock_run.return_value = m
        status = queue_order._get_project_status("org/repo", 1)
        assert status == "Backlog"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_get_project_status_returns_value(self, mock_run):
        mock_run.return_value = make_ok("In Progress")
        status = queue_order._get_project_status("org/repo", 1)
        assert status == "In Progress"

    def test_get_parent_status_no_parent(self):
        record = {"parent_ref": None, "number": 1}
        status = queue_order._get_parent_status(record, {}, "org/repo")
        assert status == "Done"

    def test_get_parent_status_parent_not_in_manifest(self):
        record = {"parent_ref": "Nonexistent", "number": 1}
        status = queue_order._get_parent_status(record, {}, "org/repo")
        assert status == "Done"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_run_queue_order_with_output_dir(self, mock_run, tmp_path):
        mock_run.return_value = make_ok("[]")
        with patch.object(
            queue_order,
            "compute_queue_order",
            return_value=[SAMPLE_MANIFEST["story-1"]],
        ):
            queue_order.run_queue_order(
                SAMPLE_MANIFEST, "org/repo", output_dir=tmp_path
            )
        assert (tmp_path / "queue-order.json").exists()

    def test_compute_queue_order_excludes_non_backlog_status(self):
        manifest = {
            "epic-1": {
                "number": 10,
                "level": "epic",
                "title": "Epic",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 11,
                "level": "story",
                "title": "Active Story",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "S",
                "blocking": [],
            },
        }
        statuses = {11: "In Progress", 10: "In Progress"}
        labels_map = {11: []}
        ordered = queue_order.compute_queue_order(
            manifest, "org/repo", statuses=statuses, labels_map=labels_map
        )
        assert len(ordered) == 0

    def test_compute_queue_order_excludes_parent_in_backlog(self):
        manifest = {
            "epic-1": {
                "number": 10,
                "level": "epic",
                "title": "Epic",
                "parent_ref": None,
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "story-1": {
                "number": 11,
                "level": "story",
                "title": "Story",
                "parent_ref": "Epic",
                "priority": "P0",
                "size": "S",
                "blocking": [],
            },
        }
        statuses = {11: "Backlog", 10: "Backlog"}
        labels_map = {11: []}
        ordered = queue_order.compute_queue_order(
            manifest, "org/repo", statuses=statuses, labels_map=labels_map
        )
        assert len(ordered) == 0


class TestCreateIssuesOutputDir:
    @patch("scripts.create_issues._get_issue_ids")
    @patch("scripts.create_issues._create_issue")
    def test_create_all_issues_with_output_dir(self, mock_create, mock_ids, tmp_path):
        counter = [1]

        def create_side(repo, title, body):
            n = counter[0]
            counter[0] += 1
            return f"https://github.com/org/repo/issues/{n}"

        def ids_side(repo, number):
            return {
                "nodeId": f"I_node_{number}",
                "databaseId": number * 100,
                "number": number,
            }

        mock_create.side_effect = create_side
        mock_ids.side_effect = ids_side

        hierarchy = {
            "scope": {
                "title": "Test",
                "description": "",
                "priority": "P0",
                "size": "M",
                "blocking": [],
            },
            "initiative": None,
            "epics": [],
            "stories": [],
            "tasks": [],
        }
        manifest = create_issues.create_all_issues(
            hierarchy, {}, "org/repo", output_dir=tmp_path
        )
        assert (tmp_path / "manifest.json").exists()
        assert len(manifest) == 1


class TestCreateIssuesTemplateRendering:
    def test_template_rendering_for_all_levels(self):
        """Ensure template-based rendering works for all 5 levels."""
        # Clear template cache to force fresh load
        create_issues._template_cache.clear()
        for level in ("scope", "initiative", "epic", "story", "task"):
            item = {
                "title": f"Test {level}",
                "description": f"Desc for {level}",
                "priority": "P0",
                "size": "M",
                "parent_ref": "Parent Item",
                "blocking": [],
            }
            body = create_issues.generate_body(item, level)
            assert len(body) > 100
            assert "I Know I Am Done When" in body

    def test_template_renders_parent_ref_for_epic(self):
        create_issues._template_cache.clear()
        item = {
            "title": "My Epic",
            "description": "",
            "priority": "P0",
            "size": "M",
            "parent_ref": "Core Initiative",
        }
        body = create_issues.generate_body(item, "epic")
        assert "Core Initiative" in body

    def test_template_renders_parent_ref_for_task(self):
        create_issues._template_cache.clear()
        item = {
            "title": "My Task",
            "description": "",
            "priority": "P0",
            "size": "XS",
            "parent_ref": "Parent Story",
        }
        body = create_issues.generate_body(item, "task")
        assert "Parent Story" in body
