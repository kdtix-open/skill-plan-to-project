"""SBR Investigation Sub-Agent — Python dispatcher.

Phase 2a wiring (2026-04-24 overnight full-auto run).  MCP tool handlers
in scripts/sbr/mcp_server.py call into InvestigationDispatcher when
SBR_INVESTIGATIONS_ENABLED is truthy; this module POSTs to the
orchestrator's local-execution bridge at /investigate and returns the
resulting Investigation dataclass after persisting it to session.

Design doc:  docs/plans/SBR Voice Agent Investigation Sub-Agent.md
Bridge side: src/bridge/investigation.ts

## Architecture (synchronous Phase 2a)

    voice agent (browser)
         │  MCP sbr_review_repo(...)
         ▼
    sbr-mcp (Python, this module)
         │  HTTP POST /investigate  {tool_kind, prompt, cwd, model?, ...}
         ▼
    local bridge (Node, host)
         │  spawn claude --print --allowed-tools ... --append-system-prompt ...
         ▼
    claude CLI (operator's subscription)
         │  findings
         ▲
         │ wait ~30-90s
         │ return InvestigateResult
         │  persist Investigation to session.investigations
         │  voice agent reads finding + narrates

## Provider ownership

Per operator decision E (2026-04-23 autonomy charter): investigation
calls run through the operator's LOCAL bridge using their own Anthropic
subscription.  KDTIX does NOT pay for these calls.  Code marked with
TODO(kdtix-subscription) needs revision when subscription tier ships.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Literal, Protocol

from scripts.sbr.api import Investigation, Session

log = logging.getLogger("sbr-mcp.investigations")

# ---------------------------------------------------------------------------
# Environment-driven configuration
# ---------------------------------------------------------------------------


def bridge_url_from_env() -> str | None:
    """Resolve the bridge base URL (e.g. http://host.docker.internal:4318).

    Checks SBR_BRIDGE_URL first (explicit override for the sbr-mcp
    container), then SDLCA_LOCAL_EXECUTION_BRIDGE_URL (shared with the
    orchestrator's worker dispatch path).  Returns None when neither
    is set — caller should raise a clear error with remediation hint.
    """
    return (
        os.environ.get("SBR_BRIDGE_URL")
        or os.environ.get("SDLCA_LOCAL_EXECUTION_BRIDGE_URL")
        or None
    )


def bridge_token_from_env() -> str | None:
    """Resolve the bridge Bearer/x-sdlca-bridge-token."""
    return (
        os.environ.get("SBR_BRIDGE_TOKEN")
        or os.environ.get("SDLCA_LOCAL_EXECUTION_BRIDGE_TOKEN")
        or None
    )


def investigations_enabled() -> bool:
    """True when SBR_INVESTIGATIONS_ENABLED is set to a truthy value.

    Kept as a module-level helper (not a constant) so tests can toggle
    via monkeypatch on os.environ.
    """
    return os.environ.get("SBR_INVESTIGATIONS_ENABLED", "").strip() in (
        "1",
        "true",
        "yes",
    )


# ---------------------------------------------------------------------------
# HTTP poster — injected so tests can mock without hitting the network
# ---------------------------------------------------------------------------


class HttpPoster(Protocol):
    """Protocol for making a POST to the bridge's /investigate endpoint.

    Production impl: `urllib_post`.  Tests pass a fake that returns a
    canned response.
    """

    def __call__(
        self,
        url: str,
        *,
        json_body: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        """Return the decoded JSON response body."""
        ...


def urllib_post(
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Production HTTP POSTer using stdlib urllib.

    Chosen over requests to keep the skill's dependency footprint
    minimal; the skill is pip-installed into a slim container and
    extra deps inflate image size.
    """
    payload = json.dumps(json_body).encode("utf-8")
    # S310 (audit URL scheme): URL is env-sourced (SBR_BRIDGE_URL /
    # SDLCA_LOCAL_EXECUTION_BRIDGE_URL) and controlled by the operator,
    # not end-user input.  Accepting file:// here would be a config
    # mistake but not an attack vector.  Accept the noqa + document.
    req = urllib.request.Request(  # noqa: S310
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        # 4xx/5xx — bridge returned an error body; surface it to caller.
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"error": body}
        parsed.setdefault("_status", exc.code)
        return parsed


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


ToolKind = Literal["review_repo", "review_plan", "research", "review_issues"]


class InvestigationDispatcher:
    """Dispatch SBR Investigation Sub-Agent calls via the local bridge.

    Wraps the HTTP POST + Investigation dataclass persistence.  Each
    call:

    1. Builds the bridge request payload
    2. POSTs to /investigate (synchronous — bridge blocks on claude)
    3. Parses the InvestigateResult
    4. Persists an Investigation to session.investigations
    5. Returns a voice-agent-friendly summary dict

    Construction is cheap — create one per MCP server bootstrap or
    per-call, either works.  State is session JSON + the underlying
    bridge; the dispatcher itself is stateless.
    """

    def __init__(
        self,
        bridge_url: str | None = None,
        bridge_token: str | None = None,
        poster: HttpPoster | None = None,
        default_timeout: float = 150.0,
    ) -> None:
        self._bridge_url = (bridge_url or bridge_url_from_env() or "").rstrip("/")
        self._bridge_token = bridge_token or bridge_token_from_env() or ""
        self._post = poster or urllib_post
        self._timeout = default_timeout

    # -- public API ---------------------------------------------------------

    def dispatch(
        self,
        session: Session,
        *,
        tool_kind: ToolKind,
        prompt: str,
        working_directory: str,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        issue_number: int | None = None,
        subsection_key: str | None = None,
        from_bookmark_label: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single investigation synchronously + return a summary.

        Raises RuntimeError with a clear remediation hint when the bridge
        URL / token isn't configured.  All other failures (claude exits
        non-zero, timeout, malformed response) get recorded as a
        failed Investigation in session.investigations so the operator
        can still see the attempt via sbr_list_investigations.
        """
        if not self._bridge_url:
            raise RuntimeError(
                "Bridge URL is not configured.  Set SBR_BRIDGE_URL or "
                "SDLCA_LOCAL_EXECUTION_BRIDGE_URL in the sbr-mcp "
                "container environment (typically "
                "http://host.docker.internal:4318 for Docker)."
            )
        if not self._bridge_token:
            raise RuntimeError(
                "Bridge token is not configured.  Set SBR_BRIDGE_TOKEN "
                "or SDLCA_LOCAL_EXECUTION_BRIDGE_TOKEN."
            )

        request_body: dict[str, Any] = {
            "tool_kind": tool_kind,
            "prompt": prompt,
            "working_directory": working_directory,
        }
        if model is not None:
            request_body["model"] = model
        if allowed_tools is not None:
            request_body["allowed_tools"] = allowed_tools
        if issue_number is not None:
            request_body["issue_number"] = issue_number
        if subsection_key is not None:
            request_body["subsection_key"] = subsection_key

        url = f"{self._bridge_url}/investigate"
        t0 = _dt.datetime.now(_dt.timezone.utc)
        log.info(
            "dispatching investigation",
            extra={
                "tool_kind": tool_kind,
                "issue_number": issue_number,
                "subsection_key": subsection_key,
                "model": model,
                "url": url,
            },
        )

        try:
            response = self._post(
                url,
                json_body=request_body,
                headers={"x-sdlca-bridge-token": self._bridge_token},
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — intentional broad catch
            log.warning("bridge POST failed", extra={"err": str(exc)})
            response = {
                "status": "failed",
                "error": f"bridge unreachable: {exc}",
                "exit_code": -1,
            }

        # Build the Investigation dataclass from the response + request.
        investigation = _response_to_investigation(
            request_body=request_body,
            response=response,
            dispatched_at=t0.isoformat(),
            from_bookmark_label=from_bookmark_label,
        )
        session.investigations.append(investigation)

        return _voice_friendly_summary(investigation)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response_to_investigation(
    *,
    request_body: dict[str, Any],
    response: dict[str, Any],
    dispatched_at: str,
    from_bookmark_label: str | None,
) -> Investigation:
    """Reshape the bridge's InvestigateResult into the skill's Investigation."""
    tool_kind = request_body["tool_kind"]
    status_raw = response.get("status")
    # Map bridge responses to the dataclass's allowed values.
    if status_raw == "ready":
        status: Literal["pending", "running", "ready", "consumed", "failed"] = "ready"
    elif status_raw == "failed":
        status = "failed"
    else:
        # Defensive — if the bridge returned something unexpected,
        # treat as failed so the operator sees it.
        status = "failed"

    finding = response.get("finding")
    if status == "ready" and isinstance(finding, str):
        summary = _extract_summary_line(finding)
    else:
        summary = None

    return Investigation(
        job_id=str(response.get("job_id") or uuid.uuid4()),
        tool_kind=tool_kind,
        prompt=request_body["prompt"],
        context={
            k: v
            for k, v in request_body.items()
            if k in {"working_directory", "issue_number", "subsection_key"}
        },
        model=str(response.get("model") or request_body.get("model") or "unknown"),
        provider="claude",
        status=status,
        dispatched_at=dispatched_at,
        completed_at=_dt.datetime.now(_dt.timezone.utc).isoformat()
        if status in ("ready", "failed")
        else None,
        finding=finding if isinstance(finding, str) else None,
        error=response.get("error") if status == "failed" else None,
        cost_usd_estimate=float(response.get("cost_usd_estimate") or 0.0),
        from_bookmark_label=from_bookmark_label,
        summary=summary,
        act_on_suggestion=None,  # Phase 2b — parse from finding
    )


def _extract_summary_line(finding: str) -> str | None:
    """Pull the `SUMMARY:` line out of the sub-agent's markdown finding.

    System prompts instruct the sub-agent to end with a one-sentence
    `SUMMARY:` line suitable for voice narration.  If that line is
    present, return it (without the SUMMARY: prefix).  Otherwise
    fall back to the first paragraph.
    """
    for line in finding.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("SUMMARY:"):
            return stripped.split(":", 1)[1].strip() or None
    # Fallback — first non-blank line, capped at 200 chars.
    for line in finding.splitlines():
        if line.strip():
            return line.strip()[:200]
    return None


def _voice_friendly_summary(investigation: Investigation) -> dict[str, Any]:
    """Shape the MCP response so the voice agent can narrate naturally."""
    base: dict[str, Any] = {
        "job_id": investigation.job_id,
        "status": investigation.status,
        "tool_kind": investigation.tool_kind,
        "model": investigation.model,
        "cost_usd_estimate": investigation.cost_usd_estimate,
    }
    if investigation.status == "ready":
        base["summary"] = investigation.summary
        base["finding"] = investigation.finding
    elif investigation.status == "failed":
        base["error"] = investigation.error
        base["message"] = (
            f"Investigation failed: {investigation.error}.  "
            "Inspect docker logs sbr-mcp for bridge-side diagnostics."
        )
    return base
