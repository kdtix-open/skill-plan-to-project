"""Tests for scripts/sbr/api.py — SBR Stage 1 MVP core."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.sbr import api

# ---------------------------------------------------------------------------
# SubsectionReviewer — iteration order + content extraction
# ---------------------------------------------------------------------------


class TestSubsectionReviewerOrdering:
    def test_scope_level_returns_ordered_subsections(self):
        body = (
            "Vision paragraph.\n\n"
            "#### Business Problem\nLegacy path broken.\n\n"
            "#### Success Criteria\n- A\n- B\n\n"
            "#### I Know I Am Done When\n- complete\n"
        )
        subs = api.SubsectionReviewer.ordered_subsections("scope", body)
        keys = [s.key for s in subs]
        assert keys[0] == "vision"
        # business_problem should precede success_criteria
        assert keys.index("business_problem") < keys.index("success_criteria")
        # done_when should be last
        assert keys[-1] == "done_when"

    def test_story_level_iteration_order(self):
        body = "#### TL;DR\nSummary.\n"
        subs = api.SubsectionReviewer.ordered_subsections("story", body)
        keys = [s.key for s in subs]
        assert "user_story" in keys
        assert "acceptance_criteria" in keys
        # user_story before tldr in template order
        assert keys.index("user_story") < keys.index("tldr")

    def test_empty_body_returns_stubs_with_blank_content(self):
        subs = api.SubsectionReviewer.ordered_subsections("scope", "")
        for s in subs:
            assert s.original_content == ""
            assert s.verdict == "pending"


# ---------------------------------------------------------------------------
# SessionManager — start, load, verdict, pause/resume, atomic persistence
# ---------------------------------------------------------------------------


class TestSessionManager:
    def _walker_stub(self, *args, **kwargs):
        return [
            {
                "number": 100,
                "title": "Project Scope: Test",
                "level": "scope",
                "parent_number": None,
            },
            {
                "number": 101,
                "title": "Story: Test Story",
                "level": "story",
                "parent_number": 100,
            },
        ]

    def test_start_creates_session_on_disk(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with patch.object(
            api, "_walk_existing_hierarchy", side_effect=self._walker_stub
        ):
            session = mgr.start(100, "owner/repo")
        session_path = tmp_path / f"{session.session_id}.json"
        assert session_path.is_file()
        assert len(session.issues) == 2

    def test_load_round_trips_session_state(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with patch.object(
            api, "_walk_existing_hierarchy", side_effect=self._walker_stub
        ):
            original = mgr.start(100, "owner/repo")
        loaded = mgr.load(original.session_id)
        assert loaded.session_id == original.session_id
        assert loaded.scope_issue_number == original.scope_issue_number
        assert len(loaded.issues) == len(original.issues)

    def test_load_missing_session_raises(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            mgr.load("does-not-exist")

    def test_skip_issues_filters_walker_output(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with patch.object(
            api, "_walk_existing_hierarchy", side_effect=self._walker_stub
        ):
            session = mgr.start(100, "owner/repo", skip_issues={101})
        numbers = [i.number for i in session.issues]
        assert 101 not in numbers
        assert 100 in numbers

    def test_apply_verdict_approved_advances_cursor(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(
                api, "get_issue_body", return_value="#### Business Problem\nx\n"
            ),
        ):
            session = mgr.start(100, "owner/repo")
            mgr.apply_verdict(session, "approved")
        assert session.current_subsection_index == 1

    def test_apply_verdict_improved_stores_content(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(
                api, "get_issue_body", return_value="#### Business Problem\nx\n"
            ),
        ):
            session = mgr.start(100, "owner/repo")
            mgr.apply_verdict(session, "improved", improved_content="NEW TEXT")
        sub = session.issues[0].subsections[0]
        assert sub.verdict == "improved"
        assert sub.approved_content == "NEW TEXT"

    def test_pause_then_resume_round_trips(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with patch.object(
            api, "_walk_existing_hierarchy", side_effect=self._walker_stub
        ):
            session = mgr.start(100, "owner/repo")
        mgr.pause(session)
        assert session.status == "paused"
        mgr.resume_session(session)
        assert session.status == "active"

    def test_session_completes_when_all_subsections_verdicted(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api,
                "_walk_existing_hierarchy",
                return_value=[
                    {
                        "number": 100,
                        "title": "Scope",
                        "level": "scope",
                        "parent_number": None,
                    }
                ],
            ),
            patch.object(
                api,
                "get_issue_body",
                return_value="Just leading text.\n",
            ),
        ):
            session = mgr.start(100, "owner/repo")
            # Approve every subsection
            for _ in range(100):
                pair = mgr.get_current_subsection(session)
                if pair is None:
                    break
                mgr.apply_verdict(session, "approved")
        assert session.status == "completed"

    def test_atomic_write_survives_corruption_scenario(self, tmp_path):
        """If the write is interrupted, the .tmp file shouldn't clobber the real
        session file on load.  We simulate by checking that the file exists +
        parses after each verdict."""
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(
                api, "get_issue_body", return_value="#### Business Problem\nx\n"
            ),
        ):
            session = mgr.start(100, "owner/repo")
            mgr.apply_verdict(session, "approved")
        # Reload succeeds + matches
        loaded = mgr.load(session.session_id)
        assert loaded.current_subsection_index == 1

    def test_go_back_decrements_cursor(self, tmp_path):
        """Regression for 2026-04-23 UAT: operator wanted to revisit
        a subsection after verdicting but there was no way back."""
        mgr = api.SessionManager(sessions_dir=tmp_path)
        body = "#### Business Problem\nx\n\n#### Success Criteria\n- A\n"
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(api, "get_issue_body", return_value=body),
        ):
            session = mgr.start(100, "owner/repo")
            mgr.apply_verdict(session, "approved")
            idx_before = session.current_subsection_index
            assert idx_before > 0
            mgr.go_back(session)
            assert session.current_subsection_index == idx_before - 1

    def test_goto_jumps_to_specified_issue(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(api, "get_issue_body", return_value="x"),
        ):
            session = mgr.start(100, "owner/repo")
            assert mgr.goto(session, issue_number=101) is True
            assert session.issues[session.current_issue_index].number == 101

    def test_goto_returns_false_for_unknown_issue(self, tmp_path):
        mgr = api.SessionManager(sessions_dir=tmp_path)
        with (
            patch.object(
                api, "_walk_existing_hierarchy", side_effect=self._walker_stub
            ),
            patch.object(api, "get_issue_body", return_value=""),
        ):
            session = mgr.start(100, "owner/repo")
            assert mgr.goto(session, issue_number=9999) is False


# ---------------------------------------------------------------------------
# SubsectionReviewer table-fallback — fixes MoSCoW empty-dict bug
# ---------------------------------------------------------------------------


class TestSubsectionTableFallback:
    def test_empty_moscow_dict_falls_back_to_raw_markdown(self):
        """Regression for 2026-04-23 UAT: MoSCoW section came back as
        '{}' (2 chars) because the structured parser couldn't decode
        the markdown table.  Now falls back to raw text."""
        body = (
            "#### MoSCoW Classification\n"
            "| Priority | Item |\n"
            "|---|---|\n"
            "| Must Have | A |\n"
            "| Must Have | B |\n"
            "| Should Have | C |\n\n"
            "#### Done When\nx\n"
        )
        subs = api.SubsectionReviewer.ordered_subsections("scope", body)
        moscow = next((s for s in subs if s.key == "moscow"), None)
        assert moscow is not None
        # If the structured parse was empty, the fallback should return
        # the raw markdown table text (much longer than "{}").
        if moscow.original_content in ("{}", "[]"):
            raise AssertionError(
                f"MoSCoW fell through to useless content: {moscow.original_content!r}"
            )
        assert (
            len(moscow.original_content) > 10
        ), f"MoSCoW content too short: {moscow.original_content!r}"
        # Raw markdown should still be recognizable
        assert (
            "Must Have" in moscow.original_content
            or "must" in moscow.original_content.lower()
        )


# ---------------------------------------------------------------------------
# LLMPromptBuilder — prompt scaffolding
# ---------------------------------------------------------------------------


class TestLLMPromptBuilder:
    def test_summary_prompt_mentions_subsection_key_and_level(self):
        prompt = api.LLMPromptBuilder.build_summary_prompt(
            "story", "why_this_matters", "some content"
        )
        assert "why_this_matters" in prompt
        assert "story" in prompt
        assert "some content" in prompt
        assert "voice" in prompt.lower() or "narration" in prompt.lower()

    def test_improvement_prompt_includes_rag_when_provided(self):
        prompt = api.LLMPromptBuilder.build_improvement_prompt(
            "scope",
            "business_problem",
            "old content",
            rag_context="prior approved content",
        )
        assert "prior approved content" in prompt
        assert "Relevant prior approvals" in prompt

    def test_improvement_prompt_without_rag_omits_block(self):
        prompt = api.LLMPromptBuilder.build_improvement_prompt(
            "scope", "business_problem", "old content"
        )
        assert "Relevant prior approvals" not in prompt


# ---------------------------------------------------------------------------
# WriteBacker — mocked gh issue edit round trip
# ---------------------------------------------------------------------------


class TestWriteBacker:
    def test_write_back_issue_uses_approved_content(self, tmp_path):
        from scripts.sbr.api import IssueReview, Session, SubsectionReview

        session = Session(
            session_id="s1",
            scope_issue_number=100,
            repo="owner/repo",
            created_at="2026-04-22T00:00:00Z",
        )
        issue = IssueReview(
            number=101,
            title="Story: Test",
            level="story",
            subsections=[
                SubsectionReview(
                    key="user_story",
                    verdict="improved",
                    original_content="old As-a block",
                    approved_content=(
                        "As a developer,\nI want coverage,\nSo that tests catch drift."
                    ),
                ),
                SubsectionReview(
                    key="tldr",
                    verdict="approved",
                    original_content="Summary one-liner.",
                    approved_content="Summary one-liner.",
                ),
            ],
        )
        session.issues = [issue]

        with (
            patch.object(
                api, "get_issue_body", return_value="# User Story: Test\n\nOld body."
            ),
            patch.object(api, "update_issue_body") as upd,
        ):
            result = api.WriteBacker.write_back_issue(session, issue)

        # update_issue_body called with repo + issue number + some new body
        assert upd.called
        args, _ = upd.call_args
        assert args[0] == "owner/repo"
        assert args[1] == 101
        new_body = args[2]
        # Improved content appears in the rendered body
        assert "As a developer" in new_body
        # WriteBacker returns diff summary
        assert result["issue_number"] == 101
        assert issue.write_back_completed is True
