"""Tests for scripts/sbr/mcp_server.py — MCP tool registration + dispatch."""

from __future__ import annotations

import pytest

from scripts.sbr import mcp_server


class TestMainGracefulFallback:
    def test_main_without_mcp_sdk_prints_remediation(self, capsys, monkeypatch):
        """When `mcp` SDK isn't installed, main() returns non-zero + prints hint."""
        monkeypatch.setattr(mcp_server, "FastMCP", None)
        # Pass explicit argv so argparse doesn't inherit pytest's sys.argv.
        rc = mcp_server.main(argv=[])
        assert rc == 2
        err = capsys.readouterr().err
        assert "mcp" in err.lower() and "install" in err.lower()


class TestNormalizeStartArgs:
    """Pure-function coverage for the alias normalizer.

    Observed-in-UAT aliases only — these map every voice-dictated variant
    we saw during 2026-04-22/23 UAT down to (scope_issue_number, repo).
    """

    def test_canonical_pair_passes_through(self):
        n, r = mcp_server._normalize_start_args(
            scope_issue_number=182, repo="kdtix-open/agent-project-queue"
        )
        assert n == 182
        assert r == "kdtix-open/agent-project-queue"

    def test_scope_id_alias_resolves_to_canonical(self):
        n, _ = mcp_server._normalize_start_args(scope_id=42, repo="o/r")
        assert n == 42

    def test_issue_number_alias_resolves_to_canonical(self):
        n, _ = mcp_server._normalize_start_args(issue_number=99, repo="o/r")
        assert n == 99

    def test_missing_scope_raises_value_error(self):
        with pytest.raises(ValueError, match="scope_issue_number is required"):
            mcp_server._normalize_start_args(repo="o/r")

    def test_split_organization_and_short_repository(self):
        """Voice models sometimes emit organization + bare repository."""
        _, r = mcp_server._normalize_start_args(
            scope_issue_number=1,
            organization="kdtix-open",
            repository="agent-project-queue",
        )
        assert r == "kdtix-open/agent-project-queue"

    def test_preslashed_repository_accepted(self):
        """Regression — 2026-04-23: model also emits already-slashed."""
        _, r = mcp_server._normalize_start_args(
            scope_issue_number=1,
            repository="kdtix-open/agent-project-queue",
        )
        assert r == "kdtix-open/agent-project-queue"

    def test_queue_name_alias(self):
        _, r = mcp_server._normalize_start_args(
            scope_issue_number=1,
            queue_name="o/r",
        )
        assert r == "o/r"

    def test_project_queue_alias(self):
        _, r = mcp_server._normalize_start_args(
            scope_issue_number=1,
            project_queue="o/r",
        )
        assert r == "o/r"

    def test_stt_caps_normalized_to_lowercase(self):
        """Regression — 2026-04-22: 'KDTIX-open/agent-project-QUE'."""
        _, r = mcp_server._normalize_start_args(
            scope_issue_number=1,
            repo="KDTIX-open/agent-project-QUEUE",
        )
        assert r == "kdtix-open/agent-project-queue"

    def test_no_repo_returns_none_for_downstream_validator(self):
        _, r = mcp_server._normalize_start_args(scope_issue_number=1)
        assert r is None


@pytest.mark.skipif(
    mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
)
class TestBuildServerRegistersTools:
    def test_build_server_registers_expected_tools(self):
        """FastMCP server exposes the canonical SBR tools + Phase 1/2
        Investigation Sub-Agent tools after build."""
        server = mcp_server._build_server()
        tool_names: set[str] = set()
        try:
            import asyncio

            tools = asyncio.run(server._tool_manager.list_tools())
            tool_names = {t.name for t in tools}
        except (AttributeError, Exception):
            tm = getattr(server, "_tool_manager", None)
            if tm is not None:
                tool_names = set(getattr(tm, "_tools", {}).keys())
        expected = {
            # Stage 1 MVP canonical tools
            "sbr_start_session",
            "sbr_next_subsection",
            "sbr_current_subsection_verbatim",
            "sbr_approve",
            "sbr_improve",
            "sbr_skip",
            "sbr_pause",
            "sbr_resume",
            "sbr_terminate",
            "sbr_session_status",
            "sbr_write_back",
            # Investigation Sub-Agent (Phase 1 scaffolding + Phase 2 wiring)
            "sbr_review_repo",
            "sbr_review_plan",
            "sbr_research",
            "sbr_review_issues",
            "sbr_investigation_status",
            "sbr_list_investigations",
            "sbr_pending_investigations",
            "sbr_save_bookmark",
            "sbr_jump_to_bookmark",
        }
        assert expected.issubset(tool_names), f"missing tools: {expected - tool_names}"


