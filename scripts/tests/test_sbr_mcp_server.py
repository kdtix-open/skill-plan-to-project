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
