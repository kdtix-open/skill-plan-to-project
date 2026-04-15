import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = ROOT / "site"


def test_pages_site_files_exist() -> None:
    assert (SITE_ROOT / "index.html").exists()
    assert (SITE_ROOT / "styles.css").exists()
    assert (SITE_ROOT / "app.js").exists()
    assert (SITE_ROOT / "assets" / "plugin-icon.png").exists()
    assert (SITE_ROOT / "assets" / "team.png").exists()
    assert (SITE_ROOT / "assets" / "early-access.png").exists()
    assert (SITE_ROOT / "assets" / "maintainer.png").exists()
    assert (SITE_ROOT / "assets" / "insider-access.png").exists()


def test_pages_site_content_covers_install_and_support() -> None:
    html = (SITE_ROOT / "index.html").read_text(encoding="utf-8")

    assert "skills.projectit.ai" in html
    assert "plan-to-project-install --destination home-skill" in html
    assert "plan-to-project-install --destination home-plugin" in html
    assert "--destination repo-plugin --repo-root /path/to/repo" in html
    assert "Support on Patreon" in html
    assert "Become a Patron" in html
    assert "https://www.patreon.com/5419581/join" in html
    assert "Join the KDTIX Insider Program" in html
    assert (
        "https://donate.stripe.com/"
        "cNi6oG973dks4mOfLv9AA00?client_reference_id="
        "kdtix-open-skill-plan-to-project" in html
    )


def test_package_json_has_pages_scripts() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_json["scripts"]

    assert scripts["dev:site"] == "wrangler pages dev site"
    assert (
        scripts["deploy:site"]
        == "wrangler pages deploy site --project-name skills-projectit-ai --branch main"
    )
    assert (
        scripts["create:site"]
        == "wrangler pages project create skills-projectit-ai --production-branch main"
    )


def test_heading_typography_has_room_for_descenders() -> None:
    css = (SITE_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "line-height: 1.12;" in css
    assert "line-height: 1.1;" in css
    assert "font-size: 1rem;" in css
