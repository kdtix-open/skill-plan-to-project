"""SBR MCP Server — exposes the SBR API as MCP tools.

Primary operator interface for Stage 1 MVP.  Claude App (with Voice),
Claude CLI, Codex, Cursor, and VS Code all drive review via the same
tool surface.

10 canonical tools mirror the CLI subcommands:
  sbr_start_session      / sbr_status
  sbr_next_subsection    / sbr_current_subsection_verbatim
  sbr_approve            / sbr_improve             / sbr_skip
  sbr_pause              / sbr_resume              / sbr_write_back
  sbr_terminate

Uses the `mcp` Python SDK (https://github.com/modelcontextprotocol/python-sdk).
If `mcp` is not available at import time, the module prints a clear
remediation hint + exits when `main()` is called.
"""

from __future__ import annotations

import sys
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover — import-time graceful failure
    FastMCP = None  # type: ignore[assignment]

from scripts.sbr.api import SessionManager, WriteBacker


def _build_server() -> FastMCP:  # type: ignore[name-defined]
    """Construct the FastMCP server + register the 10 canonical tools.

    Delegates every tool to `scripts.sbr.api` primitives; no business logic
    lives in the MCP layer.  Sticky-session support uses the same
    `~/.sbr/current-session.txt` file as the CLI.
    """
    if FastMCP is None:
        raise RuntimeError(
            "The `mcp` Python SDK is not installed.\n"
            "  pip install mcp  (see https://github.com/modelcontextprotocol/python-sdk)"
        )

    mcp = FastMCP("sbr")
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


def main() -> int:
    """Entry point for the `sbr-mcp-server` console script.

    Starts the FastMCP server in stdio mode (the default expected by
    Claude App + most MCP clients).  HTTP transport can be added in
    Stage 2 if needed.
    """
    try:
        mcp = _build_server()
    except RuntimeError as exc:
        print(f"[sbr-mcp-server] {exc}", file=sys.stderr)
        return 2
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
