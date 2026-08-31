# CLAUDE.md — skill-plan-to-project contributor context

## Environment setup

```bash
pip install -e ".[dev]"                      # required deps (incl. mcp) + pytest/ruff
```

`mcp` is a **required** project dependency (`scripts/sbr/mcp_server.py`), not an
optional/dev-only extra — install it via the command above, not ad hoc. If it's
missing, `scripts/tests/test_sbr_mcp_http.py` fails/skips in ways that can look
like a pre-existing, unrelated failure; treat a missing `mcp` import as an
incomplete environment, not an expected non-regression. `pyproject.toml` pins
`mcp>=1.0,<2` — `mcp` 2.x renamed `mcp.server.fastmcp.FastMCP` to
`mcp.server.mcpserver.MCPServer` and isn't yet compatible with `mcp_server.py`.

## Build / test commands

```bash
python3 -m pytest scripts/tests/ -x          # unit test suite (fast, no network)
python3 -m pytest scripts/tests/ -x -v       # verbose output
```

No compile step. All scripts are pure Python 3 — no build required.

## Key conventions

- **No third-party deps beyond stdlib + PyJWT + cryptography.** Do not add `requests`,
  `httpx`, or other HTTP libraries. Use `subprocess` + `gh` CLI for all GitHub API calls.
- **`--body-file` always.** Never pass multi-line markdown as a shell argument.
- **TDD: Red before Green.** Write a failing test before implementing any function.
  The test suite is in `scripts/tests/`.
- **Subsection schema gate (FR #45)** is the primary quality guard for plan authoring.
  See `references/plan-format.md` for the required-subsection table and escape hatch.

## Sub-agent fan-out pattern

When operators author large backlogs (>1 Epic × >5 Stories per Epic), a single agent
runs into context budget limits and stream timeouts. The proven approach is per-Epic
sub-agent fan-out: one sub-agent per Epic, each in its own git worktree, opening one PR.

See `references/sub-agent-fan-out-pattern.md` for the full operator workflow — including
worktree isolation conventions, tight scoping rules, quality benchmark anchors, and the
compose-and-merge strategy. Concrete prior art: PRs #567–#579 on
`kdtix-open/agent-project-queue` (Slice 2 refresh, 50 Stories across 6 Epics, two rounds).
