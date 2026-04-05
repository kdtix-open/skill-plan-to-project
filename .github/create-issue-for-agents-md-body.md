## Summary

This PR adds GitHub Copilot layered instruction files and a guide library bootstrap to the repository.

## Changes

- Adds `.github/copilot-instructions.md` — the project constitution and coding standards consumed by GitHub Copilot and Codex agents at the start of every session
- Adds `.github/docs/` — a curated guide library covering the 9-phase development workflow, TDD, security, UAT, MoSCoW prioritization, and more
- Adds `.github/skills/` — reusable, self-contained skill packages for Copilot Coding Agent and Codex

## Why

GitHub Copilot and Codex agents read `.github/copilot-instructions.md` automatically. Without this file, agents have no project context, coding standards, or workflow guidance — leading to inconsistent, lower-quality output. The guide library and skills extend this with deep, referenceable process knowledge.

## Testing

- [ ] Verify `.github/copilot-instructions.md` is present and valid Markdown
- [ ] Confirm guide library is present at `.github/docs/`
- [ ] Open a Copilot Chat session and confirm the project constitution is applied to agent responses
- [ ] Run the skill-installer to confirm skills can be installed from this repo