class TestResolveInvestigationWorkingDirectory:
    """Pure-function tests for the Phase 2a working-directory resolver."""

    def test_explicit_repo_wins_over_session_repo(self, monkeypatch):
        monkeypatch.delenv("SBR_WORKING_DIRECTORY_ROOT", raising=False)
        wd = mcp_server.resolve_investigation_working_directory(
            repo="kdtix-open/other-repo",
            session_repo="kdtix-open/agent-project-queue",
        )
        assert wd == "/workspace/host-repos/other-repo"

    def test_falls_back_to_session_repo_when_arg_none(self, monkeypatch):
        monkeypatch.delenv("SBR_WORKING_DIRECTORY_ROOT", raising=False)
        wd = mcp_server.resolve_investigation_working_directory(
            repo=None, session_repo="kdtix-open/agent-project-queue"
        )
        assert wd == "/workspace/host-repos/agent-project-queue"

    def test_honors_SBR_WORKING_DIRECTORY_ROOT_env(self, monkeypatch):
        monkeypatch.setenv("SBR_WORKING_DIRECTORY_ROOT", "/mnt/repos")
        wd = mcp_server.resolve_investigation_working_directory(
            repo=None, session_repo="owner/name"
        )
        assert wd == "/mnt/repos/name"

    def test_strips_trailing_slash_from_root(self, monkeypatch):
        monkeypatch.setenv("SBR_WORKING_DIRECTORY_ROOT", "/mnt/repos/")
        wd = mcp_server.resolve_investigation_working_directory(
            repo=None, session_repo="owner/name"
        )
        assert wd == "/mnt/repos/name"

    def test_handles_repo_without_slash(self, monkeypatch):
        monkeypatch.delenv("SBR_WORKING_DIRECTORY_ROOT", raising=False)
        wd = mcp_server.resolve_investigation_working_directory(
            repo=None, session_repo="just-a-name"
        )
        assert wd == "/workspace/host-repos/just-a-name"


@pytest.mark.skipif(
    mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
)
class TestInvestigationToolsEnabledPath:
    """When SBR_INVESTIGATIONS_ENABLED=1 + dispatcher + session infra are
    in place, the 4 primary tools dispatch + bookmark tools operate on
    session state.  These tests wire a stub dispatcher + a tmp-path
    session manager via monkeypatch so no real bridge is contacted."""

    def _call_tool(self, server, name: str, **kwargs):
        import asyncio

        return asyncio.run(server._tool_manager.call_tool(name, kwargs))

    def _bootstrap(self, tmp_path, monkeypatch):
        """Build a server with investigations enabled + a stub
        dispatcher + a real sessions dir under tmp_path."""
        monkeypatch.setenv("SBR_INVESTIGATIONS_ENABLED", "1")
        monkeypatch.setenv("SBR_BRIDGE_URL", "http://fake-bridge")
        monkeypatch.setenv("SBR_BRIDGE_TOKEN", "fake-token")
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))

        # Stub the dispatcher to avoid real HTTP.
        stub_posts: list[dict] = []

        def fake_post(url, *, json_body, headers, timeout):
            stub_posts.append({"url": url, "body": json_body, "headers": headers})
            return {
                "job_id": f"job-{len(stub_posts)}",
                "tool_kind": json_body["tool_kind"],
                "status": "ready",
                "model": "claude-sonnet-4-5-20250929",
                "finding": (
                    f"Sample finding for {json_body['tool_kind']}.\n\nSUMMARY: stubbed."
                ),
                "error": None,
                "exit_code": 0,
                "stderr_tail": "",
                "duration_ms": 42,
                "cost_usd_estimate": 0.001,
                "dispatched_at": "2026-04-24T00:00:00.000Z",
            }

        # Patch the InvestigationDispatcher to use our fake poster.
        from scripts.sbr import investigations as inv_module

        original_init = inv_module.InvestigationDispatcher.__init__

        def patched_init(self, *args, **kwargs):
            kwargs.setdefault("poster", fake_post)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(
            inv_module.InvestigationDispatcher, "__init__", patched_init
        )

        # Create + seed a session for testing.
        from scripts.sbr.api import (
            IssueReview,
            Session,
            SessionManager,
            SubsectionReview,
        )

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = Session(
            session_id="test-session",
            scope_issue_number=182,
            repo="kdtix-open/agent-project-queue",
            created_at="2026-04-24T00:00:00Z",
            issues=[
                IssueReview(
                    number=182,
                    title="Project Scope: Test",
                    level="scope",
                    subsections=[
                        SubsectionReview(
                            key="vision",
                            original_content="Vision text.",
                        ),
                        SubsectionReview(
                            key="success_criteria",
                            original_content="- C1",
                        ),
                    ],
                )
            ],
        )
        mgr._atomic_write(session)

        return self, stub_posts

    def test_review_repo_dispatches_and_persists(self, tmp_path, monkeypatch):
        _, stub_posts = self._bootstrap(tmp_path, monkeypatch)
        server = mcp_server._build_server()
        result = self._call_tool(
            server,
            "sbr_review_repo",
            session_id="test-session",
            prompt="Does the bridge support OIDC?",
        )
        serialized = str(result)
        assert "ready" in serialized
        assert "stubbed" in serialized  # from SUMMARY line
        # Bridge got the right working directory (falls back to session.repo)
        assert stub_posts[0]["body"]["working_directory"].endswith(
            "agent-project-queue"
        )
        assert stub_posts[0]["body"]["tool_kind"] == "review_repo"

    def test_research_uses_opus_default_via_bridge(self, tmp_path, monkeypatch):
        _, stub_posts = self._bootstrap(tmp_path, monkeypatch)
        server = mcp_server._build_server()
        result = self._call_tool(
            server,
            "sbr_research",
            session_id="test-session",
            prompt="frameworks for device-flow OIDC",
        )
        # Model default comes from bridge side — the Python dispatcher
        # doesn't override unless the caller explicitly sets one.
        assert "model" not in stub_posts[0]["body"] or (
            "opus" not in stub_posts[0]["body"].get("model", "")
        )
        # But the tool_kind signals "research" so bridge picks Opus default.
        assert stub_posts[0]["body"]["tool_kind"] == "research"
        assert "ready" in str(result)

    def test_save_and_jump_bookmark_round_trip(self, tmp_path, monkeypatch):
        self._bootstrap(tmp_path, monkeypatch)
        server = mcp_server._build_server()

        # Save a bookmark.
        save_result = self._call_tool(
            server,
            "sbr_save_bookmark",
            session_id="test-session",
            label="progress-1",
            reason="progress_save",
        )
        assert "progress-1" in str(save_result)
        assert "vision" in str(save_result)  # subsection_key at cursor

        # Jump to it.
        jump_result = self._call_tool(
            server,
            "sbr_jump_to_bookmark",
            session_id="test-session",
            label="progress-1",
        )
        assert "restored" in str(jump_result)

        # Non-existent label → not_found.
        miss = self._call_tool(
            server,
            "sbr_jump_to_bookmark",
            session_id="test-session",
            label="nonexistent",
        )
        assert "not_found" in str(miss)

    def test_list_and_pending_reflect_dispatched_investigations(
        self, tmp_path, monkeypatch
    ):
        self._bootstrap(tmp_path, monkeypatch)
        server = mcp_server._build_server()

        # Dispatch two investigations.
        self._call_tool(
            server,
            "sbr_review_repo",
            session_id="test-session",
            prompt="q1",
        )
        self._call_tool(
            server,
            "sbr_review_issues",
            session_id="test-session",
            prompt="q2",
        )

        list_result = self._call_tool(
            server, "sbr_list_investigations", session_id="test-session"
        )
        assert "review_repo" in str(list_result)
        assert "review_issues" in str(list_result)

        pending_result = self._call_tool(
            server, "sbr_pending_investigations", session_id="test-session"
        )
        # Both stubbed investigations came back status=ready, so both
        # are in the pending ("ready-but-unconsumed") bucket.
        assert "review_repo" in str(pending_result)


