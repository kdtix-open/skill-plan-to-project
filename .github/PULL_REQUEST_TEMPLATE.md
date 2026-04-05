## Summary
<!-- Describe the change in 2-5 sentences. For bug fixes, include the user-visible problem and the root cause. -->

## Why This Change
<!-- Link the issue/ticket/spec and explain why this work matters now. -->

- Issue / ticket:
- Problem statement:
- Success criteria:

## Scope
<!-- Use this to make the agreed scope explicit and prevent PR drift. -->

### In Scope
-

### Out of Scope
-

## MoSCoW
<!-- Start from "Won't" and justify promotions. Keep Must-have scope honest. -->

| Priority | Items |
|---|---|
| Must Have | |
| Should Have | |
| Could Have | |
| Won't Have (this time) | |

## Skill Workflow Impact
<!-- Check every workflow phase or artifact touched by this PR. -->

- [ ] `create_issues.py preflight`
- [ ] `create_issues.py parse`
- [ ] `create_issues.py create`
- [ ] `set_relationships.py`
- [ ] `set_project_fields.py`
- [ ] `compliance_check.py`
- [ ] `queue_order.py`
- [ ] Issue body templates in `assets/`
- [ ] Reference docs in `references/`
- [ ] Skill metadata / docs (`SKILL.md`, `agents/openai.yaml`)

## Baseline
<!-- Record the starting state before changes. If there were known failures, document them clearly. -->

- Branch / commit:
- Environment:
- Tests before changes:
- Lint / format before changes:
- Security scan before changes:
- Known baseline failures or skips:

## TDD and Test Coverage
<!-- Bug fixes must start with a failing regression test. New functionality follows Red -> Green -> Refactor. -->

- [ ] Tests were written before implementation
- [ ] This bug fix started with a failing regression test
- [ ] All baseline tests still pass
- [ ] New or changed behavior is covered by tests
- [ ] Coverage delta from baseline is documented below

### Coverage Delta
- Baseline coverage:
- Current coverage:
- Delta:

## Verification
<!-- Include the exact commands and the important outputs. -->

### Automated Checks
- [ ] Full test suite
- [ ] Lint / format
- [ ] Type checks (if applicable)
- [ ] Security scan (if applicable)

```text
# Commands run
```

### UAT
<!-- At least one realistic scenario is required for user-facing changes. -->

| Goal | Prerequisites | Steps | Expected Result | Actual Result | Status |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## Security
<!-- Security is part of implementation, not a follow-up task. -->

- [ ] Inputs are validated and sanitized at system boundaries
- [ ] No secrets, credentials, or connection strings were added to source control
- [ ] Safe data-access / command-execution patterns are used
- [ ] Least-privilege permissions were used
- [ ] No new high or critical vulnerabilities remain
- Security notes / trade-offs:

## Documentation
- [ ] Code-adjacent docs updated (`README`, `SKILL.md`, `references/`, templates) as needed
- [ ] Public behavior / CLI changes documented
- [ ] Examples or screenshots updated when behavior changed

## Independent Verification
<!-- Prefer a reviewer or tester who did not author the feature, ideally in a different environment. -->

- Reviewer / tester:
- Environment:
- Result:
- Follow-up findings:

## Reviewer Focus
<!-- Call out the areas where fresh eyes are most valuable. -->

-

## Risks and Rollback
- Risks:
- Rollback plan:

## Pre-Commit Checklist
<!-- This mirrors the repository constitution and must be true before merge. -->

- [ ] Build passes with zero warnings
- [ ] Full test suite: 100% pass rate, no regressions vs. recorded baseline
- [ ] No hardcoded secrets or credentials
- [ ] No debug or diagnostic code left in production paths
- [ ] Security: safe data-access patterns, input validated at all boundaries
- [ ] Coverage delta from baseline documented in this PR
- [ ] Pre-commit hooks were not bypassed
