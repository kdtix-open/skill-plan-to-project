"""Tests for scripts/sbr/investigations.py — the Phase 2a dispatcher."""

from __future__ import annotations

import pytest

from scripts.sbr import investigations
from scripts.sbr.api import Session


def _mk_session(**overrides) -> Session:
    """Helper — build a minimal Session for dispatcher tests."""
    defaults = dict(
        session_id="s1",
        scope_issue_number=100,
        repo="owner/repo",
        created_at="2026-04-24T00:00:00Z",
    )
    defaults.update(overrides)
    return Session(**defaults)


class TestEnvHelpers:
    def test_bridge_url_prefers_sbr_specific_env(self, monkeypatch):
        monkeypatch.setenv("SBR_BRIDGE_URL", "http://sbr-bridge:4318")
        monkeypatch.setenv("SDLCA_LOCAL_EXECUTION_BRIDGE_URL", "http://orch:4318")
        assert investigations.bridge_url_from_env() == "http://sbr-bridge:4318"

    def test_bridge_url_falls_back_to_orchestrator(self, monkeypatch):
        monkeypatch.delenv("SBR_BRIDGE_URL", raising=False)
        monkeypatch.setenv("SDLCA_LOCAL_EXECUTION_BRIDGE_URL", "http://orch:4318")
        assert investigations.bridge_url_from_env() == "http://orch:4318"

    def test_bridge_url_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("SBR_BRIDGE_URL", raising=False)
        monkeypatch.delenv("SDLCA_LOCAL_EXECUTION_BRIDGE_URL", raising=False)
        assert investigations.bridge_url_from_env() is None

    def test_investigations_enabled_accepts_truthy_strings(self, monkeypatch):
        for truthy in ("1", "true", "yes"):
            monkeypatch.setenv("SBR_INVESTIGATIONS_ENABLED", truthy)
            assert investigations.investigations_enabled() is True

    def test_investigations_enabled_rejects_falsy(self, monkeypatch):
        for falsy in ("", "0", "false", "no", "maybe"):
            monkeypatch.setenv("SBR_INVESTIGATIONS_ENABLED", falsy)
            assert investigations.investigations_enabled() is False


class TestExtractSummaryLine:
    def test_picks_up_summary_prefix(self):
        finding = (
            "## Finding\n\nRepo has OIDC stubs at src/bridge/oidc.ts.\n\n"
            "SUMMARY: implemented but not wired to login endpoint."
        )
        assert (
            investigations._extract_summary_line(finding)
            == "implemented but not wired to login endpoint."
        )

    def test_case_insensitive_summary(self):
        finding = "body\n\nsummary: short sentence."
        assert investigations._extract_summary_line(finding) == "short sentence."

    def test_falls_back_to_first_non_blank_line(self):
        finding = "\n\nFirst real line here.\nSecond line."
        assert investigations._extract_summary_line(finding) == "First real line here."

    def test_returns_none_for_empty(self):
        assert investigations._extract_summary_line("") is None
        assert investigations._extract_summary_line("   \n\n") is None


