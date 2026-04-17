"""Install the plan-to-project skill into agent-native destinations."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

SKILL_NAME = "plan-to-project"
DEFAULT_REPO = "kdtix-open/skill-plan-to-project"
PLUGIN_ICON_NAME = "plugin-icon.png"
PLUGIN_ICON_SOURCE = Path("assets") / PLUGIN_ICON_NAME
GITHUB_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
GITHUB_REF_PATTERN = re.compile(r"[A-Za-z0-9._/-]+")
SKILL_BUNDLE_ITEMS = (
    "SKILL.md",
    "LICENSE",
    "agents",
    "assets",
    "references",
    "scripts",
)


class InstallDestination(StrEnum):
    """Supported skill and plugin installation targets."""

    HOME_SKILL = "home-skill"
    CLAUDE_SKILL = "claude-skill"
    HOME_PLUGIN = "home-plugin"
    REPO_PLUGIN = "repo-plugin"


def resolve_codex_home(codex_home: Path | None = None) -> Path:
    """Return the effective CODEX_HOME directory."""
    if codex_home is not None:
        return codex_home.expanduser().resolve()
    raw_value = os.environ.get("CODEX_HOME", "~/.codex")
    return Path(raw_value).expanduser().resolve()


def resolve_claude_home(claude_home: Path | None = None) -> Path:
    """Return the effective Claude home directory."""
    if claude_home is not None:
        return claude_home.expanduser().resolve()
    return Path("~/.claude").expanduser().resolve()


def install_from_source(
    source_root: Path,
    destination: InstallDestination,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    repo_root: Path | None = None,
    force: bool = False,
) -> Path:
    """Install from a prepared source tree into a supported destination."""
    validate_source_root(source_root)
    if destination == InstallDestination.HOME_SKILL:
        skill_root = resolve_codex_home(codex_home) / "skills" / SKILL_NAME
        copy_skill_bundle(source_root, skill_root, force=force)
        return skill_root
    if destination == InstallDestination.CLAUDE_SKILL:
        skill_root = resolve_claude_home(claude_home) / "skills" / SKILL_NAME
        copy_skill_bundle(source_root, skill_root, force=force)
        return skill_root
    if destination == InstallDestination.HOME_PLUGIN:
        home_root = resolve_codex_home(codex_home).parent
        return install_plugin_bundle(
            source_root=source_root,
            plugin_root=home_root / "plugins" / SKILL_NAME,
            marketplace_path=home_root / ".agents" / "plugins" / "marketplace.json",
            marketplace_name="local-plugins",
            marketplace_display_name="Local Plugins",
            force=force,
        )
    if repo_root is None:
        raise ValueError("repo_root is required for repo-plugin installs")
    repo_root = repo_root.expanduser().resolve()
    return install_plugin_bundle(
        source_root=source_root,
        plugin_root=repo_root / "plugins" / SKILL_NAME,
        marketplace_path=repo_root / ".agents" / "plugins" / "marketplace.json",
        marketplace_name=f"{repo_root.name}-plugins",
        marketplace_display_name=f"{repo_root.name} Plugins",
        force=force,
    )


def validate_source_root(source_root: Path) -> None:
    """Ensure the source tree contains the expected skill bundle."""
    missing = [name for name in SKILL_BUNDLE_ITEMS if not (source_root / name).exists()]
    if missing:
        missing_text = ", ".join(missing)
        raise FileNotFoundError(f"Missing required source items: {missing_text}")


def copy_skill_bundle(source_root: Path, destination_root: Path, force: bool) -> None:
    """Copy the skill bundle into a destination directory."""
    prepare_destination(destination_root, force=force)
    destination_root.mkdir(parents=True, exist_ok=True)
    for item_name in SKILL_BUNDLE_ITEMS:
        source_path = source_root / item_name
        target_path = destination_root / item_name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, target_path)


def prepare_destination(destination_root: Path, force: bool) -> None:
    """Guard against accidental overwrite of an existing installation."""
    if destination_root.exists() and not force:
        raise FileExistsError(f"Destination already exists: {destination_root}")
    if destination_root.exists():
        shutil.rmtree(destination_root)


def install_plugin_bundle(
    source_root: Path,
    plugin_root: Path,
    marketplace_path: Path,
    marketplace_name: str,
    marketplace_display_name: str,
    force: bool,
) -> Path:
    """Create a Codex plugin wrapper around the skill bundle."""
    prepare_destination(plugin_root, force=force)
    skill_root = plugin_root / "skills" / SKILL_NAME
    skill_root.parent.mkdir(parents=True, exist_ok=True)
    copy_skill_bundle(source_root, skill_root, force=False)
    copy_plugin_assets(source_root, plugin_root)
    write_plugin_manifest(source_root, plugin_root)
    update_marketplace(
        marketplace_path=marketplace_path,
        marketplace_name=marketplace_name,
        marketplace_display_name=marketplace_display_name,
    )
    return plugin_root


def write_plugin_manifest(source_root: Path, plugin_root: Path) -> None:
    """Write the native Codex plugin manifest."""
    manifest_root = plugin_root / ".codex-plugin"
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest = build_plugin_manifest(version=read_project_version(source_root))
    (manifest_root / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def copy_plugin_assets(source_root: Path, plugin_root: Path) -> None:
    """Copy plugin-specific UI assets into the plugin root."""
    source_icon = source_root / PLUGIN_ICON_SOURCE
    if not source_icon.exists():
        raise FileNotFoundError(f"Missing required plugin icon: {source_icon}")
    assets_root = plugin_root / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_icon, assets_root / PLUGIN_ICON_NAME)


def build_plugin_manifest(version: str) -> dict[str, object]:
    """Return plugin.json metadata for the plan-to-project wrapper."""
    return {
        "name": SKILL_NAME,
        "version": version,
        "description": "Codex plugin wrapper for the plan-to-project skill.",
        "author": {
            "name": "KDTIX Open",
            "url": "https://github.com/kdtix-open",
        },
        "homepage": "https://skills.projectit.ai",
        "repository": "https://github.com/kdtix-open/skill-plan-to-project",
        "license": "MIT",
        "keywords": ["codex", "skill", "github", "planning"],
        "skills": "./skills/",
        "interface": {
            "displayName": "Plan to Project",
            "shortDescription": "Turn a markdown plan into a GitHub Project backlog.",
            "longDescription": (
                "Installs the plan-to-project skill as a Codex plugin so teams can "
                "use it from a shared repo or local plugin catalog. To have it appear "
                "under the Skills tab, install with --destination home-skill instead."
            ),
            "developerName": "KDTIX Open",
            "category": "Productivity",
            "capabilities": ["Interactive", "Write"],
            "websiteURL": "https://skills.projectit.ai",
            "composerIcon": f"./assets/{PLUGIN_ICON_NAME}",
            "logo": f"./assets/{PLUGIN_ICON_NAME}",
            "defaultPrompt": [
                "Use $plan-to-project to turn my plan into GitHub issues.",
                "Install plan-to-project into this repo as a Codex plugin.",
                "Set up the plan-to-project skill in my Codex home.",
            ],
        },
    }


def update_marketplace(
    marketplace_path: Path,
    marketplace_name: str,
    marketplace_display_name: str,
) -> None:
    """Create or update a native Codex marketplace entry for the plugin."""
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = load_marketplace(
        marketplace_path, marketplace_name, marketplace_display_name
    )
    payload["plugins"] = [
        entry for entry in payload["plugins"] if entry.get("name") != SKILL_NAME
    ]
    payload["plugins"].append(
        {
            "name": SKILL_NAME,
            "source": {"source": "local", "path": f"./plugins/{SKILL_NAME}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }
    )
    marketplace_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def load_marketplace(
    marketplace_path: Path,
    marketplace_name: str,
    marketplace_display_name: str,
) -> dict[str, object]:
    """Load an existing marketplace file or create a default structure."""
    if marketplace_path.exists():
        return json.loads(marketplace_path.read_text(encoding="utf-8"))
    return {
        "name": marketplace_name,
        "interface": {"displayName": marketplace_display_name},
        "plugins": [],
    }


def read_project_version(source_root: Path) -> str:
    """Read the package version from pyproject.toml when available."""
    pyproject_path = source_root / "pyproject.toml"
    if not pyproject_path.exists():
        return "0.1.0"
    marker = 'version = "'
    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(marker):
            return line.split(marker, maxsplit=1)[1].split('"', maxsplit=1)[0]
    return "0.1.0"


def build_github_archive_url(repo: str, ref: str) -> str:
    """Build a GitHub zipball URL from validated repo and ref parts."""
    if not GITHUB_REPO_PATTERN.fullmatch(repo):
        raise ValueError("repo must be in owner/name form")
    if not GITHUB_REF_PATTERN.fullmatch(ref) or ref.startswith("/") or ".." in ref:
        raise ValueError("ref contains unsupported characters")
    return f"https://api.github.com/repos/{repo}/zipball/{ref}"


@contextmanager
def github_source(repo: str, ref: str) -> Iterator[Path]:
    """Download a GitHub archive and yield the extracted repo root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        archive_path = temp_path / "source.zip"
        request = urllib.request.Request(  # noqa: S310
            build_github_archive_url(repo, ref),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "plan-to-project-install",
            },
        )
        with urllib.request.urlopen(request) as response:  # noqa: S310
            archive_path.write_bytes(response.read())
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(temp_path)
        roots = [path for path in temp_path.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Expected exactly one extracted repo root")
        yield roots[0]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the installer."""
    parser = argparse.ArgumentParser(
        description=(
            "Install plan-to-project into Codex, Claude Code, or plugin destinations."
        )
    )
    parser.add_argument(
        "--destination",
        type=InstallDestination,
        choices=list(InstallDestination),
        required=True,
        help="Target installation type.",
    )
    parser.add_argument(
        "--source",
        choices=("github", "local"),
        default="github",
        help="Use the published GitHub repo or the current checkout as the source.",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="GitHub repo in owner/name form when --source=github.",
    )
    parser.add_argument(
        "--ref",
        default="main",
        help="Git ref to install from when --source=github.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Local source root when --source=local.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help="Override CODEX_HOME for home-skill or home-plugin installs.",
    )
    parser.add_argument(
        "--claude-home",
        type=Path,
        default=None,
        help="Override the Claude home directory for claude-skill installs.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Target repo root when --destination=repo-plugin.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing installation if present.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""
    args = parse_args()
    if args.source == "local":
        installed_path = install_from_source(
            source_root=args.source_root.resolve(),
            destination=args.destination,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
            repo_root=args.repo_root,
            force=args.force,
        )
    else:
        with github_source(repo=args.repo, ref=args.ref) as source_root:
            installed_path = install_from_source(
                source_root=source_root,
                destination=args.destination,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
                repo_root=args.repo_root,
                force=args.force,
            )
    print(f"Installed {SKILL_NAME} to {installed_path}")
    print("Restart your agent to pick up the new skill or plugin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