@pytest.mark.skipif(
    mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
)
class TestInvestigationToolsDisabledPath:
    """When SBR_INVESTIGATIONS_ENABLED is off, every investigation tool
    returns the dispatcher_disabled sentinel.  Covers the Phase 1 safety
    guarantee (scaffolding doesn't affect runtime until the flag flips)."""

    def _call_tool(self, server, name: str, **kwargs):
        """Invoke a registered FastMCP tool by name + unwrap its result."""
        import asyncio

        tm = server._tool_manager
        # FastMCP's call_tool returns a CallToolResult with content + isError
        return asyncio.run(tm.call_tool(name, kwargs))

    def test_all_9_investigation_tools_return_sentinel_when_disabled(self, monkeypatch):
        monkeypatch.delenv("SBR_INVESTIGATIONS_ENABLED", raising=False)
        server = mcp_server._build_server()
        # Tools that require session_id + prompt
        for tool in (
            "sbr_review_repo",
            "sbr_review_plan",
            "sbr_research",
            "sbr_review_issues",
        ):
            result = self._call_tool(server, tool, session_id="x", prompt="q")
            # FastMCP wraps the dict return — check for our sentinel text
            serialized = str(result)
            assert (
                "dispatcher_disabled" in serialized
            ), f"{tool} did not return sentinel"

        # Tools with different signatures
        for tool in (
            "sbr_list_investigations",
            "sbr_pending_investigations",
        ):
            result = self._call_tool(server, tool, session_id="x")
            assert "dispatcher_disabled" in str(result)

        # sbr_investigation_status needs job_id
        result = self._call_tool(
            server, "sbr_investigation_status", session_id="x", job_id="j"
        )
        assert "dispatcher_disabled" in str(result)

        # Bookmark tools
        result = self._call_tool(server, "sbr_save_bookmark", session_id="x", label="l")
        assert "dispatcher_disabled" in str(result)
        result = self._call_tool(
            server, "sbr_jump_to_bookmark", session_id="x", label="l"
        )
        assert "dispatcher_disabled" in str(result)
