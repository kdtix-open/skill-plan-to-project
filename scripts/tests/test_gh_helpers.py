"""Tests for the shared gh_helpers module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.gh_helpers import (
    AuthError,
    GitHubAPIError,
    check_auth,
    get_issue_body,
    get_issue_labels,
    graphql,
    run_gh,
    update_issue_body,
)
from scripts.tests.conftest import make_ok


class TestRunGh:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_returns_result_on_success(self, mock_run):
        mock_run.return_value = make_ok("hello")
        result = run_gh(["gh", "api", "test"])
        assert result.stdout == "hello"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_raises_on_failure(self, mock_run):
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = "not found"
        mock_run.return_value = m
        with pytest.raises(GitHubAPIError) as exc_info:
            run_gh(["gh", "api", "bad"])
        assert exc_info.value.returncode == 1
        assert "not found" in str(exc_info.value)

    @patch("scripts.gh_helpers.subprocess.run")
    def test_no_raise_when_check_false(self, mock_run):
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = "error"
        mock_run.return_value = m
        result = run_gh(["gh", "api", "bad"], check=False)
        assert result.returncode == 1

    @patch("time.sleep")
    @patch("scripts.gh_helpers.subprocess.run")
    def test_retries_on_rate_limit(self, mock_run, mock_sleep):
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""
        fail.stderr = "rate limit exceeded"

        success = make_ok("ok")
        mock_run.side_effect = [fail, success]

        result = run_gh(["gh", "api", "test"], retries=3)
        assert result.stdout == "ok"
        assert mock_sleep.call_count == 1

    @patch("scripts.gh_helpers.subprocess.run")
    def test_no_retry_on_non_transient_error(self, mock_run):
        fail = MagicMock()
        fail.returncode = 1
        fail.stdout = ""
        fail.stderr = "not found"
        mock_run.return_value = fail

        with pytest.raises(GitHubAPIError):
            run_gh(["gh", "api", "bad"], retries=3)
        # Should only call once — no retries for non-transient errors
        assert mock_run.call_count == 1


class TestCheckAuth:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_passes_when_authenticated(self, mock_run):
        mock_run.return_value = make_ok()
        check_auth()  # Should not raise

    @patch("scripts.gh_helpers.subprocess.run")
    def test_raises_when_not_authenticated(self, mock_run):
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = "not logged in"
        mock_run.return_value = m
        with pytest.raises(AuthError):
            check_auth()


class TestGraphql:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        mock_run.return_value = make_ok(json.dumps({"data": {"test": True}}))
        result = graphql("query { test }", {})
        assert result["data"]["test"] is True


class TestIssueHelpers:
    @patch("scripts.gh_helpers.subprocess.run")
    def test_get_issue_body(self, mock_run):
        mock_run.return_value = make_ok("body content")
        body = get_issue_body("org/repo", 1)
        assert body == "body content"

    @patch("scripts.gh_helpers.subprocess.run")
    def test_get_issue_labels(self, mock_run):
        mock_run.return_value = make_ok('["bug", "P0"]')
        labels = get_issue_labels("org/repo", 1)
        assert labels == ["bug", "P0"]

    @patch("scripts.gh_helpers.subprocess.run")
    def test_update_issue_body(self, mock_run):
        mock_run.return_value = make_ok()
        update_issue_body("org/repo", 1, "new body")
        assert mock_run.called
        # Verify --body-file was used
        call_args = mock_run.call_args[0][0]
        assert "--body-file" in call_args
