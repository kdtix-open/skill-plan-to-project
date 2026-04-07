## Summary

This PR adds the Schrödinger Issue skill — a Copilot Coding Agent / Codex skill that retroactively creates a structured GitHub issue from local git changes (staged, unstaged, and recent commits).

## Problem

Developers often start coding before creating a tracking issue. The Schrödinger Issue skill solves this by analysing the current git diff and recent commits, then generating a well-structured issue that captures intent, experiments, outcomes, and remaining work — so the work is traceable even when the issue came after the code.

## Changes

- Adds the `schrodinger-issue` skill to `.github/skills/schrodinger-issue/`
- Skill triggers on requests like: "create an issue for my changes", "retroactively track this work", "schrodinger issue", or "generate an issue from my diff"
- Skill uses `git diff` and `git log` as inputs; creates the issue via `gh issue create`

## Testing

- [ ] Make a meaningful code change without creating a GitHub issue first
- [ ] Invoke the skill: "create a schrodinger issue for my changes"
- [ ] Verify the generated issue accurately captures intent, what was tried, outcomes, and remaining work
- [ ] Confirm the issue is created in the correct repository with appropriate labels
