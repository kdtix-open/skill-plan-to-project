"""
Tests for EP-001: Skill Structure, Documentation & Bundled Assets.

These tests validate the static skill artifacts (SKILL.md, openai.yaml,
templates, references) against the KDTIX compliance requirements.

TDD: These tests were written FIRST to define the acceptance criteria for
Stories #6, #7, and #8 before authoring the actual files.
"""

import re
from pathlib import Path

import pytest
import yaml

SKILL_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Story #6: SKILL.md — Author with Frontmatter, Workflow & Design Decisions
# ---------------------------------------------------------------------------


class TestSkillMd:
    """Acceptance tests for SKILL.md (Story #6)."""

    @pytest.fixture
    def skill_md(self) -> str:
        path = SKILL_ROOT / "SKILL.md"
        assert path.exists(), "SKILL.md must exist at repo root"
        return path.read_text(encoding="utf-8")

    @pytest.fixture
    def frontmatter(self, skill_md: str) -> dict:
        """Parse YAML frontmatter from SKILL.md."""
        match = re.match(r"^---\n(.*?)\n---", skill_md, re.DOTALL)
        assert match, "SKILL.md must have YAML frontmatter delimited by ---"
        return yaml.safe_load(match.group(1))

    def test_frontmatter_has_name(self, frontmatter: dict) -> None:
        assert "name" in frontmatter, "frontmatter must have 'name' field"
        assert frontmatter["name"], "name must be non-empty"

    def test_frontmatter_name_is_kebab_case(self, frontmatter: dict) -> None:
        assert (
            frontmatter["name"] == "plan-to-project"
        ), "name must be 'plan-to-project'"

    def test_frontmatter_has_description(self, frontmatter: dict) -> None:
        assert "description" in frontmatter, "frontmatter must have 'description'"
        desc = str(frontmatter["description"]).strip()
        assert len(desc) > 20, "description must be substantive (>20 chars)"

    def test_all_9_phases_documented(self, skill_md: str) -> None:
        """SKILL.md must document all 9 workflow phases."""
        for phase_num in range(1, 10):
            assert (
                f"Phase {phase_num}" in skill_md
            ), f"SKILL.md must document Phase {phase_num}"

    def test_inputs_table_present(self, skill_md: str) -> None:
        assert "PLAN_FILE" in skill_md, "Inputs table must document PLAN_FILE"
        assert "ORG" in skill_md, "Inputs table must document ORG"
        assert "REPO" in skill_md, "Inputs table must document REPO"
        assert "PROJECT_NUMBER" in skill_md, "Inputs table must document PROJECT_NUMBER"

    def test_design_decisions_section_present(self, skill_md: str) -> None:
        assert (
            "Design Decision" in skill_md
        ), "SKILL.md must have a Design Decisions section"

    def test_references_all_scripts(self, skill_md: str) -> None:
        for script in [
            "create-issues.py",
            "set-relationships.py",
            "set-project-fields.py",
            "compliance-check.py",
            "queue-order.py",
        ]:
            assert script in skill_md, f"SKILL.md must reference {script}"

    def test_references_all_assets(self, skill_md: str) -> None:
        for template in [
            "template-scope",
            "template-initiative",
            "template-epic",
            "template-story",
            "template-task",
        ]:
            assert template in skill_md, f"SKILL.md must reference {template}"

    def test_references_all_reference_docs(self, skill_md: str) -> None:
        for ref in [
            "plan-format",
            "github-graphql",
            "sub-issues-api",
            "gh-cli-patterns",
            "compliance-rules",
            "design-decisions",
        ]:
            assert ref in skill_md, f"SKILL.md must reference {ref}"

    def test_prerequisites_section_present(self, skill_md: str) -> None:
        assert "Prerequisites" in skill_md or "prerequisites" in skill_md


# ---------------------------------------------------------------------------
# Story #7: Bundle Template Assets & Create Reference Documents
# ---------------------------------------------------------------------------


class TestBundledAssets:
    """Acceptance tests for assets/ templates (Story #7)."""

    TEMPLATES = [
        "template-scope.md",
        "template-initiative.md",
        "template-epic.md",
        "template-story.md",
        "template-task.md",
    ]

    @pytest.fixture
    def assets_dir(self) -> Path:
        d = SKILL_ROOT / "assets"
        assert d.is_dir(), "assets/ directory must exist"
        return d

    @pytest.mark.parametrize("template", TEMPLATES)
    def test_template_exists(self, assets_dir: Path, template: str) -> None:
        path = assets_dir / template
        assert path.exists(), f"assets/{template} must exist"

    @pytest.mark.parametrize("template", TEMPLATES)
    def test_template_is_non_empty(self, assets_dir: Path, template: str) -> None:
        content = (assets_dir / template).read_text(encoding="utf-8")
        assert len(content) > 100, f"assets/{template} must have substantial content"

    @pytest.mark.parametrize("template", TEMPLATES)
    def test_template_has_tdd_sentinel(self, assets_dir: Path, template: str) -> None:
        content = (assets_dir / template).read_text(encoding="utf-8")
        assert (
            "TDD followed" in content
        ), f"assets/{template} must include TDD sentinel in I Know I Am Done When"

    @pytest.mark.parametrize("template", TEMPLATES)
    def test_template_has_done_when_section(
        self, assets_dir: Path, template: str
    ) -> None:
        content = (assets_dir / template).read_text(encoding="utf-8")
        assert (
            "I Know I Am Done When" in content
        ), f"assets/{template} must have 'I Know I Am Done When' section"

    def test_story_template_has_user_story_block(self, assets_dir: Path) -> None:
        content = (assets_dir / "template-story.md").read_text(encoding="utf-8")
        assert "As a" in content, "Story template must have user story format"
        assert "So that" in content, "Story template must have user story format"

    def test_story_template_has_moscow(self, assets_dir: Path) -> None:
        content = (assets_dir / "template-story.md").read_text(encoding="utf-8")
        assert "MoSCoW" in content, "Story template must have MoSCoW section"

    def test_epic_template_has_security_section(self, assets_dir: Path) -> None:
        content = (assets_dir / "template-epic.md").read_text(encoding="utf-8")
        assert (
            "Security" in content
        ), "Epic template must have Security/Compliance section"

    def test_task_template_has_security_section(self, assets_dir: Path) -> None:
        content = (assets_dir / "template-task.md").read_text(encoding="utf-8")
        assert (
            "Security" in content
        ), "Task template must have Security/Compliance section"


