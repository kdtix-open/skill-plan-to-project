# Sub-Agent Fan-Out Pattern

When a backlog is large enough that a single agent cannot hold the full plan
context comfortably, fan the authoring work out across multiple scoped
sub-agents — one per Epic. This page documents when to use that pattern, how
to isolate each sub-agent, and how to combine the results.

## When to Fan Out

Use per-Epic sub-agents when the backlog exceeds:

- **> 1 Epic** in the scope, AND
- **> 5 Stories per Epic** on average

As a concrete reference point: the Slice 2 refresh against
`kdtix-open/agent-project-queue` (#182) covered 76 issues — 1 Project Scope,
1 Initiative, 6 Epics, and 50+ Stories. A single agent attempting all 50
Stories in one prompt stream-timed-out after 17–36 minutes with 0 issue
writes. Splitting into 6 per-Epic sub-agents (one per Epic, ~8 Stories each)
completed the same 50 Stories across two ~20-minute parallel rounds with no
timeouts and richer output.

Below that threshold (e.g. 1 Epic × 4 Stories) a single-agent run is fine.

## Per-Layer Scoping Rules

| Layer | Typical sub-agent count | Context each sub-agent receives |
|---|---|---|
| **Project Scope** | 1 (rarely fan out) | The Scope body only; Initiative summaries |
| **Initiative** | 1 per Initiative | Initiative body + Epic title list |
| **Epic** | 1 per Epic | Epic body + child Story titles + sibling Epic **summaries** (NOT full sibling Epic bodies) |
| **Story** | Rarely needed | Reserve for very large Stories with deep technical scope (e.g. RFC compliance); only when per-Epic fan-out still leaves individual Stories too large to author in one pass |

The key principle: each sub-agent sees **one Epic's worth of context**. Giving
a sub-agent the full bodies of sibling Epics wastes context budget and risks
cross-Epic contamination in edits.

## Worktree Isolation Pattern

Each sub-agent works in its own git worktree so parallel runs do not
interfere with each other.

**Convention (proven across PRs #567–#579 on `kdtix-open/agent-project-queue`):**

```
Worktree path:  /tmp/sdlca-{epic-id}-{purpose}/
Branch name:    plan/{epic-id}-{purpose}
```

Examples from the Slice 2 fan-out:

```
/tmp/sdlca-ep-024-why-done-ac/   →   plan/ep-024-why-done-ac
/tmp/sdlca-ep-025-why-done-ac/   →   plan/ep-025-why-done-ac
...
/tmp/sdlca-ep-006-why-done-ac/   →   plan/ep-006-why-done-ac
```

**Per-sub-agent constraints:**

- Edit **one file only** per sub-agent run (the plan markdown). **Do NOT**
  edit GitHub issue bodies directly — that's the anti-pattern this doc
  replaces (see "Anti-Pattern" section below).
- One PR per sub-agent. Do not batch multiple Epics into one PR.
- The sub-agent must not create, rename, or delete any file outside its
  designated worktree path.

This gives the operator a clean per-Epic diff to review before merging.

**Prior art:** Round 1 (PRs #567–#572 + #573) authored `Why This Matters` /
`I Know I Am Done When` / `Acceptance Criteria` per Story. Round 2
(PRs #574–#579) authored `User Story` / `MoSCoW Classification` / `Assumptions`
per Story. Both rounds ran in parallel — 6 sub-agents at a time — with no
branch conflicts because each sub-agent owned a disjoint set of issue numbers
(its Epic's children).

## Tight Scoping Rules

Every sub-agent prompt MUST include all five of the following:

1. **Exact Epic identifier + title** — e.g.
   `Epic EP-003: "Bridge Session Auth & Recovery"`.

2. **List of OPEN child Stories** — include issue numbers. Explicitly
   exclude closed siblings by number:
   ```
   Open Stories under EP-003: #210, #211, #212, #213
   Closed (skip): #209, #214
   ```

3. **Exact subsections to author** — do not leave it to the sub-agent to
   decide. Name them explicitly:
   ```
   Author ONLY these subsections for each open Story above:
   - Why This Matters
   - I Know I Am Done When
   - Acceptance Criteria
   ```

4. **Scope guard** — a literal "do not touch" line with the other Epic
   identifiers named explicitly:
   ```
   Do NOT touch any Story or Epic OUTSIDE EP-003.
   Do not edit: EP-001, EP-002, EP-004, EP-005, EP-006.
   ```

5. **Quality benchmark anchor** — point the sub-agent to a concrete
   reference issue to match (see next section).

## Quality Benchmark Anchor

Sub-agents produce noticeably better output when given a reference Story to
match rather than an abstract rubric. Include a link to 1–2 well-authored
Stories in the prompt.

The operator's Stories #266–#268 in `kdtix-open/agent-project-queue` are the
canonical R-10/R-11/R-12 quality benchmarks for this skill's output:

```
Quality standard: match the depth and specificity of the following reference
Stories from the same repo:
  - #266 — Story: Self-Heal R-10 — Bridge credential allow-list must accept generic GITHUB_TOKEN / GH_TOKEN
  - #267 — Story: Self-Heal R-11 — Worker auth-probe must use git ls-remote ground-truth
  - #268 — Story: Self-Heal R-12 — Bridge restart must drain in-flight worker runs
```

Replace the placeholders with the actual issue titles when you build the
prompt. The sub-agent can fetch those bodies with:

```bash
gh issue view 266 --repo kdtix-open/agent-project-queue --json body --jq .body
```

Using a concrete reference eliminates the most common failure mode: a
sub-agent that writes structurally correct but one-line, context-free
subsection values.

## Anti-Pattern: Monolithic Single-Agent Runs

> **Warning:** Do NOT attempt to author all Stories for all Epics in one
> agent prompt when the backlog exceeds the thresholds above.

A single agent attempting 50+ Stories in one pass will:

- **Exceed context budgets.** Each Story requires plan prose, existing issue
  body, and per-subsection output. At ~8 Stories per Epic × 6 Epics, the
  working context easily exceeds 100 K tokens before the agent begins writing.

- **Stream-timed-out.** The prior session's first attempt at direct issue-body
  editing timed out after 17–36 minutes per agent with 0 issue writes. Long
  chains of tool calls consume context faster than they produce durable output.

- **Produce shallow output even when it succeeds.** A model holding 50 Stories
  in its working context writes shorter, less Epic-specific Acceptance Criteria
  than a model holding only 8 Stories from one Epic. The Epic body is the
  richest source of quality signal — spreading it thin across 50 Stories
  dilutes that signal.

The per-Epic fan-out pattern avoids all three failure modes because each
sub-agent sees ~5–10 Stories of context plus its parent Epic — a prompt that
fits comfortably, stays focused on Epic-level objectives, and produces an
auditable diff.

## Compose-and-Merge Strategy

Each sub-agent opens one PR. The operator reviews and merges them
sequentially (or in small groups). No special merge sequencing is required
when the PRs touch non-overlapping issue numbers:

- Round 1 PRs (#567–#572) each owned one Epic's child Stories. Those Stories
  live in distinct line ranges of the plan markdown. Git handles
  non-overlapping additions cleanly — no merge conflicts.
- Round 2 PRs (#574–#579) added different subsections to the same Stories.
  Because each sub-agent appended new subsections to existing Story sections
  in the plan markdown rather than replacing them, the diffs were again
  non-overlapping.

**Ordering rule:** merge PRs in any order WHEN their line ranges do not
overlap. If two sub-agent PRs do touch the same lines (e.g. two agents
both authored the same subsection by mistake), merge the first, then
rebase or resolve the second before merging.

For teams wanting to verify merge safety before merging sequentially,
`git merge-tree` can simulate the merge (the modern 2-argument form returns
the merged tree or exits non-zero on conflicts):

```bash
# Check if pr-branch-2 merges cleanly into main (exit 0 = no conflicts)
git merge-tree main pr-branch-2

# Or check if the two branches would conflict with each other when both merged into main
git merge-tree pr-branch-1 pr-branch-2
```

See the [git merge-tree documentation](https://git-scm.com/docs/git-merge-tree)
for the full reference.

**After all PRs from a round are merged:** run the refresh + compliance flow
to verify no placeholders leaked through:

```bash
python3 -m scripts.create_issues refresh \
  --plan PLAN_FILE --repo REPO --scope-issue SCOPE_NUMBER --dry-run
python3 -m scripts.compliance_check --manifest manifest.json --repo REPO
```
