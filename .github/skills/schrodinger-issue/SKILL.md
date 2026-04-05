---
name: schrodinger-issue
description: >
  Retroactively create a GitHub issue from current local git changes—staged,
  unstaged, and recent commits. Use when a developer has made meaningful progress
  without opening an issue first and wants to generate a structured, traceable
  issue that captures intent, experiments, outcomes, and remaining work.
  Triggers on requests like: "create an issue for my changes", "retroactively
  track this work", "schrodinger issue", or "generate an issue from my diff".
---

# Schrödinger Issue

Analyze the current git changes (diffs, staged + unstaged, recent commits, branch
name) and generate a structured GitHub issue that retroactively tracks the
work—making it appear as if the issue always existed.

> *"An issue that doesn't exist until observed, then appears as if it always did."*

## Prerequisites

- `gh` CLI authenticated (`gh auth status`). If not, ask the user to run
  `gh auth login` first.
- Must be inside a git repository.

## Workflow

### 1. Gather context

Run the bundled script to collect all inputs:

```bash
python "<path-to-skill>/scripts/create_issue.py" --dry-run
```

This prints a JSON summary of:
- `branch`: current branch name
- `staged_diff`: staged changes
- `unstaged_diff`: unstaged changes
- `recent_commits`: last N commit messages + diffs (default 10)
- `changed_files`: list of modified/added/deleted paths

### 2. Analyze and generate issue content

Using the gathered context, infer:

- **Intent (Why)** — What problem was being solved? What triggered this change?
- **Exploration (What was tried)** — POCs, failed approaches, discarded paths.
- **Outcome (What worked)** — Final solution, key decisions.
- **Gaps (What remains)** — Incomplete work, follow-ups, risks.

Produce a structured issue body:

```markdown
## 🧠 Summary
<High-level description>

## 🎯 Problem / Motivation
<Why this work was needed>

## 🧪 What Was Tried
- Attempt 1
- Attempt 2

## ✅ What Worked
- Final approach
- Key decisions

## 📦 Changes Made
- File/module summaries

## ⚠️ Remaining Work
- [ ] Task 1

## 🧾 Notes / Learnings
- Insights and tradeoffs

## 🔗 Traceability
- Branch: `<branch>`
- Related commits: <shas>
```

### 3. Create the issue

Write the generated body to `/tmp/schrodinger-issue-body.md`, then run:

```bash
python "<path-to-skill>/scripts/create_issue.py" \
  --title "<inferred-title>" \
  --body-file /tmp/schrodinger-issue-body.md \
  [--label bug|enhancement|refactor] \
  [--commits 10]
```

The script creates the GitHub issue via `gh issue create` and prints the issue
URL and number.

### 4. Return issue reference

Report the issue URL to the user and suggest referencing it in commits:

```
git commit --amend -m "fix: <description> (closes #<N>)"
```

## Notes

- `--dry-run` collects context without creating an issue—useful for reviewing
  what will be submitted.
- Labels are optional; suggest appropriate ones based on the change type.
- If working on a PR branch, also offer to update the PR body with `Closes #N`.
