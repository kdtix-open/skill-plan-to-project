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
    _insert_sections_before_done_when,
    _moscow_str_fallback_block,
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
    """A confirmed PR #75 review finding: an earlier revision anchored
    PLACEHOLDER_RE's `[N]` alternative to `(?<=#)\\[N\\]` to avoid a
    false positive on code references like `arr[N]`, but that regressed
    issue #74's literal acceptance criterion ("Update P0-4 so `[N]` is
    detected") — a bare `[N]` with no `#` prefix (e.g. "issue [N]") no
    longer tripped the gate. The fix is context-aware exclusion (strip
    code spans/blocks before scanning — see `_strip_code_spans` in
    `compliance_check.py`) rather than narrowing the regex itself, so
    `[N]` is detected unconditionally again."""

    def test_placeholder_re_matches_n_regardless_of_prefix(self) -> None:
        assert PLACEHOLDER_RE.search("see issue #[N] for details")
        assert PLACEHOLDER_RE.search("| 1 | #[N] | Title |")
        # No `#` prefix — must still match (the regressed case).
        assert PLACEHOLDER_RE.search("[N]")
        assert PLACEHOLDER_RE.search("issue [N]")

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

    def test_check_issue_flags_n_with_no_hash_prefix(self) -> None:
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "See issue [N] for the full discussion.\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        p04 = [g for g in gaps if g["rule"] == "P0-4"]
        assert p04, "expected a P0-4 gap for a bare [N] with no # prefix"

    def test_check_issue_ignores_code_reference_in_backticks(self) -> None:
        """`arr[N]` inside backticks is legitimate authored content (array
        indexing in implementation notes), not an unfilled placeholder —
        context-aware exclusion via code-span stripping, not regex
        narrowing, is what must keep this from tripping P0-4."""
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "## Implementation Notes\n\n"
            "Use `arr[N]` indexing in the ring buffer.\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        assert not [g for g in gaps if g["rule"] == "P0-4"]

    def test_check_issue_ignores_mermaid_node_bracket_syntax(self) -> None:
        """Mermaid flowchart node syntax (`A[Start]`) inside a fenced
        ```mermaid block must not trip P0-4 — it's diagram syntax, not a
        template leak."""
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "```mermaid\n"
            "flowchart LR\n"
            "    A[Start] --> B[Finish]\n"
            "```\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        assert not [g for g in gaps if g["rule"] == "P0-4"]

    def test_check_issue_still_flags_bare_n_outside_code(self) -> None:
        """The exclusion is code-scoped, not blanket: a bare `[N]` sitting
        in ordinary prose right next to a code span must still trip P0-4."""
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "Use `arr[N]` indexing; see issue [N] for context.\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        p04 = [g for g in gaps if g["rule"] == "P0-4"]
        assert p04, "expected a P0-4 gap for the bare [N] outside the code span"

    def test_check_issue_checkboxes_produce_no_p04(self) -> None:
        body = (
            "## I Know I Am Done When\n"
            "TDD followed: failing test written BEFORE implementation\n\n"
            "- [ ] criterion one\n- [x] criterion two\n- [X] criterion three\n"
        )
        gaps = check_issue(1, "Sample", body, "task")
        assert not [g for g in gaps if g["rule"] == "P0-4"]

    def test_check_issue_flags_uppercase_vision_placeholder(self) -> None:
        """A confirmed PR #75 review finding: the scope template's
        narrative-fallback placeholder is ALL CAPS
        ("[VISION — 1-2 sentences ...]"), not the title-case "Vision" the
        descriptive regex's alternation lists — so a scope item with every
        other required subsection populated but no Vision text silently
        shipped with no P0-4 gap. PLACEHOLDER_DESCRIPTIVE_RE must match
        case-insensitively."""
        body = (
            "## Vision\n\n"
            "[VISION — 1-2 sentences on the end state and the value "
            "delivered]\n\n"
            "## I Know I Am Done When\n\n"
            "TDD followed: failing test written BEFORE implementation\n"
        )
        gaps = check_issue(1, "Sample", body, "scope")
        p04 = [g for g in gaps if g["rule"] == "P0-4"]
        assert p04, "expected a P0-4 gap for the unfilled all-caps VISION placeholder"

    # NOTE: an end-to-end reproduction via generate_body() (render a scope
    # item with every FR #45 required subsection populated but no Vision,
    # confirm check_issue flags it) is deliberately NOT included here. On
    # this branch, `_render_template`'s separate, pre-existing
    # narrative-fallback bug (see the filed follow-up task) currently
    # substitutes the whole raw description into the Vision slot in that
    # exact scenario — so the `[VISION ...]` placeholder this test targets
    # never actually reaches `generate_body`'s output today, and the
    # end-to-end assertion can't exercise what it's meant to. The direct
    # scanner-level test above is the real regression guard for what this
    # PR changes (PLACEHOLDER_DESCRIPTIVE_RE case-sensitivity); add the
    # end-to-end version once the narrative-fallback fix lands and truly
    # leaves the placeholder in place.


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
        # Artifacts parses as raw text (issue #74: non-bullet content — tables,
        # paragraphs, wrapped continuations — must survive; the renderer
        # checkboxifies bullet lines at render time, not parse time).
        assert subs.get("artifacts") == "- Updated operator runbook"
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


