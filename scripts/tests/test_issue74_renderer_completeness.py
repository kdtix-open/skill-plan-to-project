"""RED-phase tests for issue #74 — restore #34 placeholder completeness and
prevent subsection folding.

Covers the four root causes from the issue:
  1. `[LABEL]` replacement ordering (composite Priority token replaced after
     its substring, leaving `P0 — [LABEL]`)
  2. Initiative/Epic child-linkage table tokens (`#[N]`, `[PTS]`, `[HRS]`,
     `[DEPS]`) with no renderer, plus the Scope Initiatives row `#[N]`
  3. P0-4 scanner missing bare `[N]` (regex required 2+ chars in brackets)
  4. Subsection folding: Scope Dependencies / Security/Compliance / Artifacts,
     MoSCoW `Won't Have This Time` group alias, Epic + Story Artifacts

Plus the end-to-end regression against the 50-item persona-governance plan
snapshot from `kdtix-open/agent-project-queue` PR #2002 (the reproduction plan
cited in the issue): zero placeholders and no authored subsection lost.

No network, no gh CLI — pure parse/render.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from scripts.compliance_check import (
    PLACEHOLDER_DESCRIPTIVE_RE,
    PLACEHOLDER_RE,
    check_issue,
)
from scripts.create_issues import (
    _parse_subsections,
    generate_body,
    parse_plan,
)

FIXTURES = Path(__file__).parent / "fixtures"
PERSONA_PLAN = FIXTURES / "plan-pr2002-persona-governance.md"


# ---------------------------------------------------------------------------
# Root cause 1: [LABEL] replacement ordering
# ---------------------------------------------------------------------------


class TestPriorityLabelComposite:
    """`[P0/P1/P2] — [LABEL]` must be replaced before its `[P0/P1/P2]`
    substring so the composite token never degrades to `P0 — [LABEL]`."""

    @pytest.mark.parametrize("level", ["initiative", "epic"])
    def test_priority_line_has_no_label_leak(self, level: str) -> None:
        item = {
            "title": "Sample",
            "description": "#### Objective\n\nWhy this exists.",
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, level, {})
        assert "[LABEL]" not in body
        assert "P0 — [LABEL]" not in body
        assert "> **Priority**: P0" in body

    @pytest.mark.parametrize("priority", ["P0", "P1", "P2"])
    def test_all_priorities_render_clean(self, priority: str) -> None:
        item = {
            "title": "Sample",
            "description": "#### Objective\n\nWhy this exists.",
            "priority": priority,
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "epic", {})
        assert "[LABEL]" not in body
        assert f"> **Priority**: {priority}" in body


# ---------------------------------------------------------------------------
# Root cause 2: child-linkage table tokens with no renderer
# ---------------------------------------------------------------------------


class TestChildLinkageRows:
    """The Initiative `Epics` table, Epic `User Stories` table, and Scope
    `Initiatives` table carry sample rows whose tokens (`#[N]`, `[PTS]`,
    `[HRS]`, `[DEPS]`) no downstream code expands — they must be neutralized
    at render time, never leaked."""

    CHILD_ROW_TOKENS = ("#[N]", "[PTS]", "[HRS]", "[DEPS]")

    def test_initiative_epics_row_neutralized(self) -> None:
        item = {
            "title": "Core Initiative",
            "description": "#### Objective\n\nWhy this exists.",
            "priority": "P0",
            "size": "L",
            "blocking": [],
        }
        body = generate_body(item, "initiative", {})
        for token in self.CHILD_ROW_TOKENS:
            assert token not in body, f"leaked {token}"

    def test_epic_user_stories_row_neutralized(self) -> None:
        item = {
            "title": "First Epic",
            "description": "#### Objective\n\nWhy this exists.",
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "epic", {})
        for token in self.CHILD_ROW_TOKENS:
            assert token not in body, f"leaked {token}"

    def test_scope_initiatives_row_neutralized(self) -> None:
        item = {
            "title": "Test Project",
            "description": "#### Vision\n\nThe end state.",
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "scope", {})
        assert "#[N]" not in body
        # The scope's own title must not be injected into the child sample row
        # (the old `[TITLE]` global replacement put it there).
        assert "| 1 | #[N] Test Project |" not in body

    def test_story_keeps_parent_and_deps_paths_working(self) -> None:
        """Regression guard: neutralizing child rows must not break the
        Story renderer paths that legitimately consume `#[N]` template rows
        (Parent Epic line, Dependencies table row)."""
        item = {
            "title": "Build the widget",
            "description": textwrap.dedent(
                """\
                #### TL;DR

                One-liner.

                #### Dependencies

                - #207 — Access-token proactive renewal (Open)
                """
            ),
            "priority": "P1",
            "size": "S",
            "blocking": [],
            "parent_ref": "First Epic",
        }
        body = generate_body(item, "story", {})
        assert "#[N]" not in body
        assert "| #207 | Access-token proactive renewal | Open |" in body


# ---------------------------------------------------------------------------
# Root cause 3: P0-4 scanner misses bare [N]
# ---------------------------------------------------------------------------


class TestP04BareNDetection:
    def test_placeholder_re_matches_bare_n(self) -> None:
        assert PLACEHOLDER_RE.search("see issue #[N] for details")
        assert PLACEHOLDER_RE.search("| 1 | #[N] | Title |")

    def test_checkbox_markers_still_allowed(self) -> None:
        for line in ("- [ ] open item", "- [x] done item", "- [X] done item"):
            assert not PLACEHOLDER_RE.search(line), f"false positive on {line!r}"

    def test_check_issue_flags_bare_n(self) -> None:
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "## Epics\n\n| 1 | #[N] | Sample |\n"
        )
        gaps = check_issue(1, "Sample", body, "initiative")
        p04 = [g for g in gaps if g["rule"] == "P0-4"]
        assert p04, "expected a P0-4 gap for bare #[N]"
        assert "[N]" in p04[0]["placeholders"]

    def test_check_issue_checkboxes_produce_no_p04(self) -> None:
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "- [ ] criterion one\n- [x] criterion two\n- [X] criterion three\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        assert not [g for g in gaps if g["rule"] == "P0-4"]


# ---------------------------------------------------------------------------
# Root cause 4a: Scope-level Dependencies / Security/Compliance / Artifacts
# ---------------------------------------------------------------------------


SCOPE_WITH_EXTRAS = textwrap.dedent(
    """\
    #### Vision

    The end state.

    #### MoSCoW

    - **Must Have**:
      - Core parsing

    #### Dependencies

    - Platform team sign-off

    #### Security/Compliance

    - No secrets in plan text.

    #### Artifacts

    - Updated operator runbook

    #### I Know I Am Done When

    - All initiatives closed
    """
)


class TestScopeSubsectionAliases:
    def test_scope_extras_parse_into_own_keys(self) -> None:
        subs = _parse_subsections(SCOPE_WITH_EXTRAS, "scope")
        assert subs.get("dependencies"), "Dependencies must parse explicitly"
        assert subs.get("security_compliance"), "Security/Compliance must parse"
        assert subs.get("artifacts") == ["Updated operator runbook"]
        # Nothing folded into MoSCoW (the previously-preceding recognized key)
        assert "Platform team sign-off" not in str(subs.get("moscow", ""))
        assert "Artifacts" not in str(subs.get("moscow", ""))

    def test_scope_extras_render_as_sections(self) -> None:
        item = {
            "title": "Test Project",
            "description": SCOPE_WITH_EXTRAS,
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "scope", {})
        assert "## Dependencies" in body
        assert "Platform team sign-off" in body
        assert "## Security/Compliance" in body
        assert "No secrets in plan text." in body
        assert "## Artifacts" in body
        assert "- [ ] Updated operator runbook" in body
        # Sections must land before the Done When section for readability
        done_idx = body.index("## I Know I Am Done When")
        for heading in ("## Dependencies", "## Security/Compliance", "## Artifacts"):
            assert body.index(heading) < done_idx, f"{heading} after Done When"


# ---------------------------------------------------------------------------
# Root cause 4b: MoSCoW `Won't Have This Time` + table-form passthrough
# ---------------------------------------------------------------------------


class TestMoscowGroupAliases:
    @pytest.mark.parametrize(
        "group_line",
        [
            "- **Won't Have This Time**:",
            "- **Wont Have This Time**:",
            "- **Won’t Have This Time**:",  # curly apostrophe
            "- **Won’t Have**:",
        ],
    )
    def test_wont_have_variants_map_to_wont_have(self, group_line: str) -> None:
        body = textwrap.dedent(
            f"""\
            #### MoSCoW

            - **Must Have**:
              - Item A

            {group_line}
              - Item E
            """
        )
        subs = _parse_subsections(body, "scope")
        moscow = subs.get("moscow")
        assert isinstance(moscow, dict)
        assert moscow.get("must_have") == ["Item A"]
        assert moscow.get("wont_have") == [
            "Item E"
        ], f"bullets under {group_line!r} must land in wont_have, got {moscow}"

    def test_story_moscow_table_passthrough(self) -> None:
        """A Story MoSCoW authored as a markdown table (the persona plan's
        dominant form) must be preserved in the rendered body — not elided."""
        item = {
            "title": "Ratify Guide Publication",
            "description": textwrap.dedent(
                """\
                #### TL;DR

                One-liner.

                #### MoSCoW

                | Priority | Scope |
                |---|---|
                | Must | Canonical corpus preservation |
                | Won’t have this time | Guide-authored domain rulings |
                """
            ),
            "priority": "P1",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "story", {})
        assert "Canonical corpus preservation" in body
        assert "Guide-authored domain rulings" in body
        assert "[ITEM]" not in body


# ---------------------------------------------------------------------------
# Root cause 4c: Epic + Story Artifacts parse and render
# ---------------------------------------------------------------------------


EPIC_WITH_ARTIFACTS = textwrap.dedent(
    """\
    #### Objective

    Why this epic exists.

    #### Security/Compliance

    - Evidence must be tamper-evident.

    #### Artifacts

    - `docs/personas/README.md`
    - Updated SHA-256 manifest
    """
)


class TestEpicStoryArtifacts:
    def test_epic_artifacts_parse_not_folded(self) -> None:
        subs = _parse_subsections(EPIC_WITH_ARTIFACTS, "epic")
        assert subs.get("artifacts") == [
            "`docs/personas/README.md`",
            "Updated SHA-256 manifest",
        ]
        assert "Artifacts" not in str(subs.get("security_compliance", ""))

    def test_epic_artifacts_render_as_section(self) -> None:
        item = {
            "title": "First Epic",
            "description": EPIC_WITH_ARTIFACTS,
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "epic", {})
        assert "### Artifacts" in body
        assert "- [ ] `docs/personas/README.md`" in body
        assert "- [ ] Updated SHA-256 manifest" in body

    def test_story_artifacts_parse_and_render(self) -> None:
        item = {
            "title": "Build the widget",
            "description": textwrap.dedent(
                """\
                #### TL;DR

                One-liner.

                #### Artifacts

                - Validation report
                """
            ),
            "priority": "P1",
            "size": "S",
            "blocking": [],
        }
        subs = _parse_subsections(item["description"], "story")
        assert subs.get("artifacts") == ["Validation report"]
        body = generate_body(item, "story", {})
        assert "### Artifacts" in body
        assert "- [ ] Validation report" in body


# ---------------------------------------------------------------------------
# End-to-end regression: PR #2002 persona-governance plan (50 items)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def persona_rendered() -> list[tuple[str, dict, str]]:
    """Parse the fixture plan and render every item's body once."""
    plan = parse_plan(str(PERSONA_PLAN))
    rows: list[tuple[str, dict, str]] = []
    for level, items in (
        ("scope", [plan["scope"]]),
        ("initiative", plan["initiatives"]),
        ("epic", plan["epics"]),
        ("story", plan["stories"]),
        ("task", plan["tasks"]),
    ):
        for item in items:
            body = generate_body(item.copy(), level, {})
            rows.append((level, item, body))
    return rows