class TestDispatcherDispatch:
    def test_ready_response_persists_investigation_with_summary(self, monkeypatch):
        calls: list[dict] = []

        def fake_post(url, *, json_body, headers, timeout):
            calls.append({"url": url, "body": json_body, "headers": headers})
            return {
                "job_id": "abc-123",
                "tool_kind": "review_repo",
                "status": "ready",
                "model": "claude-sonnet-4-5-20250929",
                "finding": (
                    "## Finding\n\nRepo has OIDC at src/bridge/oidc.ts.\n\n"
                    "SUMMARY: implemented."
                ),
                "error": None,
                "exit_code": 0,
                "stderr_tail": "",
                "duration_ms": 450,
                "cost_usd_estimate": 0.008,
                "dispatched_at": "2026-04-24T00:00:00.000Z",
            }

        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="tok",
            poster=fake_post,
        )
        session = _mk_session()
        result = dispatcher.dispatch(
            session,
            tool_kind="review_repo",
            prompt="does bridge support OIDC?",
            working_directory="/tmp/work",
            issue_number=182,
            subsection_key="success_criteria",
        )

        # POSTed to the right URL with the right body
        assert calls[0]["url"] == "http://host:4318/investigate"
        assert calls[0]["body"]["tool_kind"] == "review_repo"
        assert calls[0]["body"]["issue_number"] == 182
        assert calls[0]["headers"]["x-sdlca-bridge-token"] == "tok"
        # Voice-friendly return
        assert result["status"] == "ready"
        assert result["summary"] == "implemented."
        assert "OIDC" in result["finding"]
        assert result["cost_usd_estimate"] == pytest.approx(0.008)
        # Investigation persisted on the session
        assert len(session.investigations) == 1
        inv = session.investigations[0]
        assert inv.job_id == "abc-123"
        assert inv.status == "ready"
        assert inv.tool_kind == "review_repo"
        assert inv.context["issue_number"] == 182
        assert inv.summary == "implemented."

    def test_failed_response_persists_failed_investigation(self):
        def fake_post(url, *, json_body, headers, timeout):
            return {
                "job_id": "fail-1",
                "tool_kind": "review_repo",
                "status": "failed",
                "model": "claude-sonnet-4-5-20250929",
                "finding": None,
                "error": "claude exited 1",
                "exit_code": 1,
                "stderr_tail": "auth failure",
                "duration_ms": 100,
                "cost_usd_estimate": 0,
                "dispatched_at": "2026-04-24T00:00:00.000Z",
            }

        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="tok",
            poster=fake_post,
        )
        session = _mk_session()
        result = dispatcher.dispatch(
            session,
            tool_kind="review_repo",
            prompt="q",
            working_directory="/tmp/work",
        )

        assert result["status"] == "failed"
        assert "claude exited 1" in result["error"]
        assert "Inspect docker logs" in result["message"]
        assert len(session.investigations) == 1
        assert session.investigations[0].status == "failed"

    def test_bridge_unreachable_records_failed_investigation(self):
        def raising_post(*args, **kwargs):
            raise ConnectionError("could not connect to bridge")

        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="tok",
            poster=raising_post,
        )
        session = _mk_session()
        result = dispatcher.dispatch(
            session,
            tool_kind="research",
            prompt="q",
            working_directory="/tmp/work",
        )

        assert result["status"] == "failed"
        assert "bridge unreachable" in result["error"]
        assert len(session.investigations) == 1
        assert session.investigations[0].status == "failed"

    def test_missing_bridge_url_raises_with_remediation(self):
        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="",
            bridge_token="tok",
        )
        session = _mk_session()
        with pytest.raises(RuntimeError, match="Bridge URL is not configured"):
            dispatcher.dispatch(
                session,
                tool_kind="review_repo",
                prompt="q",
                working_directory="/tmp/work",
            )

    def test_missing_bridge_token_raises_with_remediation(self):
        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="",
        )
        session = _mk_session()
        with pytest.raises(RuntimeError, match="Bridge token is not configured"):
            dispatcher.dispatch(
                session,
                tool_kind="review_repo",
                prompt="q",
                working_directory="/tmp/work",
            )

    def test_passes_optional_args_when_provided(self):
        captured = {}

        def fake_post(url, *, json_body, headers, timeout):
            captured["body"] = json_body
            return {
                "job_id": "j",
                "status": "ready",
                "model": "m",
                "finding": "ok",
                "exit_code": 0,
                "duration_ms": 1,
                "cost_usd_estimate": 0,
            }

        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="tok",
            poster=fake_post,
        )
        session = _mk_session()
        dispatcher.dispatch(
            session,
            tool_kind="research",
            prompt="q",
            working_directory="/tmp/work",
            model="claude-opus-4-7-max",
            allowed_tools=["WebFetch"],
            issue_number=357,
            subsection_key="assumptions",
            from_bookmark_label="disp-1",
        )

        assert captured["body"]["model"] == "claude-opus-4-7-max"
        assert captured["body"]["allowed_tools"] == ["WebFetch"]
        assert captured["body"]["issue_number"] == 357
        assert session.investigations[0].from_bookmark_label == "disp-1"

    def test_does_not_send_optional_fields_when_omitted(self):
        captured = {}

        def fake_post(url, *, json_body, headers, timeout):
            captured["body"] = json_body
            return {
                "job_id": "j",
                "status": "ready",
                "model": "m",
                "finding": "ok",
                "exit_code": 0,
                "duration_ms": 1,
                "cost_usd_estimate": 0,
            }

        dispatcher = investigations.InvestigationDispatcher(
            bridge_url="http://host:4318",
            bridge_token="tok",
            poster=fake_post,
        )
        session = _mk_session()
        dispatcher.dispatch(
            session,
            tool_kind="review_repo",
            prompt="q",
            working_directory="/tmp/work",
        )

        assert "model" not in captured["body"]
        assert "allowed_tools" not in captured["body"]
        assert "issue_number" not in captured["body"]
        assert "subsection_key" not in captured["body"]
