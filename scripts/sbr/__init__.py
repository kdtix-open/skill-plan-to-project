"""Sprint Backlog Review (SBR) — Voice-first, LLM-driven backlog review.

See `docs/plans/kdtix-format/Sprint Backlog Review (KDTIX-format).md` in
`kdtix-open/agent-project-queue` for the full plan.

Stage 1 MVP (this package):
    api        — canonical business logic (SessionManager, IssueWalker,
                 SubsectionReviewer, LLMPromptBuilder, WriteBacker).
    cli        — `sbr` shell wrapper, thin argparse around api.
    mcp_server — `sbr-mcp-server`, exposes api as 10 canonical MCP tools.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
