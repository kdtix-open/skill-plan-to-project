"""Tests for scripts/refresh_subtree.py.

Focus: deterministic placeholder substitution. Network-touching code
(fetch_subtree, write_back) is covered by integration smoke tests, not
unit tests, since it requires a live GitHub repo + auth.
"""

from __future__ import annotations

import re

from scripts.refresh_subtree import _REPLACEMENTS, fill_placeholders


def test_priority_label_shell_replaced():
    body = "> **Priority**: P0 — [LABEL]"
    new, n = fill_placeholders(body)
    assert "[LABEL]" not in new
    assert "P0" in new
    assert n == 1


def test_standalone_label_replaced():
    body = "Some text [LABEL] more text"
    new, n = fill_placeholders(body)
    assert "[LABEL]" not in new
    assert "_TBD_" in new
    assert n == 1


def test_long_prose_stub_replaced():
    body = "[What becomes possible after this epic ships — 1-2 sentences]"
    new, n = fill_placeholders(body)
    assert "[What becomes" not in new
    assert "_See narrative above_" in new
    assert n == 1


def test_long_prose_stub_for_story_variant():
    body = "[Why this story is needed and what breaks without it — 2-3 sentences]"
    new, n = fill_placeholders(body)
    assert "[Why" not in new
    assert n == 1


def test_acceptance_criteria_scenario_shell():
    body = (
        "**Scenario 1**: [SCENARIO NAME]\n"
        "- **Given**: [PRECONDITION]\n"
        "- **When**: [ACTION]\n"
        "- **Then**: [EXPECTED OUTCOME]\n"
    )
    new, n = fill_placeholders(body)
    assert "[SCENARIO NAME]" not in new
    assert "[PRECONDITION]" not in new
    assert "[ACTION]" not in new
    assert "[EXPECTED OUTCOME]" not in new
    assert n == 4


def test_user_story_role_what_outcome():
    body = "As a [ROLE],\nI want [WHAT],\nSo that [OUTCOME]."
    new, n = fill_placeholders(body)
    assert "[ROLE]" not in new
    assert "[WHAT]" not in new
    assert "[OUTCOME]" not in new


def test_numbered_criterion_placeholders():
    body = (
        "- [ ] [CRITERION 1]\n"
        "- [ ] [CRITERION 2]\n"
        "- [ ] [ASSUMPTION 1]\n"
        "- [ ] [ACCEPTANCE CRITERION 1]\n"
    )
    new, n = fill_placeholders(body)
    assert "[CRITERION" not in new
    assert "[ASSUMPTION" not in new
    assert "[ACCEPTANCE CRITERION" not in new
    assert n == 4


def test_moscow_table_rows():
    body = (
        "| Priority | Item |\n"
        "|----------|------|\n"
        "| Must Have | [ITEM] |\n"
        "| Should Have | [ITEM] |\n"
        "| Could Have | [ITEM] |\n"
        "| Won't Have | [ITEM] |\n"
    )
    new, n = fill_placeholders(body)
    assert "[ITEM]" not in new
    assert n == 4


def test_feature_scope_table_row():
    body = "| 1 | [FEATURE] | [INCLUDES] | [ENABLES] |"
    new, n = fill_placeholders(body)
    assert "[FEATURE]" not in new
    assert "[INCLUDES]" not in new
    assert "[ENABLES]" not in new


def test_subtasks_needed_table_row():
    body = "| 1 | [TASK] | [PTS] | [YES/NO] |"
    new, n = fill_placeholders(body)
    assert "[TASK]" not in new
    assert "[PTS]" not in new
    assert "[YES/NO]" not in new


def test_dependency_table_with_n_placeholder():
    body = "| #[N] | _(child linkage populated after creation)_ | Backlog |"
    new, n = fill_placeholders(body)
    assert "#[N]" not in new


def test_questions_and_constraints():
    body = "- [QUESTION]\n- [CONSTRAINT]\n- [ ] [ARTIFACT]\n- [ITEM]"
    new, n = fill_placeholders(body)
    assert "[QUESTION]" not in new
    assert "[CONSTRAINT]" not in new
    assert "[ARTIFACT]" not in new
    assert "[ITEM]" not in new


def test_story_assumptions_brackets():
    body = (
        "- **Roles**: [WHO uses the output of this story]\n"
        "- **Starting point**: [Preconditions that must be true]\n"
        "- **Preconditions**: [What must exist before this story starts]\n"
    )
    new, n = fill_placeholders(body)
    assert "[WHO" not in new
    assert "[Preconditions" not in new
    assert "[What must" not in new


def test_idempotent_already_filled_body():
    body = "All placeholders are already replaced. _TBD_ everywhere."
    new, n = fill_placeholders(body)
    assert n == 0
    assert new == body


def test_empty_body_no_changes():
    new, n = fill_placeholders("")
    assert n == 0
    assert new == ""


def test_running_twice_is_stable():
    """Running the refresh twice must not create new differences on the second pass."""
    body = "> **Priority**: P0 — [LABEL]\n- [ ] [CRITERION 1]\n- [QUESTION]"
    once, _ = fill_placeholders(body)
    twice, n2 = fill_placeholders(once)
    assert n2 == 0
    assert once == twice


def test_real_initiative_body_passes_no_remaining_brackets():
    """Smoke-test against the actual #271 body shape — no `[ALL_CAPS]` remains."""
    body = """
# Initiative: Test

> **Priority**: P0 — [LABEL]
> **Initiative Owner**: TBD

## PRODUCT SECTION

### Objective

Some narrative content here.

### Release Value

[What becomes possible after this initiative ships — 1-2 sentences]

### Success Criteria

- [ ] [CRITERION 1]
- [ ] [CRITERION 2]

### Feature Scope

| # | Feature | Includes | Enables |
|---|---------|----------|---------|
| 1 | [FEATURE] | [INCLUDES] | [ENABLES] |

### Assumptions

- [ASSUMPTION 1]
- [ASSUMPTION 2]

### Dependencies

| Dep | Type | Owner | Status |
|-----|------|-------|--------|
| [DEPENDENCY] | [TYPE] | TBD | Backlog |

### Out of Scope

- [ITEM]

### Artifacts

- [ ] [ARTIFACT]

### I Know I Am Done When

- [ ] [PROJECT-SPECIFIC CRITERION]
"""
    new, _ = fill_placeholders(body)
    # No bracketed `[ALL_CAPS]` placeholder strings remain
    leftovers = re.findall(r"\[[A-Z][A-Z0-9 _\-/]*\]", new)
    assert leftovers == [], f"Unfilled placeholders: {leftovers}"


def test_preserves_authored_narrative_content():
    """The tool must NEVER strip or alter authored prose paragraphs."""
    body = (
        "Defines the `ProviderAdapter` interface (mirroring "
        "`kdtix-open/token-reporting:src/providers/registry.ts`) + "
        "`BudgetProvider` base class."
    )
    new, n = fill_placeholders(body)
    assert n == 0
    assert new == body


def test_replacement_table_has_no_duplicate_compiled_patterns():
    """No two rules use the same exact pattern (would dead-code one)."""
    seen = set()
    for pattern, _ in _REPLACEMENTS:
        assert pattern.pattern not in seen, f"Duplicate pattern: {pattern.pattern!r}"
        seen.add(pattern.pattern)