class TestPersonaGovernancePlanEndToEnd:
    def test_expected_item_count(self, persona_rendered) -> None:
        assert len(persona_rendered) == 50

    def test_zero_placeholders_in_all_bodies(self, persona_rendered) -> None:
        offenders: list[str] = []
        for level, item, body in persona_rendered:
            detected = sorted(
                set(PLACEHOLDER_RE.findall(body))
                | set(PLACEHOLDER_DESCRIPTIVE_RE.findall(body))
            )
            if "#[N]" in body:
                detected.append("#[N]")
            if detected:
                offenders.append(f"{level} {item['title'][:50]}: {detected}")
        assert not offenders, "\n".join(offenders)

    def test_check_issue_reports_no_p04(self, persona_rendered) -> None:
        offenders: list[str] = []
        for level, item, body in persona_rendered:
            gaps = check_issue(1, item["title"], body, level)
            p04 = [g for g in gaps if g["rule"] == "P0-4"]
            if p04:
                offenders.append(
                    f"{level} {item['title'][:50]}: {p04[0]['placeholders']}"
                )
        assert not offenders, "\n".join(offenders)

    def test_all_authored_artifacts_parse(self, persona_rendered) -> None:
        authored = sum(
            bool(re.match(r"^#{1,6} Artifacts\s*$", line))
            for line in PERSONA_PLAN.read_text(encoding="utf-8").splitlines()
        )
        parsed = sum(
            bool(item["subsections"].get("artifacts"))
            for _, item, _ in persona_rendered
        )
        assert authored == 37, "fixture drifted — expected 37 authored Artifacts"
        assert parsed == authored, f"only {parsed}/{authored} Artifacts parsed"

    def test_no_artifacts_folded_into_security(self, persona_rendered) -> None:
        folded = [
            item["title"][:50]
            for _, item, _ in persona_rendered
            if "Artifacts" in str(item["subsections"].get("security_compliance", ""))
        ]
        assert not folded, f"Artifacts folded into security_compliance: {folded}"

    def test_artifacts_render_in_bodies(self, persona_rendered) -> None:
        """Every parsed artifact list must surface in the rendered body."""
        missing: list[str] = []
        for level, item, body in persona_rendered:
            artifacts = item["subsections"].get("artifacts") or []
            for entry in artifacts:
                if entry not in body:
                    missing.append(f"{level} {item['title'][:40]}: {entry[:60]}")
        assert not missing, "\n".join(missing)

    def test_story_moscow_tables_preserved(self, persona_rendered) -> None:
        """The 10 stories authoring MoSCoW as tables must keep their rows
        (including `Won't have this time`) in the rendered body."""
        preserved = 0
        lost: list[str] = []
        for level, item, body in persona_rendered:
            if level != "story":
                continue
            moscow = item["subsections"].get("moscow")
            if isinstance(moscow, str) and moscow.strip():
                preserved += 1
                # First data row of the authored table must survive rendering
                first_row = next(
                    (
                        line
                        for line in moscow.splitlines()
                        if line.strip().startswith("|")
                        and not re.match(r"^\|[\s\-:|]+\|?$", line.strip())
                        and "Priority" not in line
                    ),
                    None,
                )
                if first_row and first_row.strip() not in body:
                    lost.append(f"{item['title'][:50]}: {first_row.strip()[:60]}")
        assert (
            preserved == 10
        ), f"expected 10 table-form story MoSCoW sections, found {preserved}"
        assert not lost, "\n".join(lost)

    def test_recognized_bullet_subsections_preserved(self, persona_rendered) -> None:
        """Spot-preservation sweep: the first entry of every parsed bullet
        subsection must appear in the rendered body."""
        checked_keys = ("success_criteria", "done_when", "artifacts", "out_of_scope")
        missing: list[str] = []
        for level, item, body in persona_rendered:
            for key in checked_keys:
                val = item["subsections"].get(key)
                if isinstance(val, list) and val:
                    if val[0] not in body:
                        missing.append(
                            f"{level} {item['title'][:40]} {key}: {val[0][:60]}"
                        )
        assert not missing, "\n".join(missing)
