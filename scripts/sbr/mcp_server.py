"""SBR MCP Server — exposes the SBR API as MCP tools.

Primary operator interface for Stage 1 MVP.  Claude App (with Voice),
Claude CLI, Codex, Cursor, and VS Code all drive review via the same
tool surface.

11 canonical tools mirror the CLI subcommands:
  sbr_start_session      / sbr_session_status
  sbr_next_subsection    / sbr_current_subsection_verbatim
  sbr_approve            / sbr_improve             / sbr_skip
  sbr_pause              / sbr_resume              / sbr_write_back
  sbr_terminate

Uses the `mcp` Python SDK (https://github.com/modelcontextprotocol/python-sdk).
If `mcp` is not available at import time, the module prints a clear
remediation hint + exits when `main()` is called.

## Transports (Story #393 — Stage 1.5 Voice Pilot)

- `stdio` (default): Claude App / Claude CLI local path; no auth needed (local
  trust).  `sbr-mcp-server` with no flags runs in this mode.
- `streamable-http`: hosted consumption (web browser calling from
  `dev.projectit.ai/tools/sbr`).  Requires `--auth-token` (Bearer).  Uses
  FastMCP's streamable-http transport on `/mcp` by default.
- `sse`: older SSE transport; also requires `--auth-token`.  Kept for
  backwards compatibility with MCP clients that don't yet support
  streamable-http.

Bearer-token auth uses `hmac.compare_digest` for constant-time comparison to
prevent timing side-channels on token validation.  The expected token is
typically a GitHub App installation token (1-hour TTL, rotated by cron);
`sbr-mcp-server` itself doesn't mint tokens — upstream infra (cron +
`mint_app_token.py`) is responsible for populating the env var that
`--auth-token` reads.
"""

from __future__ import annotations

import argparse
import hmac
import sys
from typing import Any

try:
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover — import-time graceful failure
    AccessToken = None  # type: ignore[assignment, misc]
    AuthSettings = None  # type: ignore[assignment, misc]
    FastMCP = None  # type: ignore[assignment]
    TokenVerifier = object  # type: ignore[assignment, misc]

from scripts.sbr.api import SessionManager, WriteBacker


class BearerTokenVerifier:
    """Constant-time Bearer token verifier for hosted HTTP/SSE transport.

    Implements the MCP SDK's `TokenVerifier` protocol.  Only accepts tokens
    that match `expected_token` exactly (compared via `hmac.compare_digest`
    to prevent timing side-channels).

    An empty `expected_token` rejects every request — defense-in-depth so a
    misconfigured deployment doesn't accidentally open an anonymous
    endpoint.
    """

    def __init__(self, expected_token: str) -> None:
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> Any:
        """Protocol: return AccessToken on success, None on failure."""
        if not self.expected_token:
            return None
        if not token:
            return None
        if not hmac.compare_digest(token, self.expected_token):
            return None
        if AccessToken is None:  # pragma: no cover — stdio-only install
            return None
        return AccessToken(
            token=token,
            client_id="sbr-hosted-consumer",
            scopes=["sbr:review"],
            expires_at=None,
            resource=None,
        )


