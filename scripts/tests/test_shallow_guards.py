"""Tests for the three --allow-shallow-subsections enforcement guards.

Implements the test plan from kdtix-open/skill-plan-to-project#68:

  Guard A (5 tests): --shallow-justification "<≥30 chars>" requirement +
    persistence in manifest + body marker + label apply.
  Guard B (4 tests): JSONL audit log write + dir creation + token-kind
    detection + tail CLI.
  Guard C (5 tests): fail-closed refresh on marked-shallow subtrees +
    --close-shallow-debt graduation verb.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts import audit_log, manifest_io

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_audit_dir(tmp_path, monkeypatch):
    audit_dir = tmp_path / "sdlca_audit"
    monkeypatch.setenv("SDLCA_AUDIT_DIR", str(audit_dir))
    monkeypatch.chdir(tmp_path)
    return audit_dir


@pytest.fixture
def shallow_plan(tmp_path):
    """A plan markdown file whose Stories are missing required subsections."""
    plan = tmp_path / "shallow.md"
    plan.write_text(
        "# Project Scope: Shallow Test\n"
        "\n"
        "Business problem: TBD\n"
        "Success Criteria: TBD\n"
        "Assumptions: TBD\n"
        "Out of Scope: TBD\n"
        "I Know I Am Done When: TBD\n"
        "\n"
        "## Initiative: One\n"
        "\n"
        "Objective: TBD\n"
        "Release Value: TBD\n"
        "Success Criteria: TBD\n"
        "Feature Scope: TBD\n"
        "I Know I Am Done When: TBD\n"
        "\n"
        "### Epic: First\n"
        "\n"
        "Objective: TBD\n"
        "Release Value: TBD\n"
        "Success Criteria: TBD\n"
        "I Know I Am Done When: TBD\n"
        "\n"
        "#### User Story: shallow-story-no-subsections\n"
        "\n"
        "Just a sentence.  No required Story subsections present.\n",
        encoding="utf-8",
    )
    return plan


# ---------------------------------------------------------------------------
# Guard A — --shallow-justification requirement
# ---------------------------------------------------------------------------


class TestGuardA_Justification:
    def test_create_shallow_without_justification_exits_2(self, isolated_audit_dir, shallow_plan):
        from scripts import create_issues

        with pytest.raises(SystemExit) as excinfo:
            create_issues.validate_shallow_justification(
                allow_shallow=True, justification=None, command="create"
            )
        assert excinfo.value.code == 2

    def test_short_justification_under_30_chars_exits_2(self, isolated_audit_dir):
        from scripts import create_issues

        with pytest.raises(SystemExit) as excinfo:
            create_issues.validate_shallow_justification(
                allow_shallow=True, justification="too short", command="create"
            )
        assert excinfo.value.code == 2

    def test_30_plus_char_justification_passes(self, isolated_audit_dir):
        from scripts import create_issues

        # Should not raise.
        create_issues.validate_shallow_justification(
            allow_shallow=True,
            justification="A" * 31 + " — Stage-0 recon, deep authoring deferred",
            command="create",
        )

    def test_manifest_records_justification(self, isolated_audit_dir, tmp_path):
        from scripts import create_issues

        out = tmp_path / "out"
        out.mkdir()
        manifest = {"scope-1": {"number": 1, "level": "scope", "title": "x"}}
        justification = "Stage-0 recon for OH-001 hot-lane; depth backfill filed as #NNN"
        create_issues.write_manifest_with_shallow_metadata(
            manifest=manifest,
            output_dir=out,
            justification=justification,
        )
        data = json.loads((out / "manifest.json").read_text())
        assert data["shallow_subsections_justification"] == justification

    def test_issue_body_carries_marker_with_verbatim_justification(self, isolated_audit_dir):
        from scripts import create_issues

        justification = "Stage-0 recon — full Stage 1-4 backfill tracked as #NNN"
        body = "## Some Section\nbody text.\n"
        wrapped = create_issues.inject_shallow_marker(body, justification)
        assert "shallow-subsections" in wrapped
        assert justification in wrapped
        assert "R-19" in wrapped


# ---------------------------------------------------------------------------
# Guard B — P0 JSONL audit log
# ---------------------------------------------------------------------------


class TestGuardB_AuditLog:
    def test_emit_writes_jsonl_line(self, isolated_audit_dir):
        entry = audit_log.emit_audit_entry(
            command="create",
            plan_path="/tmp/p.md",
            scope_issue=271,
            repo="kdtix-open/agent-project-queue",
            shallow_items=[{"level": "epic", "title": "X", "missing": ["objective"]}],
            justification="Stage-0 recon — backfill tracked elsewhere; ≥30 chars present",
        )
        path = audit_log.audit_log_path()
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        for key in (
            "ts",
            "actor_user",
            "actor_token_kind",
            "command",
            "plan_path",
            "scope_issue",
            "repo",
            "shallow_items",
            "justification",
            "remediation_issue",
            "remediation_resolved",
            "severity",
            "skill_version",
            "command_invocation",
        ):
            assert key in loaded, f"missing key: {key}"
        assert loaded["severity"] == "P0"
        assert loaded["scope_issue"] == 271
        assert entry["repo"] == loaded["repo"]

    def test_audit_dir_auto_created_with_mode_0700(self, tmp_path, monkeypatch):
        audit_dir = tmp_path / "fresh_dir"
        monkeypatch.setenv("SDLCA_AUDIT_DIR", str(audit_dir))
        assert not audit_dir.exists()
        audit_log.emit_audit_entry(
            command="create",
            plan_path="x",
            scope_issue=None,
            repo="r/n",
            shallow_items=[],
            justification="x" * 40,
        )
        assert audit_dir.exists()
        # 0o700 — owner-only.
        mode = audit_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"expected 0700 mode, got {oct(mode)}"

    @pytest.mark.parametrize(
        "token,expected",
        [
            ("ghs_abc123", "github-app"),
            ("ghp_abc123", "pat"),
            ("github_pat_xx", "pat"),
            ("oauth_legacy_token", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_token_kind_detection(self, token, expected):
        assert audit_log.detect_actor_token_kind(token) == expected

    def test_tail_cli_pretty_prints_last_n(self, isolated_audit_dir, capsys):
        for i in range(5):
            audit_log.emit_audit_entry(
                command="create",
                plan_path=f"/tmp/p{i}.md",
                scope_issue=100 + i,
                repo="r/n",
                shallow_items=[],
                justification="justification number " + str(i) + " (≥30 chars padding here ok)",
            )
        rc = audit_log.main(["tail", "-n", "3"])
        assert rc == 0
        captured = capsys.readouterr().out
        # last 3 → scope_issue 102, 103, 104
        assert "#102" in captured
        assert "#103" in captured
        assert "#104" in captured
        assert "#100" not in captured


# ---------------------------------------------------------------------------
# Guard C — fail-closed refresh + graduation verb
# ---------------------------------------------------------------------------


class TestGuardC_FailClosedRefresh:
    def test_marked_shallow_blocks_refresh_when_plan_still_fails_and_no_justification(
        self, isolated_audit_dir, shallow_plan
    ):
        from scripts import create_issues

        manifest_io.mark_shallow(
            scope_issue=271,
            justification="prior shallow create — Stage-0 recon for OH-001",
            repo="kdtix-open/x",
            plan_path=str(shallow_plan),
            actor="tester@example.com",
        )
        with pytest.raises(SystemExit) as excinfo:
            create_issues.enforce_shallow_refresh_gate(
                plan_path=str(shallow_plan),
                scope_issue=271,
                repo="kdtix-open/x",
                allow_shallow=False,
                justification=None,
                command="refresh",
            )
        assert excinfo.value.code == 2

    def test_fresh_justification_proceeds_and_emits_new_p0_audit(
        self, isolated_audit_dir, shallow_plan
    ):
        from scripts import create_issues

        manifest_io.mark_shallow(
            scope_issue=272,
            justification="prior shallow create",
            repo="kdtix-open/x",
            plan_path=str(shallow_plan),
            actor="tester@example.com",
        )
        # Should NOT raise.
        create_issues.enforce_shallow_refresh_gate(
            plan_path=str(shallow_plan),
            scope_issue=272,
            repo="kdtix-open/x",
            allow_shallow=True,
            justification="Re-acknowledged bypass — depth still pending in #NNN; ≥30 chars",
            command="refresh",
        )
        entries = audit_log.read_entries()
        assert any(e["scope_issue"] == 272 and e["severity"] == "P0" for e in entries)

    def test_clean_plan_does_not_block_even_when_marker_present(
        self, isolated_audit_dir, tmp_path, shallow_plan
    ):
        """If the marker is set but the plan now passes the gate, no block."""
        from scripts import create_issues

        manifest_io.mark_shallow(
            scope_issue=273,
            justification="prior shallow create",
            repo="kdtix-open/x",
            plan_path=str(shallow_plan),
            actor="tester@example.com",
        )
        # Mock the gate so it reports clean.
        with patch.object(create_issues, "_compute_subsection_gaps", return_value=[]):
            create_issues.enforce_shallow_refresh_gate(
                plan_path=str(shallow_plan),
                scope_issue=273,
                repo="kdtix-open/x",
                allow_shallow=False,
                justification=None,
                command="refresh",
            )

    def test_close_shallow_debt_with_failing_plan_exits_2(
        self, isolated_audit_dir, shallow_plan
    ):
        from scripts import create_issues

        manifest_io.mark_shallow(
            scope_issue=274,
            justification="prior shallow create",
            repo="kdtix-open/x",
            plan_path=str(shallow_plan),
            actor="tester@example.com",
        )
        with pytest.raises(SystemExit) as excinfo:
            create_issues.close_shallow_debt(
                plan_path=str(shallow_plan),
                scope_issue=274,
                repo="kdtix-open/x",
                apply=False,
            )
        assert excinfo.value.code == 2
        # Marker MUST remain set.
        data = manifest_io.load_manifest(274)
        assert data["shallow_subsections"] is True

    def test_close_shallow_debt_with_clean_plan_clears_marker_and_emits_audit(
        self, isolated_audit_dir, shallow_plan
    ):
        from scripts import create_issues

        manifest_io.mark_shallow(
            scope_issue=275,
            justification="prior shallow create",
            repo="kdtix-open/x",
            plan_path=str(shallow_plan),
            actor="tester@example.com",
        )
        with patch.object(create_issues, "_compute_subsection_gaps", return_value=[]):
            create_issues.close_shallow_debt(
                plan_path=str(shallow_plan),
                scope_issue=275,
                repo="kdtix-open/x",
                apply=True,
            )
        data = manifest_io.load_manifest(275)
        assert data["shallow_subsections"] is False
        assert "shallow_debt_closed_at" in data
        assert data["shallow_debt_closed_by"]
        entries = audit_log.read_entries()
        assert any(
            e["scope_issue"] == 275 and e["command"] == "shallow_debt_closed"
            for e in entries
        )
