# Compliance Rules Reference

Rules used by `compliance-check.py` to classify and auto-fix issue body gaps.

## Gap Severity Levels

| Level | Action | Description |
|-------|--------|-------------|
| P0 | Auto-fix | Critical missing section; compliance-check.py adds it automatically |
| P1 | Report only | Important section missing; flagged for human review |
| P2 | Report only | Nice-to-have section missing; low priority |

## P0 Rules (Auto-Fixed)

### P0-1: Missing TDD Language

**Condition:** Issue body does not contain the TDD sentinel phrase in "I Know I Am Done When":
```
TDD followed: failing test written BEFORE implementation
```

**Auto-fix:** Append the following line to the "I Know I Am Done When" section:
```markdown
- [ ] TDD followed: failing test written BEFORE implementation (Red phase confirmed before writing any production code)
```

**Applies to:** All 5 hierarchy levels.

### P0-2: Missing Security/Compliance Section (Mutation Issues)

**Condition:** Issue title or body contains mutation keywords (`create`, `update`, `delete`,
`resolve`, `write`, `set`, `build`, `implement`) AND the body does not contain a
`## Security/Compliance` or `### Security/Compliance` section header.

**Auto-fix:** Append to end of body:
```markdown

### Security/Compliance

- [ ] Input validated before use
- [ ] No secrets committed to source
- [ ] Least-privilege gh CLI scopes used
```

**Applies to:** Epic, Story, Task levels.

### P0-3: Missing Dependency Table (Blocked Issues)

**Condition:** Issue has a `blocked` label AND the body does not contain a
`### Dependencies` or `## Dependencies` section.

**Auto-fix:** Append to end of body:
```markdown

### Dependencies

| Ticket | Description | Status |
|--------|-------------|--------|
| [BLOCKER] | [Add blocking issue reference] | Open |
```

**Applies to:** All levels.

## P1 Rules (Report Only)

| Rule | Condition | Section Expected |
|------|-----------|-----------------|
| P1-1 | No Assumptions section | `## Assumptions` or `### Assumptions` |
| P1-2 | No MoSCoW table | `## MoSCoW` or `### MoSCoW Classification` |
| P1-3 | No Subtasks Needed section | `### Subtasks Needed` (Story/Epic only) |
| P1-4 | No Implementation Options | `### Implementation Options` (Task/Story only) |

## P2 Rules (Report Only)

| Rule | Condition | Section Expected |
|------|-----------|-----------------|
| P2-1 | No "Release Value" section | `### Release Value` (Initiative/Epic only) |
| P2-2 | No "Why This Matters" | `### Why This Matters` (Story only) |
| P2-3 | No "TL;DR" | `### TL;DR` (Story only) |

## Compliance Report Format

`compliance-report.json`:

```json
{
  "summary": {
    "total_issues": 18,
    "p0_fixed": 3,
    "p1_gaps": 2,
    "p2_gaps": 5
  },
  "issues": [
    {
      "number": 9,
      "title": "Story: Build create-issues.py",
      "gaps": [
        { "severity": "P0", "rule": "P0-1", "description": "Missing TDD language", "fixed": true },
        { "severity": "P1", "rule": "P1-1", "description": "Missing Assumptions section", "fixed": false }
      ]
    }
  ]
}
```

## Section Detection Patterns

Use case-insensitive regex to detect section headers:

```python
import re

TDD_SENTINEL = r"TDD followed.*failing test"
SECURITY_HEADER = r"^#{1,4}\s+Security"
DEPENDENCIES_HEADER = r"^#{1,4}\s+Dependenc"
ASSUMPTIONS_HEADER = r"^#{1,4}\s+Assumptions"
MOSCOW_HEADER = r"^#{1,4}\s+MoSCoW"
SUBTASKS_HEADER = r"^#{1,4}\s+Subtasks"
IMPL_OPTIONS_HEADER = r"^#{1,4}\s+Implementation\s+Options"
RELEASE_VALUE_HEADER = r"^#{1,4}\s+Release\s+Value"
WHY_MATTERS_HEADER = r"^#{1,4}\s+Why\s+This\s+Matters"
TLDR_HEADER = r"^#{1,4}\s+TL;?DR"

MUTATION_KEYWORDS = re.compile(
    r"\b(create|update|delete|resolve|write|set|build|implement)\b",
    re.IGNORECASE,
)
```
