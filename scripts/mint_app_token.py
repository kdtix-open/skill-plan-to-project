#!/usr/bin/env python3
"""
mint_app_token.py — FR #49: mint a GitHub App installation token.

Reads a GitHub App's private key + App ID, signs a short-lived JWT, and
exchanges it for a 1-hour installation access token scoped to a given
org's installation.  Prints the token to stdout.

Primary use case: unblock plan-to-project + SBR skill runs against
Enterprise-owned orgs where personal fine-grained PATs cannot combine the
required scopes.  The App's installation token inherits whatever permissions
the App was granted on the org — in the `projectit-ai-repo-orchestrator`
case, that's a superset of what the skill needs (Issues r/w, Contents r/w,
Projects r/w, Issue types r/w, Administration r, Copilot metrics r, etc.).

Configuration (env vars or ~/.sdlca/app.conf):

    SDLCA_APP_ID                      GitHub App's numeric ID (required)
    SDLCA_APP_PRIVATE_KEY_PATH        Path to .pem file (required)
    SDLCA_APP_INSTALLATION_ID_<ORG>   Optional per-org installation ID.
                                      Auto-discovered if unset.

Usage:

    python3 -m scripts.mint_app_token kdtix-open
    # → prints token to stdout

    TOKEN=$(python3 -m scripts.mint_app_token kdtix-open)
    GH_TOKEN=$TOKEN gh api /user
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import jwt as _pyjwt
except ImportError:  # pragma: no cover
    print(
        "[mint-app-token] ERROR: PyJWT is not installed.\n"
        "  pip install PyJWT cryptography",
        file=sys.stderr,
    )
    sys.exit(2)


def _load_config() -> tuple[str, Path]:
    """Load App ID + private key path from env / ~/.sdlca/app.conf.

    Returns (app_id, private_key_path).
    Raises SystemExit with helpful message if config incomplete.
    """
    app_id = os.environ.get("SDLCA_APP_ID", "").strip()
    key_path_raw = os.environ.get("SDLCA_APP_PRIVATE_KEY_PATH", "").strip()

    # Fallback: ~/.sdlca/app.conf (shell-style KEY=VALUE)
    conf_path = Path.home() / ".sdlca" / "app.conf"
    if (not app_id or not key_path_raw) and conf_path.is_file():
        for raw_line in conf_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "SDLCA_APP_ID" and not app_id:
                app_id = v
            elif k == "SDLCA_APP_PRIVATE_KEY_PATH" and not key_path_raw:
                key_path_raw = v

    if not app_id:
        print(
            "[mint-app-token] ERROR: SDLCA_APP_ID not set.\n"
            "  Get the App ID from the App's settings page:\n"
            "  https://github.com/organizations/<org>/settings/apps/<app-slug>\n"
            "  Set via env (`export SDLCA_APP_ID=...`) or ~/.sdlca/app.conf",
            file=sys.stderr,
        )
        sys.exit(2)
    if not key_path_raw:
        print(
            "[mint-app-token] ERROR: SDLCA_APP_PRIVATE_KEY_PATH not set.\n"
            "  Download the App's private key (.pem) from the App settings page\n"
            "  and save under ~/.sdlca/ with chmod 0600.\n"
            "  Set via env or ~/.sdlca/app.conf.",
            file=sys.stderr,
        )
        sys.exit(2)

    key_path = Path(os.path.expanduser(key_path_raw))
    if not key_path.is_file():
        print(
            f"[mint-app-token] ERROR: private key not found at {key_path}",
            file=sys.stderr,
        )
        sys.exit(2)
    return app_id, key_path


def _sign_app_jwt(app_id: str, private_key_path: Path) -> str:
    """Sign a short-lived (~10 min) JWT authenticating as the App itself.

    Clock-skew tolerance: -60 seconds on iat.
    Expiry: +540 seconds (9 min); GitHub max is 10 min.
    """
    pem = private_key_path.read_bytes()
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    payload = {
        "iat": int((now - _dt.timedelta(seconds=60)).timestamp()),
        "exp": int((now + _dt.timedelta(seconds=540)).timestamp()),
        "iss": app_id,
    }
    return _pyjwt.encode(payload, pem, algorithm="RS256")


def _http_request(
    url: str, token: str, method: str = "GET"
) -> tuple[int, dict]:
    """Minimal GitHub API call (stdlib urllib; no `requests` dependency)."""
    # URL is always hardcoded https://api.github.com/... from the two call
    # sites below; no user-controlled scheme risk.  S310 false positive.
    req = urllib.request.Request(url, method=method)  # noqa: S310
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return resp.getcode(), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"message": body}
        return exc.code, payload


def _discover_installation_id(app_jwt: str, org: str) -> int:
    """Query GitHub for the App's installation ID on the given org."""
    status, payload = _http_request(
        f"https://api.github.com/orgs/{org}/installation", app_jwt
    )
    if status != 200 or "id" not in payload:
        print(
            f"[mint-app-token] ERROR: could not discover installation "
            f"for org '{org}' (HTTP {status}): {payload.get('message', '?')}",
            file=sys.stderr,
        )
        sys.exit(3)
    return int(payload["id"])


def _mint_installation_token(app_jwt: str, installation_id: int) -> dict:
    """Exchange the App JWT for a 1-hour installation access token."""
    status, payload = _http_request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        app_jwt,
        method="POST",
    )
    if status not in (200, 201) or "token" not in payload:
        print(
            f"[mint-app-token] ERROR: installation-token mint failed "
            f"(HTTP {status}): {payload.get('message', '?')}",
            file=sys.stderr,
        )
        sys.exit(3)
    return payload


def mint_for_org(org: str) -> dict:
    """High-level: returns {'token', 'expires_at', 'installation_id'} dict."""
    app_id, key_path = _load_config()
    app_jwt = _sign_app_jwt(app_id, key_path)

    env_key = f"SDLCA_APP_INSTALLATION_ID_{org.upper().replace('-', '_')}"
    explicit = os.environ.get(env_key, "").strip()
    installation_id = (
        int(explicit) if explicit.isdigit() else _discover_installation_id(app_jwt, org)
    )

    resp = _mint_installation_token(app_jwt, installation_id)
    return {
        "token": resp["token"],
        "expires_at": resp.get("expires_at", ""),
        "installation_id": installation_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mint a GitHub App installation token for a given org."
    )
    parser.add_argument("org", help="Org login (e.g. `kdtix-open`).")
    parser.add_argument(
        "--format",
        choices=("token", "json", "env"),
        default="token",
        help=(
            "Output format: `token` (just the token, default), "
            "`json` (full response with expiry + installation id), "
            "`env` (shell-compatible: GH_TOKEN=... COPILOT_GITHUB_TOKEN=...)."
        ),
    )
    args = parser.parse_args(argv)

    result = mint_for_org(args.org)

    if args.format == "token":
        print(result["token"])
    elif args.format == "json":
        print(json.dumps(result, indent=2))
    else:  # env
        t = result["token"]
        exp = result["expires_at"]
        print(f"export GH_TOKEN={t}")
        print(f"export COPILOT_GITHUB_TOKEN={t}")
        print(f"# expires at {exp} (installation_id={result['installation_id']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
