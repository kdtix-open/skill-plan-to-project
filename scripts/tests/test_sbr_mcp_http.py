"""Tests for scripts/sbr/mcp_server.py HTTP/SSE transport (Story #393).

TDD Red-first: these tests define the contract before the implementation
lands.  They cover:

- `--transport streamable-http` CLI arg dispatches to the right FastMCP mode
- `--auth-token` is required when running HTTP transport (for Bearer-token auth)
- `BearerTokenVerifier` rejects missing/invalid tokens and accepts matching ones
- `main()` exits non-zero with a clear remediation hint if auth is misconfigured

Live-server integration tests (spinning up uvicorn + curling endpoints) are
intentionally NOT in this file — those belong in a slower integration suite
to keep the unit-test loop fast.  This file asserts wiring + routing only.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.sbr import mcp_server

# ---------------------------------------------------------------------------
# Argument parsing — stdio stays default; --transport streamable-http opt-in
# ---------------------------------------------------------------------------


class TestCliTransportArg:
    def test_default_transport_is_stdio(self, monkeypatch):
        """No --transport flag → runs in stdio mode (preserves Claude App path)."""
        fake_mcp = MagicMock()
        monkeypatch.setattr(mcp_server, "_build_server", lambda **kw: fake_mcp)
        monkeypatch.setattr("sys.argv", ["sbr-mcp-server"])

        rc = mcp_server.main()

        assert rc == 0
        fake_mcp.run.assert_called_once()
        call = fake_mcp.run.call_args
        # transport kwarg either omitted (default stdio) or explicitly "stdio"
        transport = call.kwargs.get("transport", "stdio")
        assert transport == "stdio"

    def test_transport_streamable_http_runs_http_mode(self, monkeypatch):
        """`--transport streamable-http --auth-token X` runs HTTP mode."""
        fake_mcp = MagicMock()
        monkeypatch.setattr(mcp_server, "_build_server", lambda **kwargs: fake_mcp)
        monkeypatch.setattr(
            "sys.argv",
            [
                "sbr-mcp-server",
                "--transport",
                "streamable-http",
                "--port",
                "3456",
                "--auth-token",
                "test-token-xxx",
            ],
        )

        rc = mcp_server.main()

        assert rc == 0
        fake_mcp.run.assert_called_once()
        call = fake_mcp.run.call_args
        assert call.kwargs.get("transport") == "streamable-http"

    def test_http_transport_without_auth_token_exits_nonzero(self, monkeypatch, capsys):
        """HTTP transport without `--auth-token` exits 2 with remediation hint."""
        monkeypatch.setattr(
            "sys.argv",
            ["sbr-mcp-server", "--transport", "streamable-http", "--port", "3456"],
        )

        rc = mcp_server.main()

        assert rc == 2
        err = capsys.readouterr().err
        assert "auth-token" in err.lower() or "token" in err.lower()
        assert "required" in err.lower() or "missing" in err.lower()

    def test_sse_transport_also_requires_auth_token(self, monkeypatch, capsys):
        """SSE transport (alternative HTTP mode) also requires --auth-token."""
        monkeypatch.setattr(
            "sys.argv",
            ["sbr-mcp-server", "--transport", "sse", "--port", "3456"],
        )

        rc = mcp_server.main()

        assert rc == 2
        err = capsys.readouterr().err
        assert "token" in err.lower()

    def test_stdio_transport_ignores_auth_token(self, monkeypatch):
        """stdio transport works without --auth-token (local Claude App trust)."""
        fake_mcp = MagicMock()
        monkeypatch.setattr(mcp_server, "_build_server", lambda **kw: fake_mcp)
        monkeypatch.setattr("sys.argv", ["sbr-mcp-server", "--transport", "stdio"])

        rc = mcp_server.main()

        assert rc == 0
        fake_mcp.run.assert_called_once()


# ---------------------------------------------------------------------------
# BearerTokenVerifier — async verify_token contract per MCP SDK TokenVerifier
# ---------------------------------------------------------------------------


class TestBearerTokenVerifier:
    def test_matching_token_returns_access_token(self):
        verifier = mcp_server.BearerTokenVerifier(expected_token="ghs_abc123")

        result = asyncio.run(verifier.verify_token("ghs_abc123"))

        assert result is not None
        assert result.token == "ghs_abc123"
        assert result.client_id == "sbr-hosted-consumer"
        assert "sbr:review" in result.scopes

    def test_mismatched_token_returns_none(self):
        verifier = mcp_server.BearerTokenVerifier(expected_token="ghs_abc123")

        result = asyncio.run(verifier.verify_token("ghs_different"))

        assert result is None

    def test_empty_token_returns_none(self):
        verifier = mcp_server.BearerTokenVerifier(expected_token="ghs_abc123")

        result = asyncio.run(verifier.verify_token(""))

        assert result is None

    def test_none_expected_token_rejects_everything(self):
        """Defense-in-depth: if expected_token is empty, reject all requests."""
        verifier = mcp_server.BearerTokenVerifier(expected_token="")

        result = asyncio.run(verifier.verify_token("anything"))

        assert result is None

    def test_constant_time_comparison_used(self):
        """Verifier uses hmac.compare_digest (or equivalent) — not ==.

        We can't easily observe timing, but we can check the implementation
        source for the constant-time primitive.
        """
        import inspect

        source = inspect.getsource(mcp_server.BearerTokenVerifier)
        assert (
            "compare_digest" in source or "hmac" in source
        ), "BearerTokenVerifier should use hmac.compare_digest for timing safety"


# ---------------------------------------------------------------------------
# _build_server wiring — token_verifier plumbed when auth_token provided
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
)
class TestSbrStartSessionValidation:
    """Regression guards for the 2026-04-22 Stage 1.5 first-use miss.

    The voice model called sbr_start_session with repo='kdtix-open' (bare
    org, no slash).  The server accepted it + silently returned
    queue_size=0 because the walker couldn't enumerate a nonexistent
    repo.  Result: operator stuck with a useless session, no clear error.

    These tests lock the new fail-fast + warn-on-empty behavior.
    """

    def _get_tool_fn(self, server: Any, name: str) -> Any:
        """Return the raw Python fn registered for a tool name.  FastMCP
        wraps it in a Tool object but `.fn` is the underlying callable."""
        tm = getattr(server, "_tool_manager", None)
        # In this SDK version list_tools() is synchronous; internal
        # storage is `_tools` dict keyed by name.
        tools_dict = getattr(tm, "_tools", None)
        if tools_dict is None:
            raise AssertionError("_tool_manager._tools not present")
        tool = tools_dict.get(name)
        if tool is None:
            raise AssertionError(f"tool {name} not registered")
        return tool.fn

    def test_rejects_bare_org_without_slash(self):
        server = mcp_server._build_server()
        fn = self._get_tool_fn(server, "sbr_start_session")
        with pytest.raises(ValueError, match="missing slash separator"):
            fn(scope_issue_number=182, repo="kdtix-open")

    def test_rejects_repo_with_whitespace(self):
        """Operator's voice transcription often produces
        'kdtix-open agent-project-queue' (space instead of slash).
        Must reject + suggest the slash form."""
        server = mcp_server._build_server()
        fn = self._get_tool_fn(server, "sbr_start_session")
        with pytest.raises(ValueError) as exc_info:
            fn(
                scope_issue_number=182,
                repo="kdtix-open agent-project-queue",
            )
        msg = str(exc_info.value)
        assert "Invalid repo format" in msg
        # Error message MUST suggest the correct format
        assert "kdtix-open/agent-project-queue" in msg

    def test_rejects_repo_with_slash_and_whitespace(self):
        """Edge case: 'kdtix-open /agent' — has slash BUT also space.
        The whitespace branch of validation must catch this."""
        server = mcp_server._build_server()
        fn = self._get_tool_fn(server, "sbr_start_session")
        with pytest.raises(ValueError, match="contains whitespace"):
            fn(
                scope_issue_number=182,
                repo="kdtix-open /agent-project-queue",
            )


class TestBuildServerAuthWiring:
    @pytest.mark.skipif(
        mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
    )
    def test_build_server_without_auth_uses_no_verifier(self):
        """Without auth_token → stdio-only path → FastMCP gets no token_verifier."""
        server = mcp_server._build_server()
        # FastMCP stores the auth provider internally; absence means stdio is safe
        provider = getattr(server, "_auth_server_provider", None)
        # stdio path: no verifier required
        assert provider is None

    @pytest.mark.skipif(
        mcp_server.FastMCP is None, reason="mcp SDK not installed in this env"
    )
    def test_build_server_with_auth_token_installs_verifier(self):
        """With auth_token → _build_server passes a BearerTokenVerifier to FastMCP."""
        with patch.object(mcp_server, "FastMCP") as fake_fastmcp_cls:
            fake_fastmcp_cls.return_value = MagicMock()
            _ = mcp_server._build_server(auth_token="ghs_test_xyz")

            fake_fastmcp_cls.assert_called_once()
            kwargs = fake_fastmcp_cls.call_args.kwargs
            verifier = kwargs.get("token_verifier")
            assert verifier is not None
            assert isinstance(verifier, mcp_server.BearerTokenVerifier)
            assert verifier.expected_token == "ghs_test_xyz"
