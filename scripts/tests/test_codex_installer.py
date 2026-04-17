"""Acceptance tests for Codex-native installer support."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

import pytest
import tomllib

from scripts import install_codex


def _write_skill_source(root: Path) -> None:
    """Create a minimal skill source tree for installer tests."""
    (root / "agents").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "references").mkdir()
    (root / "scripts").mkdir()

    (root / "SKILL.md").write_text(
        "---\nname: plan-to-project\ndescription: Test skill\n---\n# Plan to Project\n",
        encoding="utf-8",
    )
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / "agents" / "openai.yaml").write_text(
        'interface:\n  display_name: "Plan to Project"\n',
        encoding="utf-8",
    )
    (root / "assets" / "template-story.md").write_text(
        "story template\n",
        encoding="utf-8",
    )
    (root / "assets" / "plugin-icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "references" / "plan-format.md").write_text(
        "plan reference\n",
        encoding="utf-8",
    )
    (root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (root / "scripts" / "create_issues.py").write_text(
        "def main():\n    return 0\n",
        encoding="utf-8",
    )


class TestCodexInstaller:
    """Installer scenarios for home and repo-native Codex destinations."""

    def test_pyproject_explicitly_limits_setuptools_packages(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        assert pyproject["tool"]["setuptools"]["packages"] == ["scripts"]

    def test_home_skill_installs_under_codex_home(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        codex_home = tmp_path / "codex-home"
        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.HOME_SKILL,
            codex_home=codex_home,
        )

        skill_root = codex_home / "skills" / "plan-to-project"
        assert (skill_root / "SKILL.md").exists()
        assert (skill_root / "agents" / "openai.yaml").exists()
        assert (skill_root / "assets" / "template-story.md").exists()

    def test_claude_skill_installs_under_claude_home(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        claude_home = tmp_path / "claude-home"
        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.CLAUDE_SKILL,
            claude_home=claude_home,
        )

        skill_root = claude_home / "skills" / "plan-to-project"
        assert (skill_root / "SKILL.md").exists()
        assert (skill_root / "agents" / "openai.yaml").exists()
        assert (skill_root / "references" / "plan-format.md").exists()

    def test_cursor_rule_installs_under_repo_cursor_rules(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)
        source_root.joinpath("assets", "cursor-plan-to-project.mdc").write_text(
            "---\ndescription: plan-to-project\nalwaysApply: false\n---\n",
            encoding="utf-8",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.CURSOR_RULE,
            repo_root=repo_root,
        )

        rule_path = repo_root / ".cursor" / "rules" / "plan-to-project.mdc"
        assert rule_path.exists()
        assert "description: plan-to-project" in rule_path.read_text(encoding="utf-8")

    def test_home_plugin_creates_plugin_and_marketplace(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        codex_home = tmp_path / "codex-home"
        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.HOME_PLUGIN,
            codex_home=codex_home,
        )

        plugin_root = tmp_path / "plugins" / "plan-to-project"
        plugin_manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (tmp_path / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )

        assert plugin_manifest["name"] == "plan-to-project"
        assert plugin_manifest["skills"] == "./skills/"
        assert (
            plugin_manifest["interface"]["composerIcon"] == "./assets/plugin-icon.png"
        )
        assert plugin_manifest["interface"]["logo"] == "./assets/plugin-icon.png"
        assert (
            plugin_manifest["interface"]["websiteURL"] == "https://skills.projectit.ai"
        )
        assert (plugin_root / "assets" / "plugin-icon.png").exists()
        assert (
            plugin_root / "skills" / "plan-to-project" / "references" / "plan-format.md"
        ).exists()
        assert (
            marketplace["plugins"][0]["source"]["path"] == "./plugins/plan-to-project"
        )

    def test_repo_plugin_installs_into_target_repo(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        repo_root = tmp_path / "target-repo"
        repo_root.mkdir()
        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.REPO_PLUGIN,
            repo_root=repo_root,
        )

        plugin_root = repo_root / "plugins" / "plan-to-project"
        marketplace_path = repo_root / ".agents" / "plugins" / "marketplace.json"

        assert (plugin_root / ".codex-plugin" / "plugin.json").exists()
        assert (plugin_root / "skills" / "plan-to-project" / "SKILL.md").exists()
        assert marketplace_path.exists()

    def test_existing_destination_requires_force(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        codex_home = tmp_path / "codex-home"
        existing_root = codex_home / "skills" / "plan-to-project"
        existing_root.mkdir(parents=True)

        with pytest.raises(FileExistsError):
            install_codex.install_from_source(
                source_root=source_root,
                destination=install_codex.InstallDestination.HOME_SKILL,
                codex_home=codex_home,
            )

    def test_repo_plugin_requires_repo_root(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        with pytest.raises(ValueError):
            install_codex.install_from_source(
                source_root=source_root,
                destination=install_codex.InstallDestination.REPO_PLUGIN,
            )

    def test_force_replaces_existing_destination(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()
        _write_skill_source(source_root)

        codex_home = tmp_path / "codex-home"
        existing_root = codex_home / "skills" / "plan-to-project"
        existing_root.mkdir(parents=True)
        (existing_root / "old.txt").write_text("stale", encoding="utf-8")

        install_codex.install_from_source(
            source_root=source_root,
            destination=install_codex.InstallDestination.HOME_SKILL,
            codex_home=codex_home,
            force=True,
        )

        assert not (existing_root / "old.txt").exists()
        assert (existing_root / "SKILL.md").exists()

    def test_validate_source_root_reports_missing_items(self, tmp_path: Path) -> None:
        source_root = tmp_path / "source"
        source_root.mkdir()

        with pytest.raises(FileNotFoundError):
            install_codex.validate_source_root(source_root)

    def test_load_marketplace_preserves_existing_payload(self, tmp_path: Path) -> None:
        marketplace_path = tmp_path / "marketplace.json"
        marketplace_path.write_text(
            json.dumps(
                {
                    "name": "existing-marketplace",
                    "interface": {"displayName": "Existing"},
                    "plugins": [{"name": "another-plugin"}],
                }
            ),
            encoding="utf-8",
        )

        payload = install_codex.load_marketplace(
            marketplace_path=marketplace_path,
            marketplace_name="ignored",
            marketplace_display_name="Ignored",
        )

        assert payload["name"] == "existing-marketplace"
        assert payload["plugins"][0]["name"] == "another-plugin"

    def test_read_project_version_defaults_when_pyproject_missing(
        self, tmp_path: Path
    ) -> None:
        assert install_codex.read_project_version(tmp_path) == "0.1.0"

    def test_read_project_version_uses_pyproject_version(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "plan-to-project"\nversion = "1.2.3"\n',
            encoding="utf-8",
        )

        assert install_codex.read_project_version(tmp_path) == "1.2.3"

    def test_parse_args_accepts_repo_plugin_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "example-repo"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plan-to-project-install",
                "--destination",
                "repo-plugin",
                "--repo-root",
                str(repo_root),
                "--source",
                "local",
            ],
        )

        args = install_codex.parse_args()

        assert args.destination == install_codex.InstallDestination.REPO_PLUGIN
        assert args.repo_root == repo_root
        assert args.source == "local"

    def test_parse_args_accepts_claude_skill_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        claude_home = tmp_path / "claude-home"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plan-to-project-install",
                "--destination",
                "claude-skill",
                "--claude-home",
                str(claude_home),
                "--source",
                "local",
            ],
        )

        args = install_codex.parse_args()

        assert args.destination == install_codex.InstallDestination.CLAUDE_SKILL
        assert args.claude_home == claude_home
        assert args.source == "local"

    def test_parse_args_accepts_cursor_rule_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plan-to-project-install",
                "--destination",
                "cursor-rule",
                "--repo-root",
                str(repo_root),
                "--source",
                "local",
            ],
        )

        args = install_codex.parse_args()

        assert args.destination == install_codex.InstallDestination.CURSOR_RULE
        assert args.repo_root == repo_root
        assert args.source == "local"

    def test_build_github_archive_url_allows_expected_repo_and_ref(self) -> None:
        archive_url = install_codex.build_github_archive_url(
            repo="kdtix-open/skill-plan-to-project",
            ref="main",
        )

        assert (
            archive_url
            == "https://api.github.com/repos/kdtix-open/skill-plan-to-project/zipball/main"
        )

    def test_build_github_archive_url_rejects_unsafe_repo(self) -> None:
        with pytest.raises(ValueError):
            install_codex.build_github_archive_url(
                repo="https://example.com/bad",
                ref="main",
            )

    def test_main_local_source_prints_install_location(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        source_root = tmp_path / "source"
        codex_home = tmp_path / "codex-home"
        installed_path = codex_home / "skills" / "plan-to-project"
        namespace = argparse.Namespace(
            destination=install_codex.InstallDestination.HOME_SKILL,
            source="local",
            repo=install_codex.DEFAULT_REPO,
            ref="main",
            source_root=source_root,
            codex_home=codex_home,
            claude_home=None,
            repo_root=None,
            force=False,
        )
        monkeypatch.setattr(install_codex, "parse_args", lambda: namespace)
        monkeypatch.setattr(
            install_codex,
            "install_from_source",
            lambda **_: installed_path,
        )

        result = install_codex.main()

        captured = capsys.readouterr()
        assert result == 0
        assert f"Installed plan-to-project to {installed_path}" in captured.out
        assert "Restart your agent" in captured.out

    def test_main_github_source_uses_downloaded_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        source_root = tmp_path / "source"
        codex_home = tmp_path / "codex-home"
        downloaded_root = tmp_path / "downloaded-source"
        installed_path = codex_home / "skills" / "plan-to-project"
        namespace = argparse.Namespace(
            destination=install_codex.InstallDestination.HOME_SKILL,
            source="github",
            repo=install_codex.DEFAULT_REPO,
            ref="main",
            source_root=source_root,
            codex_home=codex_home,
            claude_home=None,
            repo_root=None,
            force=False,
        )
        seen: dict[str, Path] = {}

        @contextlib.contextmanager
        def fake_github_source(repo: str, ref: str):
            assert repo == install_codex.DEFAULT_REPO
            assert ref == "main"
            yield downloaded_root

        def fake_install_from_source(**kwargs):
            seen["source_root"] = kwargs["source_root"]
            return installed_path

        monkeypatch.setattr(install_codex, "parse_args", lambda: namespace)
        monkeypatch.setattr(install_codex, "github_source", fake_github_source)
        monkeypatch.setattr(
            install_codex, "install_from_source", fake_install_from_source
        )

        result = install_codex.main()

        captured = capsys.readouterr()
        assert result == 0
        assert seen["source_root"] == downloaded_root
        assert "Installed plan-to-project" in captured.out


class TestPackagingMetadata:
    """Packaging metadata for remote GitHub installation."""

    def test_pyproject_exposes_console_entry_point(self) -> None:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        assert data["project"]["scripts"]["plan-to-project-install"]
        assert (
            data["project"]["scripts"]["plan-to-project-install"]
            == "scripts.install_codex:main"
        )
