"""SBR MCP Server — exposes the SBR API as MCP tools.

Primary operator interface for Stage 1 MVP.  Claude App (with Voice),
Claude CLI, Codex, Cursor, and VS Code all drive review via the same
tool surface.

11 canonical tools mirror the CLI subcommands:
  sbr_start_session      / sbr_session_status
  sbr_next_subsection    / sbr_current_subsection_verbatim
  sbr_approve            / sbr_improve             / sbr_skip
  sbr_pause              / sbr_resume              / sbr_write_back
  sbr_terminate

Uses the `mcp` Python SDK (https://github.com/modelcontextprotocol/python-sdk).
If `mcp` is not available at import time, the module prints a clear
remediation hint + exits when `main()` is called.

## Transports (Story #393 — Stage 1.5 Voice Pilot)

- `stdio` (default): Claude App / Claude CLI local path; no auth needed (local
  trust).  `sbr-mcp-server` with no flags runs in this mode.
- `streamable-http`: hosted consumption (web browser calling from
  `dev.projectit.ai/tools/sbr`).  Requires `--auth-token` (Bearer).  Uses
  FastMCP's streamable-http transport on `/mcp` by default.
- `sse`: older SSE transport; also requires `--auth-token`.  Kept for
  backwards compatibility with MCP clients that don't yet support
  streamable-http.

Bearer-token auth uses `hmac.compare_digest` for constant-time comparison to
prevent timing side-channels on token validation.  The expected token is
typically a GitHub App installation token (1-hour TTL, rotated by cron);
`sbr-mcp-server` itself doesn't mint tokens — upstream infra (cron +
`mint_app_token.py`) is responsible for populating the env var that
`--auth-token` reads.
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # pragma: no cover — import-time graceful failure
    AccessToken = None  # type: ignore[assignment, misc]
    AuthSettings = None  # type: ignore[assignment, misc]
    FastMCP = None  # type: ignore[assignment]
    TokenVerifier = object  # type: ignore[assignment, misc]
    TransportSecuritySettings = None  # type: ignore[assignment, misc]

from scripts.sbr.api import SessionManager, WriteBacker


class BearerTokenVerifier:
    """Constant-time Bearer token verifier for hosted HTTP/SSE transport.

    Implements the MCP SDK's `TokenVerifier` protocol.  Only accepts tokens
    that match `expected_token` exactly (compared via `hmac.compare_digest`
    to prevent timing side-channels).

    An empty `expected_token` rejects every request — defense-in-depth so a
    misconfigured deployment doesn't accidentally open an anonymous
    endpoint.
    """

    def __init__(self, expected_token: str) -> None:
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> Any:
        """Protocol: return AccessToken on success, None on failure."""
        if not self.expected_token:
            return None
        if not token:
            return None
        if not hmac.compare_digest(token, self.expected_token):
            return None
        if AccessToken is None:  # pragma: no cover — stdio-only install
            return None
        return AccessToken(
            token=token,
            client_id="sbr-hosted-consumer",
            scopes=["sbr:review"],
            expires_at=None,
            resource=None,
        )


def _build_server(
    auth_token: str | None = None,
    resource_server_url: str = "http://127.0.0.1:3456/",
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> FastMCP:  # type: ignore[name-defined]
    """Construct the FastMCP server + register the 11 canonical tools.

    Delegates every tool to `scripts.sbr.api` primitives; no business logic
    lives in the MCP layer.  Sticky-session support uses the same
    `~/.sbr/current-session.txt` file as the CLI.

    Args:
        auth_token: If provided, installs a BearerTokenVerifier that accepts
            only this exact token for hosted HTTP/SSE transport.  None for
            stdio (local trust; no auth).
        resource_server_url: Public URL of this MCP server — included in
            AuthSettings for OAuth protected-resource metadata.  Defaults
            to localhost for local dev; override to the hosted URL in
            production (e.g. "https://dev.projectit.ai/mcp/sbr/").
        allowed_hosts: Host header values that pass FastMCP's DNS-rebinding
            protection.  Defaults to ["127.0.0.1:*", "localhost:*"] when
            unset; override to include public hostnames behind a reverse
            proxy (e.g. ["dev.projectit.ai", "127.0.0.1:*"]).  Empty list =
            protection disabled entirely (not recommended).
        allowed_origins: Origin header values that pass DNS-rebinding
            protection.  Browsers send `Origin:` on cross-origin (and many
            same-origin) fetches — FastMCP rejects every Origin when the
            allowlist is empty, so hosted consumers MUST populate this.
            Default ["http://127.0.0.1:*", "http://localhost:*"].
    """
    log = logging.getLogger("sbr-mcp.build_server")
    if FastMCP is None:
        raise RuntimeError(
            "The `mcp` Python SDK is not installed.\n"
            "  pip install mcp  (see https://github.com/modelcontextprotocol/python-sdk)"
        )

    kwargs: dict[str, Any] = {}
    if auth_token:
        kwargs["token_verifier"] = BearerTokenVerifier(expected_token=auth_token)
        # FastMCP requires AuthSettings when a token_verifier is present.
        # For simple Bearer flows (non-OAuth), we pass placeholder URLs —
        # no OAuth discovery / DCR is performed because we don't provide an
        # auth_server_provider.  The resource_server_url matches this
        # server's public URL so clients know where the resource lives.
        kwargs["auth"] = AuthSettings(
            issuer_url=resource_server_url,
            resource_server_url=resource_server_url,
            required_scopes=["sbr:review"],
        )
        log.info(
            "Bearer auth installed", extra={"resource_server_url": resource_server_url}
        )

    # DNS-rebinding protection — default to loopback+localhost, let callers
    # add public hostnames.  `127.0.0.1:*` matches any port; `localhost:*`
    # ditto.  Browsers send Origin headers with scheme+host (no port for
    # :80/:443); we include http/https variants.
    effective_hosts = (
        allowed_hosts if allowed_hosts is not None else ["127.0.0.1:*", "localhost:*"]
    )
    effective_origins = (
        allowed_origins
        if allowed_origins is not None
        else ["http://127.0.0.1:*", "http://localhost:*"]
    )
    if TransportSecuritySettings is not None:
        if effective_hosts or effective_origins:
            kwargs["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=effective_hosts,
                allowed_origins=effective_origins,
            )
            log.info(
                "DNS-rebinding protection ON",
                extra={
                    "allowed_hosts": effective_hosts,
                    "allowed_origins": effective_origins,
                },
            )
        else:
            kwargs["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
            log.warning("DNS-rebinding protection DISABLED (empty allowlists)")

    mcp = FastMCP("sbr", **kwargs)
    mgr = SessionManager()

    @mcp.tool()
    def sbr_start_session(
        scope_issue_number: int | None = None,
        repo: str | None = None,
        skip_issues: list[int] | None = None,
        # --- Aliases for voice-agent flexibility ------------------------
        # The voice model often picks natural-sounding names.  Accept the
        # variants observed in production + normalize to the canonical
        # fields below.  Docstring warns the model NOT to use these, but
        # if it does, we find the intent instead of hard-failing.
        scope_id: int | None = None,
        scope: int | None = None,
        issue_number: int | None = None,
        item_id: int | None = None,
        organization: str | None = None,
        organisation: str | None = None,  # British spelling
        org: str | None = None,
        repository: str | None = None,
        repo_name: str | None = None,
        queue_name: str | None = None,
        project_queue: str | None = None,
        project_queue_name: str | None = None,
    ) -> dict[str, Any]:
        """Start a new Sprint Backlog Review session rooted at a Project Scope issue.

        CANONICAL args (use these — do NOT use aliases):
            scope_issue_number: The Project Scope issue number (e.g. 182).
            repo: owner/name in GitHub's format (e.g. "kdtix-open/agent-project-queue").

        Aliases accepted for voice-agent flexibility — the model should
        still prefer the canonical names above, but these variants are
        tolerated so one slip doesn't kill the session:
            scope_id, scope, issue_number, item_id → scope_issue_number
            organization + repository, org + repo_name → joined as owner/name
            queue_name, project_queue(_name) → repo (if already in owner/name form)

        PHONETIC variations the voice STT sometimes produces:
            "KD-TX-Open", "KDTIX OPEN", "KAYDEETIX-OPEN" → kdtix-open
            Always lowercase + hyphenate.

        Returns:
            {session_id, queue_size, scope_issue_number, repo,
             warning?: str if queue is empty}
        """
        tool_log = logging.getLogger("sbr-mcp.sbr_start_session")

        # Normalize scope_issue_number aliases — pick the first non-None.
        scope_issue_number = (
            scope_issue_number or scope_id or scope or issue_number or item_id
        )
        if scope_issue_number is None:
            raise ValueError(
                "scope_issue_number is required — the GitHub issue number "
                "of the Project Scope to review (e.g. 182).  Pass as "
                "`scope_issue_number=182`.  "
                "Also accepted (will be normalized): scope_id, scope, "
                "issue_number, item_id."
            )

        # Normalize repo aliases.
        if not repo:
            org_value = organization or organisation or org
            repo_part = repository or repo_name
            # Case A: split org + short repo name → join with slash.
            if org_value and repo_part and "/" not in repo_part:
                repo = f"{org_value}/{repo_part}"
                tool_log.info(
                    "normalized split organization+repository to repo",
                    extra={"repo": repo},
                )
            # Case B: the "repository" arg already contains a slash
            # (full owner/name form) → accept as-is.  This was a bug
            # discovered during 2026-04-23 UAT: model passed
            # repository="kdtix-open/agent-project-queue" with no
            # organization, and the code fell through to the queue_name
            # branch + left repo=None.
            elif repo_part and "/" in repo_part:
                repo = repo_part
                tool_log.info(
                    "accepting repository arg as already-slashed repo",
                    extra={"repo": repo},
                )
            else:
                repo = queue_name or project_queue or project_queue_name

        # STT often produces caps-y variants ("KDTIX-open/Agent-Project-QUE")
        # because it treats the hyphenated word as an abbreviation.  The
        # GitHub-canonical form is lowercase.  Lowercase defensively —
        # GitHub is case-insensitive on repo names anyway, but the
        # capitalization difference can trip up cached lookups.
        if repo and isinstance(repo, str):
            lower = repo.lower()
            if lower != repo:
                tool_log.info(
                    "normalized STT caps in repo",
                    extra={"before": repo, "after": lower},
                )
                repo = lower

        # Validate repo format up-front so operators get a clear error
        # instead of a silent empty queue (the server would otherwise
        # walk a nonexistent hierarchy and return queue_size=0).
        if not isinstance(repo, str) or "/" not in repo or " " in repo:
            tool_log.warning(
                "rejected invalid repo format",
                extra={"repo": repo, "scope": scope_issue_number},
            )
            reason = (
                "missing slash separator"
                if repo and "/" not in str(repo)
                else "contains whitespace"
                if repo
                else "repo argument is missing entirely"
            )
            raise ValueError(
                f"Invalid repo format: {repo!r}.  Expected owner/name "
                f"(e.g. 'kdtix-open/agent-project-queue').  "
                f"Received: {repo!r} — {reason}.  "
                f"Retry with repo='kdtix-open/agent-project-queue' "
                f"(org + slash + repo name, no spaces).  "
                f"If you heard 'kdtix-open agent-project-queue' or "
                f"'KD-TX-Open agent-project-queue' (STT variation), "
                f"normalize to lowercase + slash."
            )

        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise ValueError(
                f"Invalid repo format: {repo!r}.  Both org and repo name "
                f"required, separated by /.  Received: owner={owner!r}, name={name!r}."
            )

        # Automatic preflight — fail loud on expired tokens instead of
        # silently returning queue_size=0.  Operator's Stage 1.5 UAT
        # showed "No children found under scope #182" when the token
        # was actually just expired; we now surface that as a hard error
        # with remediation text the voice agent can narrate.
        import shutil
        import subprocess

        if shutil.which("gh"):
            try:
                probe = subprocess.run(
                    ["gh", "api", f"repos/{repo}", "--jq", ".full_name"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if probe.returncode != 0:
                    err = probe.stderr.strip()
                    if "bad credentials" in err.lower():
                        raise ValueError(
                            f"Preflight FAILED: GitHub App installation token "
                            f"is expired (caps at 60 min).  Restart the sbr-mcp "
                            f"container to pick up the rotated token from "
                            f".env.sbr, OR run "
                            f"scripts/hosted-sbr/refresh-sbr-token.sh.  "
                            f"Raw gh error: {err[:200]}"
                        )
                    if "not found" in err.lower() or probe.returncode == 1:
                        raise ValueError(
                            f"Preflight FAILED: cannot read {repo!r}.  "
                            f"Either the repo doesn't exist under that owner, "
                            f"or the GitHub App isn't installed on it.  "
                            f"Raw gh error: {err[:200]}"
                        )
                    raise ValueError(
                        f"Preflight FAILED: unexpected gh error on "
                        f"{repo!r}.  {err[:200]}"
                    )
            except subprocess.TimeoutExpired:
                tool_log.warning(
                    "preflight probe timed out — proceeding anyway",
                    extra={"repo": repo},
                )

        tool_log.info(
            "starting session",
            extra={
                "scope_issue_number": scope_issue_number,
                "repo": repo,
                "skip_count": len(skip_issues or ()),
            },
        )
        session = mgr.start(
            scope_issue_number, repo, skip_issues=set(skip_issues or set())
        )
        queue_size = len(session.issues)
        tool_log.info(
            "session started",
            extra={
                "session_id": session.session_id,
                "queue_size": queue_size,
                "repo": repo,
            },
        )
        result: dict[str, Any] = {
            "session_id": session.session_id,
            "queue_size": queue_size,
            "scope_issue_number": session.scope_issue_number,
            "repo": session.repo,
            "status": session.status,
        }
        # queue_size == 0 is almost always a symptom of a wrong repo OR a
        # scope issue that has no children yet — surface a warning the
        # model can narrate to the operator.
        if queue_size == 0:
            result["warning"] = (
                f"Scope #{scope_issue_number} in {repo} has no child issues "
                f"to review.  Either the scope number is wrong, the repo "
                f"name is wrong, or the scope genuinely has no children. "
                f"Ask the operator to verify the scope + repo before "
                f"continuing."
            )
        return result

    @mcp.tool()
    def sbr_preflight(repo: str | None = None) -> dict[str, Any]:
        """Diagnose service health BEFORE starting a review session.

        Runs a small suite of connectivity checks + returns a dict the
        voice agent can narrate to the operator.  Covers:
          - GitHub App installation token validity (gh api)
          - gh CLI presence + basic auth
          - (optional) Read access to the target repo if `repo` is passed
          - Timestamp of the last token rotation

        Use BEFORE sbr_start_session if the operator says "can we begin",
        "are we ready", "is everything working", etc.  Also called
        automatically inside sbr_start_session — a failing preflight
        aborts the session start with a helpful ValueError.

        Args:
            repo: Optional "owner/name" to probe for read access.

        Returns:
            dict with keys:
              ok                (bool)        — all checks passed
              checks            (list[dict])  — per-check results
              remediation       (str | None)  — one-line fix if ok=False
              suggested_args    (dict | None) — hint for sbr_start_session
        """
        import shutil
        import subprocess
        import time

        tool_log = logging.getLogger("sbr-mcp.sbr_preflight")
        checks: list[dict[str, Any]] = []
        ok = True
        remediation: str | None = None

        # 1. gh CLI present?
        gh_path = shutil.which("gh")
        checks.append(
            {
                "name": "gh_cli_installed",
                "ok": bool(gh_path),
                "detail": gh_path or "gh not on PATH",
            }
        )
        if not gh_path:
            ok = False
            remediation = (
                "gh CLI is missing from the server image — "
                "operator should check the sbr-mcp Dockerfile."
            )

        # 2. gh can authenticate with the env GH_TOKEN?
        gh_auth_ok = False
        gh_auth_detail = ""
        if gh_path:
            try:
                r = subprocess.run(
                    ["gh", "api", "repos/octocat/hello-world", "--jq", ".id"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                gh_auth_ok = r.returncode == 0 and r.stdout.strip().isdigit()
                gh_auth_detail = (
                    r.stdout.strip()
                    if gh_auth_ok
                    else (r.stderr.strip() or "no stderr")
                )
            except subprocess.TimeoutExpired:
                gh_auth_detail = "gh api timed out after 5s"
            except OSError as exc:
                gh_auth_detail = f"{type(exc).__name__}: {exc}"
        checks.append(
            {
                "name": "gh_token_valid",
                "ok": gh_auth_ok,
                "detail": gh_auth_detail[:200] if gh_auth_detail else "",
            }
        )
        if gh_path and not gh_auth_ok:
            ok = False
            if "bad credentials" in gh_auth_detail.lower():
                remediation = (
                    "GitHub App installation token is expired (GitHub caps "
                    "at 60 min).  Restart the sbr-mcp container to pick up "
                    "the rotated token from .env.sbr, OR wait for the "
                    "health-probe-daemon's 45-min auto-refresh."
                )
            else:
                remediation = (
                    "gh api probe failed.  Check GH_TOKEN env in the "
                    "sbr-mcp container (should match SBR_AUTH_TOKEN); "
                    "gh auth status for additional detail."
                )

        # 3. Optional: target repo readable?
        target_repo_ok: bool | None = None
        target_repo_detail = ""
        if repo and gh_auth_ok:
            if "/" not in repo:
                target_repo_ok = False
                target_repo_detail = "repo missing slash separator"
            else:
                try:
                    r = subprocess.run(
                        ["gh", "api", f"repos/{repo}", "--jq", ".full_name"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    target_repo_ok = r.returncode == 0 and "/" in r.stdout
                    target_repo_detail = (
                        r.stdout.strip()
                        if target_repo_ok
                        else (r.stderr.strip() or "repo not accessible")
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    target_repo_ok = False
                    target_repo_detail = f"{type(exc).__name__}: {exc}"
            checks.append(
                {
                    "name": "target_repo_readable",
                    "ok": target_repo_ok,
                    "detail": target_repo_detail[:200],
                }
            )
            if not target_repo_ok:
                ok = False
                remediation = remediation or (
                    f"Cannot read {repo!r} with current token.  Verify the "
                    f"App is installed on the repo + the token scope "
                    f"includes it."
                )

        # 4. Token freshness — container doesn't see host .env.sbr; the
        # best signal is the container's own process-start time, which
        # aligns with when env_file was last read (on container restart).
        token_age_sec: int | None = None
        try:
            with open("/proc/1/stat") as f:
                stat_parts = f.read().split()
            # /proc/1/stat field 22 = starttime in clock ticks since boot
            ticks = int(stat_parts[21])
            clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            with open("/proc/uptime") as f:
                boot_up = float(f.read().split()[0])
            boot_epoch = time.time() - boot_up
            start_epoch = boot_epoch + (ticks / clk)
            token_age_sec = int(time.time() - start_epoch)
        except (OSError, ValueError, IndexError):
            token_age_sec = None
        checks.append(
            {
                "name": "token_age_under_60min",
                "ok": token_age_sec is not None and token_age_sec < 3300,
                "detail": (
                    f"{token_age_sec}s"
                    if token_age_sec is not None
                    else "could not measure"
                ),
            }
        )
        if token_age_sec is not None and token_age_sec >= 3300:
            remediation = remediation or (
                f"Token is {token_age_sec}s old — nearing or past "
                f"GitHub's 1h cap.  The health-probe-daemon will refresh "
                f"within 45 min; force a refresh now via "
                f"scripts/hosted-sbr/refresh-sbr-token.sh."
            )
            if token_age_sec >= 3600:
                ok = False

        suggested_args: dict[str, Any] | None = None
        if ok:
            # Guide the voice agent to the canonical call shape.
            suggested_args = {
                "scope_issue_number": "<integer>",
                "repo": "<owner/name>",
            }

        tool_log.info(
            "preflight",
            extra={"ok": ok, "gh_auth_ok": gh_auth_ok, "repo": repo},
        )
        return {
            "ok": ok,
            "checks": checks,
            "remediation": remediation,
            "suggested_args": suggested_args,
        }

    # -----------------------------------------------------------------
    # Aliases for common voice-model hallucinations — delegate to the
    # canonical tool above.  Registering them as real tools (instead of
    # fighting via system prompt alone) lets the model use its natural
    # phrasing without a failure round-trip.  Every UAT run has started
    # with the model calling start_sbr_review because that matches the
    # operator's spoken phrase "start SBR review".
    # -----------------------------------------------------------------

    @mcp.tool()
    def sbr_review(
        scope_issue_number: int | None = None,
        repo: str | None = None,
        skip_issues: list[int] | None = None,
        scope_id: int | None = None,
        scope: int | None = None,
        issue_number: int | None = None,
        item_id: int | None = None,
        organization: str | None = None,
        organisation: str | None = None,
        org: str | None = None,
        repository: str | None = None,
        repo_name: str | None = None,
        queue_name: str | None = None,
        project_queue: str | None = None,
        project_queue_name: str | None = None,
    ) -> dict[str, Any]:
        """Alias for sbr_start_session — matches the 2nd-try hallucination
        'sbr_review' seen in 2026-04-23 UAT.  Same args, same behavior."""
        return sbr_start_session(
            scope_issue_number=scope_issue_number,
            repo=repo,
            skip_issues=skip_issues,
            scope_id=scope_id,
            scope=scope,
            issue_number=issue_number,
            item_id=item_id,
            organization=organization,
            organisation=organisation,
            org=org,
            repository=repository,
            repo_name=repo_name,
            queue_name=queue_name,
            project_queue=project_queue,
            project_queue_name=project_queue_name,
        )

    @mcp.tool()
    def start_sbr_review(
        scope_issue_number: int | None = None,
        repo: str | None = None,
        skip_issues: list[int] | None = None,
        scope_id: int | None = None,
        scope: int | None = None,
        issue_number: int | None = None,
        item_id: int | None = None,
        organization: str | None = None,
        organisation: str | None = None,
        org: str | None = None,
        repository: str | None = None,
        repo_name: str | None = None,
        queue_name: str | None = None,
        project_queue: str | None = None,
        project_queue_name: str | None = None,
    ) -> dict[str, Any]:
        """Alias for sbr_start_session — matches the operator's natural
        phrase "start SBR review".  Same args, same behavior.  The
        voice agent often prefers this name; both are supported."""
        return sbr_start_session(
            scope_issue_number=scope_issue_number,
            repo=repo,
            skip_issues=skip_issues,
            scope_id=scope_id,
            scope=scope,
            issue_number=issue_number,
            item_id=item_id,
            organization=organization,
            organisation=organisation,
            org=org,
            repository=repository,
            repo_name=repo_name,
            queue_name=queue_name,
            project_queue=project_queue,
            project_queue_name=project_queue_name,
        )

    @mcp.tool()
    def sbr_next_subsection(session_id: str) -> dict[str, Any]:
        """Advance to the next pending subsection in the session.

        Returns a summary describing the current issue + subsection + whether
        content is present.  When the session has no more pending subsections,
        `has_next` is False.
        """
        session = mgr.load(session_id)
        pair = mgr.get_current_subsection(session)
        mgr._atomic_write(session)  # persist lazy-populated subsections
        if pair is None:
            return {
                "has_next": False,
                "status": session.status,
                "message": (
                    "Session complete.  Run sbr_write_back to commit approved "
                    "verdicts, or sbr_terminate to discard."
                ),
            }
        issue, sub = pair
        return {
            "has_next": True,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_level": issue.level,
            "subsection_key": sub.key,
            "has_content": bool(sub.original_content.strip()),
            "content_length": len(sub.original_content),
        }

    @mcp.tool()
    def sbr_current_subsection_verbatim(session_id: str) -> dict[str, Any]:
        """Return the verbatim (unsummarized) content of the current subsection.

        Use when the operator asks to hear the original text instead of an
        LLM-summarized version.
        """
        session = mgr.load(session_id)
        pair = mgr.get_current_subsection(session)
        if pair is None:
            return {"has_current": False, "content": ""}
        _issue, sub = pair
        return {
            "has_current": True,
            "subsection_key": sub.key,
            "content": sub.original_content,
        }

    @mcp.tool()
    def sbr_approve(session_id: str) -> dict[str, Any]:
        """Approve the current subsection as-is + advance."""
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "approved")
        return {"status": "approved", "session_status": session.status}

    @mcp.tool()
    def sbr_improve(
        session_id: str,
        new_content: str | None = None,
        # Aliases the voice model commonly produces instead of new_content.
        # See ARGUMENT NAMING cheatsheet in the system prompt.
        suggestion: str | None = None,
        suggested_content: str | None = None,
        new_text: str | None = None,
        content: str | None = None,
        improvement: str | None = None,
        text: str | None = None,
        body: str | None = None,
        prose: str | None = None,
        replacement: str | None = None,
        updated_content: str | None = None,
    ) -> dict[str, Any]:
        """Replace the current subsection with improved content + advance.

        CANONICAL signature:
            sbr_improve(session_id: str, new_content: str)

        Aliases accepted (normalized to new_content):
            suggestion, suggested_content, new_text, content, improvement,
            text, body, prose, replacement, updated_content
        """
        resolved_content = (
            new_content
            or suggestion
            or suggested_content
            or new_text
            or content
            or improvement
            or text
            or body
            or prose
            or replacement
            or updated_content
        )
        if not resolved_content:
            raise ValueError(
                "sbr_improve requires the replacement prose.  Pass it as "
                "new_content (e.g. sbr_improve(session_id='abc', "
                "new_content='...')).  Aliases also accepted: suggestion, "
                "suggested_content, new_text, content, improvement, text, "
                "body, prose, replacement, updated_content."
            )
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "improved", improved_content=resolved_content)
        return {
            "status": "improved",
            "session_status": session.status,
            "content_length": len(resolved_content),
        }

    @mcp.tool()
    def sbr_skip(session_id: str) -> dict[str, Any]:
        """Skip the current subsection (leave as-is) + advance."""
        session = mgr.load(session_id)
        mgr.apply_verdict(session, "skipped")
        return {"status": "skipped", "session_status": session.status}

    @mcp.tool()
    def sbr_previous(session_id: str) -> dict[str, Any]:
        """Move cursor back one subsection and clear its verdict.

        Use when the operator says "go back", "previous", "last
        section", "let me redo that".  Crosses issue boundaries.  The
        previous subsection's verdict is cleared so the operator can
        re-approve/re-improve/re-skip cleanly.
        """
        session = mgr.load(session_id)
        mgr.go_back(session)
        pair = mgr.get_current_subsection(session)
        if pair is None:
            return {
                "status": "at_beginning",
                "session_status": session.status,
            }
        issue, sub = pair
        return {
            "status": "moved_back",
            "session_status": session.status,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_level": issue.level,
            "subsection_key": sub.key,
        }

    @mcp.tool()
    def sbr_goto(
        session_id: str,
        issue_number: int,
        subsection_key: str | None = None,
    ) -> dict[str, Any]:
        """Jump the cursor to a specific issue (and optional subsection).

        Use when the operator says "jump to issue 200", "go to the done
        when section on issue 183", etc.  Clears the verdict on the
        target subsection so it can be re-verdicted.
        """
        session = mgr.load(session_id)
        found = mgr.goto(
            session,
            issue_number=issue_number,
            subsection_key=subsection_key,
        )
        if not found:
            return {
                "status": "not_found",
                "issue_number": issue_number,
                "session_status": session.status,
                "message": (
                    f"Issue #{issue_number} is not in this session's queue.  "
                    f"Check the scope number + sbr_session_status for the "
                    f"queue listing."
                ),
            }
        pair = mgr.get_current_subsection(session)
        if pair is None:
            return {"status": "moved", "session_status": session.status}
        issue, sub = pair
        return {
            "status": "moved",
            "session_status": session.status,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_level": issue.level,
            "subsection_key": sub.key,
        }

    @mcp.tool()
    def sbr_pause(session_id: str) -> dict[str, Any]:
        """Pause the session (preserves cursor for resume)."""
        session = mgr.load(session_id)
        mgr.pause(session)
        return {"status": "paused", "session_status": session.status}

    @mcp.tool()
    def sbr_resume(session_id: str) -> dict[str, Any]:
        """Resume a paused session."""
        session = mgr.load(session_id)
        mgr.resume_session(session)
        return {"status": "active", "session_status": session.status}

    @mcp.tool()
    def sbr_terminate(session_id: str) -> dict[str, Any]:
        """Terminate a session without write-back (discards verdicts)."""
        session = mgr.load(session_id)
        mgr.terminate(session)
        return {"status": "terminated", "session_status": session.status}

    @mcp.tool()
    def sbr_session_status(session_id: str) -> dict[str, Any]:
        """Return the session's current progress snapshot."""
        session = mgr.load(session_id)
        approved = sum(i.approved_count for i in session.issues)
        improved = sum(i.improved_count for i in session.issues)
        skipped = sum(i.skipped_count for i in session.issues)
        return {
            "session_id": session.session_id,
            "status": session.status,
            "current_issue_index": session.current_issue_index,
            "total_issues": len(session.issues),
            "approved": approved,
            "improved": improved,
            "skipped": skipped,
        }

    @mcp.tool()
    def sbr_write_back(session_id: str) -> dict[str, Any]:
        """Commit approved verdicts for all completed issues in the session."""
        session = mgr.load(session_id)
        results: list[dict[str, Any]] = []
        for issue in session.issues:
            if issue.write_back_completed:
                continue
            if not any(
                s.verdict in ("approved", "improved") for s in issue.subsections
            ):
                continue
            results.append(WriteBacker.write_back_issue(session, issue))
        mgr._atomic_write(session)
        return {"write_back_count": len(results), "results": results}

    return mcp


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbr-mcp-server",
        description=(
            "SBR MCP server — stdio (default, for Claude App) or "
            "streamable-http/sse (for hosted consumers like "
            "dev.projectit.ai/tools/sbr)."
        ),
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "streamable-http", "sse"),
        help=(
            "Transport mode.  stdio = Claude App + local CLIs (default).  "
            "streamable-http = hosted web consumers.  sse = legacy HTTP "
            "streaming.  HTTP modes require --auth-token."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3456,
        help="Port for HTTP/SSE transports (ignored for stdio).  Default: 3456.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind address for HTTP/SSE transports (default: 127.0.0.1 — "
            "bind 0.0.0.0 only behind a reverse proxy)."
        ),
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help=(
            "Bearer token for HTTP/SSE transports.  Typically a GitHub App "
            "installation token rotated by cron.  REQUIRED for HTTP/SSE; "
            "ignored (and unused) for stdio."
        ),
    )
    parser.add_argument(
        "--mount-path",
        default="/",
        help="Mount path for HTTP/SSE transports.  Default: /",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        help=(
            "Allowed Host header for HTTP/SSE transport (repeatable).  "
            "Used by FastMCP's DNS-rebinding protection.  Default when "
            "unset: 127.0.0.1:* + localhost:*.  Add public hostnames "
            "when fronted by a reverse proxy, e.g. --allowed-host "
            "dev.projectit.ai --allowed-host 127.0.0.1:*"
        ),
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        help=(
            "Allowed Origin header for HTTP/SSE transport (repeatable).  "
            "Browsers send Origin on fetches; FastMCP rejects every "
            "non-allowed Origin when the allowlist is non-empty.  Default "
            "when unset: http://127.0.0.1:* + http://localhost:*.  Add "
            "your public origin for hosted deployments, e.g. "
            "--allowed-origin https://dev.projectit.ai"
        ),
    )
    # Observability per .github/docs/standards/observability-and-logging.md.
    parser.add_argument(
        "--verbose",
        type=int,
        choices=(0, 1, 2, 3),
        default=None,
        help=(
            "Log verbosity: 0=errors only, 1=info (default), 2=debug, "
            "3=trace (payloads).  Env fallback: VERBOSE.  Writes to "
            "stderr AND logs/sbr-mcp-server-<YYYY-MM-DD>.log always."
        ),
    )
    parser.add_argument(
        "--debug",
        type=int,
        choices=(0, 1, 2, 3),
        default=None,
        help=(
            "Debug detail level (implies --verbose 2 minimum).  Env "
            "fallback: DEBUG.  Level 3 emits full HTTP bodies + inter-"
            "service calls into logs/sbr-mcp-server-trace-<YYYY-MM-DD>.log"
        ),
    )
    parser.add_argument(
        "--logs-dir",
        default=None,
        help=(
            "Directory for log files.  Defaults to `logs/` under the "
            "current working directory.  Created automatically at "
            "startup — UAT never has to create it.  Env fallback: "
            "SBR_LOGS_DIR."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Structured logging — .github/docs/standards/observability-and-logging.md
# ---------------------------------------------------------------------------

TRACE_LEVEL_NUM = 5  # custom level below DEBUG (10)
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def _log_trace(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kwargs)  # type: ignore[attr-defined]


logging.Logger.trace = _log_trace  # type: ignore[attr-defined]


def _log_file_path(
    logs_dir: Path, service: str = "sbr-mcp-server", suffix: str = ""
) -> Path:
    """Return the dated log file path.  Creates logs_dir if missing."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    infix = f"-{suffix}" if suffix else ""
    return logs_dir / f"{service}{infix}-{date_str}.log"


def _configure_logging(verbose: int, debug: int, logs_dir: Path) -> None:
    """Configure root logger per the observability standard.

    - `verbose=0` → errors only (to stderr + file)
    - `verbose=1` → info (default)
    - `verbose=2` or `debug>=1` → debug
    - `verbose=3` or `debug>=3` → trace (full payloads, separate file)
    """
    effective = max(verbose, 2 if debug >= 1 else 0, 3 if debug >= 3 else 0)
    level_map = {
        0: logging.ERROR,
        1: logging.INFO,
        2: logging.DEBUG,
        3: TRACE_LEVEL_NUM,
    }
    log_level = level_map.get(min(effective, 3), logging.INFO)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(log_level)
    # Clear any handlers a parent process installed.
    root.handlers.clear()

    # Console (stderr) — always on
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)

    # Main log file — always on, standard + info
    main_path = _log_file_path(logs_dir)
    main_handler = logging.FileHandler(main_path)
    main_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(main_handler)

    # Debug log file — when --debug >= 1
    if debug >= 1:
        debug_path = _log_file_path(logs_dir, suffix="debug")
        debug_handler = logging.FileHandler(debug_path)
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(debug_handler)

    # Trace log file — when --verbose 3 or --debug 3 (full payloads)
    if effective >= 3:
        trace_path = _log_file_path(logs_dir, suffix="trace")
        trace_handler = logging.FileHandler(trace_path)
        trace_handler.setLevel(TRACE_LEVEL_NUM)
        trace_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(trace_handler)

    log = logging.getLogger("sbr-mcp")
    log.info(
        "Logging configured",
        extra={
            "verbose": verbose,
            "debug": debug,
            "effective_level": logging.getLevelName(log_level),
            "main_log": str(main_path),
        },
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `sbr-mcp-server` console script.

    Parses CLI args + dispatches to the correct FastMCP transport.  stdio
    runs locally for Claude App; streamable-http + sse serve hosted
    consumers with Bearer-token auth.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # HTTP/SSE modes MUST have an auth token — refuse to open an anonymous
    # network endpoint by mistake.
    if args.transport in ("streamable-http", "sse") and not args.auth_token:
        print(
            f"[sbr-mcp-server] --auth-token is required for --transport "
            f"{args.transport}.\n"
            f"  Missing: mint an App installation token + pass it explicitly.\n"
            f"  Example: sbr-mcp-server --transport streamable-http "
            f"--auth-token $GH_TOKEN",
            file=sys.stderr,
        )
        return 2

    # Observability — resolve verbose/debug from CLI flags, env, or defaults.
    verbose = (
        args.verbose if args.verbose is not None else int(os.environ.get("VERBOSE", 1))
    )
    debug = args.debug if args.debug is not None else int(os.environ.get("DEBUG", 0))
    logs_dir = (
        Path(args.logs_dir or os.environ.get("SBR_LOGS_DIR") or "logs")
        .expanduser()
        .resolve()
    )
    _configure_logging(verbose=verbose, debug=debug, logs_dir=logs_dir)
    log = logging.getLogger("sbr-mcp.main")

    log.info(
        "Starting sbr-mcp-server",
        extra={
            "transport": args.transport,
            "host": args.host,
            "port": args.port,
            "allowed_hosts": args.allowed_hosts,
            "allowed_origins": args.allowed_origins,
        },
    )

    try:
        auth_token = args.auth_token if args.transport != "stdio" else None
        mcp = _build_server(
            auth_token=auth_token,
            allowed_hosts=args.allowed_hosts,
            allowed_origins=args.allowed_origins,
        )
    except RuntimeError as exc:
        log.error("build_server failed: %s", exc, exc_info=True)
        print(f"[sbr-mcp-server] {exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        log.info("Running in stdio mode — waiting for Claude App / CLI to connect")
        mcp.run(transport="stdio")
    else:
        # FastMCP reads host/port/mount_path from init kwargs for HTTP modes;
        # _build_server doesn't set them, so we override at run-time via the
        # FastMCP settings object.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        if args.mount_path != "/":
            mcp.settings.mount_path = args.mount_path
        log.info(
            "Running HTTP/SSE transport",
            extra={"bind": f"{args.host}:{args.port}", "mount_path": args.mount_path},
        )
        mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
