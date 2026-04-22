"""Tests for scripts/sbr/cli.py — CLI subcommand dispatch."""

from __future__ import annotations

import json
from unittest.mock import patch

from scripts.sbr import api, cli


class TestCliStickySession:
    def test_start_writes_current_session_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))

        with patch.object(
            api,
            "_walk_existing_hierarchy",
            return_value=[
                {
                    "number": 100,
                    "title": "Scope",
                    "level": "scope",
                    "parent_number": None,
                }
            ],
        ):
            rc = cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )

        assert rc == 0
        current = (tmp_path / "current.txt").read_text()
        assert current, "sticky session file should be written"
        # JSON output is valid
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["queue_size"] == 1

    def test_next_uses_sticky_session(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))

        with (
            patch.object(
                api,
                "_walk_existing_hierarchy",
                return_value=[
                    {
                        "number": 100,
                        "title": "Scope",
                        "level": "scope",
                        "parent_number": None,
                    }
                ],
            ),
            patch.object(api, "get_issue_body", return_value="Vision text.\n"),
        ):
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            capsys.readouterr()  # flush start output
            rc = cli.main(["--format", "json", "next"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["has_next"] is True
        assert payload["issue_number"] == 100
        assert payload["subsection_key"] == "vision"

    def test_approve_advances_cursor(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))
        with (
            patch.object(
                api,
                "_walk_existing_hierarchy",
                return_value=[
                    {
                        "number": 100,
                        "title": "Scope",
                        "level": "scope",
                        "parent_number": None,
                    }
                ],
            ),
            patch.object(api, "get_issue_body", return_value="Vision text.\n"),
        ):
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            cli.main(["--format", "json", "next"])
            capsys.readouterr()
            rc = cli.main(["--format", "json", "approve"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "approved"

    def test_improve_reads_from_stdin_on_dash(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))
        import io

        with (
            patch.object(
                api,
                "_walk_existing_hierarchy",
                return_value=[
                    {
                        "number": 100,
                        "title": "Scope",
                        "level": "scope",
                        "parent_number": None,
                    }
                ],
            ),
            patch.object(api, "get_issue_body", return_value="Vision text.\n"),
        ):
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            cli.main(["--format", "json", "next"])
            capsys.readouterr()
            monkeypatch.setattr("sys.stdin", io.StringIO("improved vision text"))
            rc = cli.main(["--format", "json", "improve", "-"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "improved"


class TestCliLifecycle:
    """Coverage for pause / resume / terminate / status / verbatim / write-back."""

    def _setup_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))
        walker = patch.object(
            api,
            "_walk_existing_hierarchy",
            return_value=[
                {
                    "number": 100,
                    "title": "Scope",
                    "level": "scope",
                    "parent_number": None,
                }
            ],
        )
        body = patch.object(api, "get_issue_body", return_value="Vision text.\n")
        return walker, body

    def test_status_of_fresh_session(self, tmp_path, monkeypatch, capsys):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body:
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            capsys.readouterr()
            rc = cli.main(["--format", "json", "status"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total_issues"] == 1
        assert payload["approved"] == 0

    def test_pause_then_resume(self, tmp_path, monkeypatch, capsys):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body:
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            capsys.readouterr()
            cli.main(["--format", "json", "pause"])
            payload_paused = json.loads(capsys.readouterr().out)
            assert payload_paused["status"] == "paused"
            cli.main(["--format", "json", "resume"])
            payload_resumed = json.loads(capsys.readouterr().out)
            assert payload_resumed["status"] == "active"

    def test_terminate(self, tmp_path, monkeypatch, capsys):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body:
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            capsys.readouterr()
            rc = cli.main(["--format", "json", "terminate"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "terminated"

    def test_verbatim_prints_raw_content(self, tmp_path, monkeypatch, capsys):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body:
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            cli.main(["--format", "json", "next"])
            capsys.readouterr()
            rc = cli.main(["verbatim"])
        assert rc == 0
        out = capsys.readouterr().out
        # Vision section is empty in the mock body → "(empty)"
        assert out.strip() == "(empty)"

    def test_skip_advances(self, tmp_path, monkeypatch, capsys):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body:
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            cli.main(["--format", "json", "next"])
            capsys.readouterr()
            rc = cli.main(["--format", "json", "skip"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "skipped"

    def test_write_back_with_no_verdicts_writes_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        walker, body = self._setup_session(tmp_path, monkeypatch)
        with walker, body, patch.object(api, "update_issue_body"):
            cli.main(
                ["--format", "json", "start", "--scope", "100", "--repo", "owner/repo"]
            )
            capsys.readouterr()
            rc = cli.main(["--format", "json", "write-back"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["write_back_count"] == 0


class TestCliStatus:
    def test_status_with_no_session_exits(self, tmp_path, monkeypatch, capsys):
        import pytest

        monkeypatch.setenv("SBR_SESSIONS_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("SBR_CURRENT_SESSION_FILE", str(tmp_path / "current.txt"))
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["status"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "No session" in err