class TestReferenceDocuments:
    """Acceptance tests for references/ docs (Story #7)."""

    REFERENCES = [
        "plan-format.md",
        "github-graphql.md",
        "sub-issues-api.md",
        "gh-cli-patterns.md",
        "compliance-rules.md",
        "design-decisions.md",
    ]

    @pytest.fixture
    def references_dir(self) -> Path:
        d = SKILL_ROOT / "references"
        assert d.is_dir(), "references/ directory must exist"
        return d

    @pytest.mark.parametrize("ref", REFERENCES)
    def test_reference_exists(self, references_dir: Path, ref: str) -> None:
        path = references_dir / ref
        assert path.exists(), f"references/{ref} must exist"

    @pytest.mark.parametrize("ref", REFERENCES)
    def test_reference_is_non_empty(self, references_dir: Path, ref: str) -> None:
        content = (references_dir / ref).read_text(encoding="utf-8")
        assert len(content) > 100, f"references/{ref} must have substantial content"

    def test_sub_issues_api_documents_database_id(self, references_dir: Path) -> None:
        content = (references_dir / "sub-issues-api.md").read_text(encoding="utf-8")
        assert "databaseId" in content, "sub-issues-api.md must document databaseId"
        assert "-F" in content, "sub-issues-api.md must document -F flag for integers"

    def test_gh_cli_patterns_uses_body_file(self, references_dir: Path) -> None:
        content = (references_dir / "gh-cli-patterns.md").read_text(encoding="utf-8")
        assert "--body-file" in content, "gh-cli-patterns.md must document --body-file"

    def test_gh_cli_patterns_uses_utf8_encoding(self, references_dir: Path) -> None:
        content = (references_dir / "gh-cli-patterns.md").read_text(encoding="utf-8")
        assert (
            "utf-8" in content.lower()
        ), "gh-cli-patterns.md must document utf-8 encoding"

    def test_compliance_rules_has_p0_p1_p2(self, references_dir: Path) -> None:
        content = (references_dir / "compliance-rules.md").read_text(encoding="utf-8")
        for level in ["P0", "P1", "P2"]:
            assert level in content, f"compliance-rules.md must define {level} rules"

    def test_design_decisions_has_body_file_rationale(
        self, references_dir: Path
    ) -> None:
        content = (references_dir / "design-decisions.md").read_text(encoding="utf-8")
        assert "--body-file" in content


# ---------------------------------------------------------------------------
# Story #8: agents/openai.yaml Agents Metadata
# ---------------------------------------------------------------------------


class TestOpenAIYaml:
    """Acceptance tests for agents/openai.yaml (Story #8)."""

    @pytest.fixture
    def openai_yaml(self) -> dict:
        path = SKILL_ROOT / "agents" / "openai.yaml"
        assert path.exists(), "agents/openai.yaml must exist"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_has_interface_section(self, openai_yaml: dict) -> None:
        assert "interface" in openai_yaml, "openai.yaml must have 'interface' key"

    def test_has_display_name(self, openai_yaml: dict) -> None:
        iface = openai_yaml["interface"]
        assert "display_name" in iface, "interface must have display_name"
        assert iface["display_name"], "display_name must be non-empty"

    def test_has_short_description(self, openai_yaml: dict) -> None:
        iface = openai_yaml["interface"]
        assert "short_description" in iface, "interface must have short_description"
        desc = iface["short_description"]
        assert (
            25 <= len(desc) <= 64
        ), f"short_description must be 25-64 chars, got {len(desc)}: '{desc}'"

    def test_has_default_prompt(self, openai_yaml: dict) -> None:
        iface = openai_yaml["interface"]
        assert "default_prompt" in iface, "interface must have default_prompt"
        assert (
            "$plan-to-project" in iface["default_prompt"]
        ), "default_prompt must reference $plan-to-project"

    def test_display_name_matches_skill(self) -> None:
        skill_path = SKILL_ROOT / "SKILL.md"
        openai_path = SKILL_ROOT / "agents" / "openai.yaml"
        skill_text = skill_path.read_text(encoding="utf-8")
        openai_data = yaml.safe_load(openai_path.read_text(encoding="utf-8"))
        assert (
            "plan-to-project" in skill_text
        ), "SKILL.md must reference plan-to-project"
        assert openai_data["interface"]["display_name"], "display_name must be set"
