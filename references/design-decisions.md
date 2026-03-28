# Design Decisions

Key design choices made when building the plan-to-project skill.

## Language: Python 3 + gh CLI (not raw GraphQL/REST)

**Decision:** All scripts use Python 3 with `subprocess` calls to `gh` CLI.

**Why:** `gh` handles OAuth token management, retry logic, and provides a consistent
interface across environments. Raw `curl` requires managing tokens explicitly. PowerShell
is not available on all target environments. Python + gh is available everywhere KDTIX
runs Copilot/Codex.

## Issue Body Injection: `--body-file` (not inline string)

**Decision:** Write body to a temp file and pass via `gh issue create --body-file`.

**Why:** Markdown issue bodies contain backticks, pipes, double quotes, and newlines.
Passing them as shell arguments causes silent truncation, escaping errors, or injection.
`--body-file` is deterministic regardless of body content.

## Sub-Issue API Key: databaseId (integer) via `-F`

**Decision:** Sub-issue REST API calls use `databaseId` (the integer ID from the REST
API, e.g., `12345678`) passed with `-F` flag (typed value), not `-f` (string value).

**Why:** The sub-issues API requires an integer `sub_issue_id`. Passing the string form
produces `422 Unprocessable Entity`. `nodeId` (the GraphQL string like `I_kwDO...`) is
rejected. The `-F` flag in `gh api` sends the value as a JSON integer; `-f` sends a string.

## Creation Order: Top-Down (Scope First)

**Decision:** Issues are created in strict top-down order: Scope → Initiative →
Epics → Stories → Tasks.

**Why:** Sub-issue relationships must be set after both parent and child exist.
Creating top-down ensures every parent exists before any of its children are linked.
Creating bottom-up would require a second pass to link all relationships.

## Manifest JSON: Three IDs Per Issue

**Decision:** `manifest.json` stores `number` (int), `nodeId` (string), and
`databaseId` (int) for every created issue.

**Why:** Different operations need different ID types:
- `number` — used for labels, body updates, and human-readable references
- `nodeId` — used for GraphQL mutations (project field values, Issue Types)
- `databaseId` — used for sub-issues REST API

Storing all three avoids extra API calls in downstream scripts.

## TDD: Red Before Green

**Decision:** Every function has a failing unit test written before any implementation.

**Why:** Follows the KDTIX engineering standard from `modular-engineering-guides`.
Catching bugs at the unit-test level is 10× cheaper than catching them in integration.
The Red phase also forces clear specification of expected behavior before coding begins.

## Compliance Auto-Fix: Append, Never Replace

**Decision:** P0 auto-fixes append to existing body content; they never replace or
truncate existing sections.

**Why:** Issue bodies may contain information added after creation. Replacing a section
would lose that context. Appending is always safe; a human can clean up later.

## Rate Limiting: 0.5s Sleep Between Creates

**Decision:** `create-issues.py` sleeps 0.5s between `gh issue create` calls.

**Why:** GitHub's secondary rate limit triggers on bursts of identical operations.
0.5s is sufficient to avoid the limit for backlogs up to 50 issues without adding
meaningful wall-clock time.
