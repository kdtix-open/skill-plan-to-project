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
        """FastMCP server exposes the 10 canonical SBR tools after build."""
        server = mcp_server._build_server()
        # FastMCP stores tools internally; introspect via the registered names.
        # The exact attribute can vary by SDK version; try a few known paths.
        tool_names: set[str] = set()
        # FastMCP uses _tool_manager.list_tools() in newer versions
        try:
            import asyncio

            tools = asyncio.run(server._tool_manager.list_tools())
            tool_names = {t.name for t in tools}
        except (AttributeError, Exception):
            # Fallback — inspect the tools list directly
            tm = getattr(server, "_tool_manager", None)
            if tm is not None:
                tool_names = set(getattr(tm, "_tools", {}).keys())
        expected = {
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
        }
        # At least the 10 canonical tools should be registered
        assert expected.issubset(tool_names), f"missing tools: {expected - tool_names}"