class TestMoscowStrFallbackShapes:
    """A confirmed review finding: the raw-text MoSCoW fallback must not
    wedge non-table content (prose, flat bullets with no group headers)
    underneath the template's table header — that produces a malformed
    GFM table with no bracketed placeholder for the P0-4 gate to catch."""

    def test_prose_only_passes_through_verbatim_no_dangling_header(self) -> None:
        block = _moscow_str_fallback_block(
            "Everything here is must-have; nothing deferred."
        )
        assert block == "Everything here is must-have; nothing deferred."
        assert "| Priority | Item |" not in block

    def test_flat_bullets_no_group_headers_pass_through_verbatim(self) -> None:
        block = _moscow_str_fallback_block("- Core parsing\n- Fast rendering")
        assert block == "- Core parsing\n- Fast rendering"
        assert "| Priority | Item |" not in block

    def test_table_preceded_by_prose_keeps_prose_above_rebuilt_table(self) -> None:
        raw = (
            "Classification per governance charter:\n\n"
            "| Priority | Item |\n"
            "|---|---|\n"
            "| Must | Corpus preservation |\n"
        )
        block = _moscow_str_fallback_block(raw)
        assert block.startswith("Classification per governance charter:")
        # The authored header + delimiter are not duplicated as data rows.
        assert block.count("| Priority | Item |") == 1
        assert block.count("|---|---|") == 0
        assert "| Must | Corpus preservation |" in block

    def test_table_preceded_by_prose_renders_without_garbled_rows(self) -> None:
        item = {
            "title": "Ratify Guide Publication",
            "description": textwrap.dedent(
                """\
                #### TL;DR

                One-liner.

                #### MoSCoW

                Classification per governance charter:

                | Priority | Item |
                |---|---|
                | Must | Corpus preservation |
                """
            ),
            "priority": "P1",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "story", {})
        assert "Classification per governance charter:" in body
        assert "Corpus preservation" in body
        # No duplicated header/delimiter rows from the authored table.
        assert body.count("| Priority | Item |") == 1
        assert "|---|---|" not in body