def _build_server(
    auth_token: str | None = None,
    resource_server_url: str = "http://127.0.0.1:3456/",
) -> FastMCP:  # type: ignore[name-defined]
    """Construct the FastMCP server + register the 11 canonical tools.

    Delegates every tool to `scripts.sbr.api` primitives; no business logic
    lives in the MCP layer.  Sticky-session support uses the same
    `~/.sbr/current-session.txt` file as the CLI.

    Args:
        auth_token: If provided, installs a BearerTokenVerifier that accepts
            only this exact token for hosted HTTP/SSE transport.  None for
            stdio (local trust; no auth).
        resource_server_url: Public URL of this MCP server — included in
            AuthSettings for OAuth protected-resource metadata.  Defaults
            to localhost for local dev; override to the hosted URL in
            production (e.g. "https://dev.projectit.ai/mcp/sbr/").
    """
    if FastMCP is None:
        raise RuntimeError(
            "The `mcp` Python SDK is not installed.\n"
            "  pip install mcp  (see https://github.com/modelcontextprotocol/python-sdk)"
        )

    kwargs: dict[str, Any] = {}
    if auth_token:
        kwargs["token_verifier"] = BearerTokenVerifier(expected_token=auth_token)
        # FastMCP requires AuthSettings when a token_verifier is present.
        # For simple Bearer flows (non-OAuth), we pass placeholder URLs —
        # no OAuth discovery / DCR is performed because we don't provide an
        # auth_server_provider.  The resource_server_url matches this
        # server's public URL so clients know where the resource lives.
        kwargs["auth"] = AuthSettings(
            issuer_url=resource_server_url,
            resource_server_url=resource_server_url,
            required_scopes=["sbr:review"],
        )

    mcp = FastMCP("sbr", **kwargs)
    mgr = SessionManager()

    @mcp.tool()
    def sbr_start_session(
        scope_issue_number: int, repo: str, skip_issues: list[int] | None = None
    ) -> dict[str, Any]:
        """Start a new Sprint Backlog Review session rooted at a Project Scope issue.

        Args:
            scope_issue_number: The Project Scope issue number in the repo (e.g. 357).
            repo: owner/name (e.g. "kdtix-open/agent-project-queue").
            skip_issues: Optional list of issue numbers to exclude from review.

        Returns:
            {session_id, queue_size, scope_issue_number, repo}
        """
        session = mgr.start(
            scope_issue_number, repo, skip_issues=set(skip_issues or set())
        )
        return {
            "session_id": session.session_id,
            "queue_size": len(session.issues),
            "scope_issue_number": session.scope_issue_number,
            "repo": session.repo,
            "status": session.status,
        }

    @mcp.tool()
    def sbr_next_subsection(session_id: str) -> dict[str, Any]:
        """Advance to the next pending subsection in the session.

        Returns a summary describing the current issue + subsection + whether
        content is present.  When the session has no more pending subsections,
        `has_next` is False.
        """
        session = mgr.load(session_id)
        pair = mgr.get_current_subsection(session)
        mgr._atomic_write(session)  # persist lazy-populated subsections
        if pair is None:
            return {
                "has_next": False,
                "status": session.status,
                "message": (
                    "Session complete.  Run sbr_write_back to commit approved "
                    "verdicts, or sbr_terminate to discard."
                ),
            }
        issue, sub = pair
        return {
            "has_next": True,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_level": issue.level,
            "subsection_key": sub.key,
            "has_content": bool(sub.original_content.strip()),
            "content_length": len(sub.original_content),
        }

    @mcp.tool()
    def sbr_current_subsection_verbatim(session_id: str) -> dict[str, Any]:
        """Return the verbatim (unsummarized) content of the current subsection.

        Use when the operator asks to hear the original text instead of an
        LLM-summarized version.
        """
        session = mgr.load(session_id)
        pair = mgr.get_current_subsection(session)
        if pair is None:
            return {"has_current": False, "content": ""}
        _issue, sub = pair
        return {
            "has_current": True,
            "subsection_key": sub.key,
            "content": sub.original_content,
        }

    @mcp.tool()
    def sbr_approve(session_id: str) -> dict[str, Any]:
        """Approve the current subsection as-is + advance."""
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "approved")
        return {"status": "approved", "session_status": session.status}

    @mcp.tool()
    def sbr_improve(session_id: str, new_content: str) -> dict[str, Any]:
        """Replace the current subsection with improved content + advance."""
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "improved", improved_content=new_content)
        return {
            "status": "improved",
            "session_status": session.status,
            "content_length": len(new_content),
        }

    @mcp.tool()
    def sbr_skip(session_id: str) -> dict[str, Any]:
        """Skip the current subsection (leave as-is) + advance."""
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "skipped")
        return {"status": "skipped", "session_status": session.status}

    @mcp.tool()
    def sbr_pause(session_id: str) -> dict[str, Any]:
        """Pause the session (preserves cursor for resume)."""
        session = mgr.load(session_id)
        mgr.pause(session)
        return {"status": "paused", "session_status": session.status}

    @mcp.tool()
    def sbr_resume(session_id: str) -> dict[str, Any]:
        """Resume a paused session."""
        session = mgr.load(session_id)
        mgr.resume_session(session)
        return {"status": "active", "session_status": session.status}

    @mcp.tool()
    def sbr_terminate(session_id: str) -> dict[str, Any]:
        """Terminate a session without write-back (discards verdicts)."""
        session = mgr.load(session_id)
        mgr.terminate(session)
        return {"status": "terminated", "session_status": session.status}

    @mcp.tool()
    def sbr_session_status(session_id: str) -> dict[str, Any]:
        """Return the session's current progress snapshot."""
        session = mgr.load(session_id)
        approved = sum(i.approved_count for i in session.issues)
        improved = sum(i.improved_count for i in session.issues)
        skipped = sum(i.skipped_count for i in session.issues)
        return {
            "session_id": session.session_id,
            "status": session.status,
            "current_issue_index": session.current_issue_index,
            "total_issues": len(session.issues),
            "approved": approved,
            "improved": improved,
            "skipped": skipped,
        }

    @mcp.tool()
    def sbr_write_back(session_id: str) -> dict[str, Any]:
        """Commit approved verdicts for all completed issues in the session."""
        session = mgr.load(session_id)
        results: list[dict[str, Any]] = []
        for issue in session.issues:
            if issue.write_back_completed:
                continue
            if not any(
                s.verdict in ("approved", "improved") for s in issue.subsections
            ):
                continue
            results.append(WriteBacker.write_back_issue(session, issue))
        mgr._atomic_write(session)
        return {"write_back_count": len(results), "results": results}

    return mcp


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbr-mcp-server",
        description=(
            "SBR MCP server — stdio (default, for Claude App) or "
            "streamable-http/sse (for hosted consumers like "
            "dev.projectit.ai/tools/sbr)."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "streamable-http", "sse"),
        help=(
            "Transport mode.  stdio = Claude App + local CLIs (default).  "
            "streamable-http = hosted web consumers.  sse = legacy HTTP "
            "streaming.  HTTP modes require --auth-token."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3456,
        help="Port for HTTP/SSE transports (ignored for stdio).  Default: 3456.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind address for HTTP/SSE transports (default: 127.0.0.1 — "
            "bind 0.0.0.0 only behind a reverse proxy)."
        ),
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help=(
            "Bearer token for HTTP/SSE transports.  Typically a GitHub App "
            "installation token rotated by cron.  REQUIRED for HTTP/SSE; "
            "ignored (and unused) for stdio."
        ),
    )
    parser.add_argument(
        "--mount-path",
        default="/",
        help="Mount path for HTTP/SSE transports.  Default: /",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `sbr-mcp-server` console script.

    Parses CLI args + dispatches to the correct FastMCP transport.  stdio
    runs locally for Claude App; streamable-http + sse serve hosted
    consumers with Bearer-token auth.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # HTTP/SSE modes MUST have an auth token — refuse to open an anonymous
    # network endpoint by mistake.
    if args.transport in ("streamable-http", "sse") and not args.auth_token:
        print(
            f"[sbr-mcp-server] --auth-token is required for --transport "
            f"{args.transport}.\n"
            f"  Missing: mint an App installation token + pass it explicitly.\n"
            f"  Example: sbr-mcp-server --transport streamable-http "
            f"--auth-token $GH_TOKEN",
            file=sys.stderr,
        )
        return 2

    try:
        auth_token = args.auth_token if args.transport != "stdio" else None
        mcp = _build_server(auth_token=auth_token)
    except RuntimeError as exc:
        print(f"[sbr-mcp-server] {exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # FastMCP reads host/port/mount_path from init kwargs for HTTP modes;
        # _build_server doesn't set them, so we override at run-time via the
        # FastMCP settings object.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        if args.mount_path != "/":
            mcp.settings.mount_path = args.mount_path
        mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