class TestInsertSectionsBeforeDoneWhenAnchoring:
    """A confirmed MAJOR review finding: anchoring on the FIRST 1-6-hash
    'I Know I Am Done When' match could land inside the narrative
    fallback blob (the raw description gets substituted whole into the
    Vision/Objective/TL;DR slot when the plan has no primary narrative
    subsection and no leading text — and that raw text can carry the
    plan's own `#### I Know I Am Done When` heading).  The fix anchors on
    the LAST match of the template's own heading level."""

    def test_scope_with_no_vision_or_leading_text_does_not_splice_narrative(
        self,
    ) -> None:
        # No `#### Vision` and no leading prose — `_render_template`'s
        # PRE-EXISTING (not introduced by issue #74) narrative fallback
        # substitutes the entire raw description into the Vision slot in
        # this shape, including the plan's own Done-When heading text —
        # see the follow-up task filed for that separate bug. What THIS
        # fix owns: the newly-inserted Dependencies/Artifacts/Security
        # sections must not land inside that blob, must not fragment on
        # the wrong (`####`) heading level, and must appear exactly once
        # as their own proper sections — not further duplicated by the
        # insertion logic itself.
        description = textwrap.dedent(
            """\
            #### Business Problem

            Existing process doesn't scale.

            #### Success Criteria

            - Ships on time

            #### Assumptions

            - Team is available

            #### Out of Scope

            - Legacy migration

            #### Dependencies

            - Platform team sign-off

            #### I Know I Am Done When

            - All initiatives closed
            """
        )
        item = {
            "title": "Test Project",
            "description": description,
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "scope", {})
        # The template's own (`## `) Done When heading appears exactly
        # once; the pre-existing bug duplicates the PLAN's `#### ` one
        # inside the Vision blob, which is a distinct heading level —
        # anchored at line start so "## " isn't matched as a substring of
        # "#### " (both contain the two-hash sequence).
        assert len(re.findall(r"(?m)^## I Know I Am Done When", body)) == 1
        # The inserted Dependencies section must not land inside the Vision
        # narrative block (i.e. it must appear AFTER the Vision heading's
        # own content, not spliced into raw description text above it),
        # and must precede the template's real Done When section.  Line-
        # anchored (as above): the Vision blob also contains a raw
        # "#### Dependencies" heading, whose "## Dependencies" substring
        # would otherwise be matched at the wrong (`####`) heading level.
        vision_idx = body.index("## Vision")
        deps_matches = [m.start() for m in re.finditer(r"(?m)^## Dependencies", body)]
        done_idx = re.search(r"(?m)^## I Know I Am Done When", body).start()
        # The inserted "## Dependencies" section appears exactly once (the
        # pre-existing Vision-blob bug separately echoes the plan's raw
        # "#### Dependencies" text at a different heading level, which is
        # out of scope here).
        assert len(deps_matches) == 1
        assert vision_idx < deps_matches[0] < done_idx

    def test_no_done_when_heading_appends_at_end(self) -> None:
        rendered = "# Title\n\nSome body.\n"
        out = _insert_sections_before_done_when(
            rendered, ["## Extra\n\ncontent\n\n"], "##"
        )
        assert out.startswith(rendered.rstrip("\n"))
        assert out.rstrip("\n").endswith("## Extra\n\ncontent")

    def test_multiple_done_when_headings_anchors_on_last(self) -> None:
        rendered = (
            "#### I Know I Am Done When\n"
            "- raw plan criterion\n\n"
            "## I Know I Am Done When\n"
            "- [ ] TDD\n"
        )
        out = _insert_sections_before_done_when(rendered, ["## Extra\n\n"], "##")
        # Inserted content lands before the LAST (## ) match, not the
        # first (#### ) match.
        assert out.index("## Extra") > out.index("#### I Know I Am Done When")
        assert out.index("## Extra") < out.rindex("## I Know I Am Done When")


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
        # Artifacts parses as raw text (issue #74): a bullet-parse would
        # silently drop any non-bullet content (tables, paragraphs, wrapped
        # continuation lines) that commonly appears in Artifacts sections.
        subs = _parse_subsections(EPIC_WITH_ARTIFACTS, "epic")
        assert subs.get("artifacts") == (
            "- `docs/personas/README.md`\n- Updated SHA-256 manifest"
        )
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
        assert subs.get("artifacts") == "- Validation report"
        body = generate_body(item, "story", {})
        assert "### Artifacts" in body
        assert "- [ ] Validation report" in body

    def test_epic_artifacts_preserves_table_and_paragraph(self) -> None:
        """Regression guard for the confirmed data-loss finding: an
        Artifacts section mixing a bullet, a paragraph, and a table must
        keep ALL of that content — not just the bullet — in the rendered
        body.  Before this test the bullet-parse path silently dropped
        everything except recognized bullet lines."""
        description = textwrap.dedent(
            """\
            #### Objective

            Why this epic exists.

            #### Artifacts

            - `docs/personas/README.md`

            Shared evidence-state semantics:

            | State | Required proof |
            |---|---|
            | Ratified | Governance sign-off |
            | Built | Passing test suite |
            """
        )
        subs = _parse_subsections(description, "epic")
        artifacts = subs.get("artifacts")
        assert isinstance(artifacts, str)
        assert "Shared evidence-state semantics:" in artifacts
        assert "| Ratified | Governance sign-off |" in artifacts

        item = {
            "title": "Evidence Epic",
            "description": description,
            "priority": "P0",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "epic", {})
        assert "- [ ] `docs/personas/README.md`" in body
        assert "Shared evidence-state semantics:" in body
        assert "| Ratified | Governance sign-off |" in body

    def test_story_artifacts_preserves_wrapped_continuation_line(self) -> None:
        """A bullet whose sentence wraps onto a continuation line (no `-`
        marker) must keep that continuation, not truncate mid-sentence."""
        description = textwrap.dedent(
            """\
            #### TL;DR

            One-liner.

            #### Artifacts

            - Persona/runtime authority matrix with current, target, gap, and
              confidence fields.
            """
        )
        item = {
            "title": "Baseline Persona Authority",
            "description": description,
            "priority": "P1",
            "size": "M",
            "blocking": [],
        }
        body = generate_body(item, "story", {})
        assert "confidence fields." in body


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
        assert len(persona_rendered) == 50, (
            "fixture item count drifted from the frozen PR #2002 snapshot "
            "(see the provenance comment at the top of the fixture file) — "
            "if the fixture was deliberately regenerated, update this and "
            "the other hard-coded counts in this test class (37 Artifacts "
            "headings, 10 table-form story MoSCoW sections)"
        )

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
        """Every non-blank line of the raw Artifacts text must survive
        rendering — including non-bullet content (tables, paragraphs,
        wrapped continuation lines) that a bullet-only parse would drop.
        Artifacts parses as raw text (issue #74), so this walks lines
        rather than parsed list entries; bullet lines render as
        checkboxes, so the comparison strips the bullet marker."""
        missing: list[str] = []
        for level, item, body in persona_rendered:
            artifacts = item["subsections"].get("artifacts")
            if not isinstance(artifacts, str) or not artifacts.strip():
                continue
            for line in artifacts.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                content = re.sub(r"^[-*]\s+", "", stripped)
                if content not in body:
                    missing.append(f"{level} {item['title'][:40]}: {content[:60]}")
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
        subsection must appear in the rendered body.  (`artifacts` is
        checked separately in test_artifacts_render_in_bodies — it parses
        as raw text, not a bullet list, so it doesn't belong in this
        list-shaped sweep.)"""
        checked_keys = ("success_criteria", "done_when", "out_of_scope")
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
