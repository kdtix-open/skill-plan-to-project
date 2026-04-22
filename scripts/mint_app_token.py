#!/usr/bin/env python3
"""
mint_app_token.py — FR #49: mint a GitHub App installation token.

Reads a GitHub App's private key + App ID, signs a short-lived JWT, and
exchanges it for a 1-hour installation access token scoped to a given
org's installation.  Prints the token to stdout.

Primary use case: unblock plan-to-project + SBR skill runs against
Enterprise-owned orgs where personal fine-grained PATs cannot combine the
required scopes.  The App's installation token inherits whatever permissions
the App was granted on the org — typically a superset of what the skill
needs (Issues r/w, Contents r/w, Projects r/w, Issue types r/w, etc.).

Configuration (checked in order):

  1. Env vars: GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY (inline PEM)
     — the names already used in ~/.sdlca/bridge/.env.credentials by the
     bridge runtime.  Preferred — no separate config needed.
  2. Env vars: SDLCA_APP_ID / SDLCA_APP_PRIVATE_KEY_PATH (file path)
     — legacy names; still supported for operators who prefer a separate
     .pem on disk.
  3. File: ~/.sdlca/app.conf (shell KEY=VALUE format) — fallback.
  4. File: ~/.sdlca/bridge/.env.credentials — auto-sourced if none of
     the above are set (bridge's existing credentials file).

    GITHUB_APP_ID / SDLCA_APP_ID               GitHub App's numeric ID
    GITHUB_APP_PRIVATE_KEY                     PEM content (inline env var;
                                               preferred)
    SDLCA_APP_PRIVATE_KEY_PATH                 Path to .pem file (alt)
    SDLCA_APP_INSTALLATION_ID_<ORG>            Optional per-org installation ID.
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


def _parse_shell_env_file(path: Path) -> dict[str, str]:
    """Parse a bash-style KEY=VALUE env file into a dict.

    Handles:
    - Simple KEY=value
    - KEY="value with spaces"
    - KEY='value with spaces'
    - Multi-line values when wrapped in double quotes (PEM keys)
    - Leading `export ` prefix
    - Blank lines + lines starting with `#`

    Does NOT evaluate shell expressions (safer than `source`ing).
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    i = 0
    lines = text.splitlines(keepends=False)
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        key = key.strip()
        val = val  # keep whitespace around quotes as-is for now
        # If value starts with `"` but doesn't close on the same line,
        # collect subsequent lines until we find the closing `"`.
        if val.startswith('"') and not (
            val.endswith('"') and len(val) > 1 and val[-2] != "\\"
        ):
            buf = [val[1:]]  # strip opening quote
            while i < len(lines):
                nxt = lines[i]
                i += 1
                if nxt.endswith('"'):
                    buf.append(nxt[:-1])
                    break
                buf.append(nxt)
            val = "\n".join(buf)
        elif val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = val[1:-1]
        elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
            val = val[1:-1]
        else:
            val = val.strip()
        result[key] = val
    return result


def _load_config() -> tuple[str, bytes, str]:
    """Resolve App ID + private-key bytes from one of several sources.

    Resolution order:
      1. Env: GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY (inline PEM; preferred)
      2. Env: SDLCA_APP_ID + SDLCA_APP_PRIVATE_KEY_PATH (file path)
      3. File: ~/.sdlca/app.conf
      4. File: ~/.sdlca/bridge/.env.credentials (the bridge's own credentials)

    Returns (app_id, private_key_pem_bytes, source_desc).

    Raises SystemExit(2) with helpful remediation when none resolve.
    """
    app_id = (
        os.environ.get("GITHUB_APP_ID", "").strip()
        or os.environ.get("SDLCA_APP_ID", "").strip()
    )
    inline_pem = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
    key_path_raw = os.environ.get("SDLCA_APP_PRIVATE_KEY_PATH", "").strip()

    source_desc_parts: list[str] = []
    if app_id:
        source_desc_parts.append("env(app_id)")
    if inline_pem:
        source_desc_parts.append("env(inline_pem)")
    if key_path_raw:
        source_desc_parts.append("env(key_path)")

    if not app_id or not (inline_pem or key_path_raw):
        # Fallback 1: ~/.sdlca/app.conf
        conf_path = Path.home() / ".sdlca" / "app.conf"
        for k, v in _parse_shell_env_file(conf_path).items():
            if k == "GITHUB_APP_ID" and not app_id:
                app_id = v
                source_desc_parts.append("app.conf(app_id)")
            elif k == "SDLCA_APP_ID" and not app_id:
                app_id = v
                source_desc_parts.append("app.conf(app_id)")
            elif k == "GITHUB_APP_PRIVATE_KEY" and not inline_pem:
                inline_pem = v
                source_desc_parts.append("app.conf(inline_pem)")
            elif k == "SDLCA_APP_PRIVATE_KEY_PATH" and not key_path_raw:
                key_path_raw = v
                source_desc_parts.append("app.conf(key_path)")

    if not app_id or not (inline_pem or key_path_raw):
        # Fallback 2: ~/.sdlca/bridge/.env.credentials — the bridge's
        # existing credentials file already has GITHUB_APP_ID +
        # GITHUB_APP_PRIVATE_KEY set by `sdlca-bridge install`.
        creds_path = Path.home() / ".sdlca" / "bridge" / ".env.credentials"
        for k, v in _parse_shell_env_file(creds_path).items():
            if k == "GITHUB_APP_ID" and not app_id:
                app_id = v
                source_desc_parts.append(".env.credentials(app_id)")
            elif k == "GITHUB_APP_PRIVATE_KEY" and not inline_pem:
                inline_pem = v
                source_desc_parts.append(".env.credentials(inline_pem)")

    if not app_id:
        print(
            "[mint-app-token] ERROR: App ID not found.\n"
            "  Checked: GITHUB_APP_ID / SDLCA_APP_ID env vars,\n"
            "           ~/.sdlca/app.conf,\n"
            "           ~/.sdlca/bridge/.env.credentials.\n"
            "  Set GITHUB_APP_ID (preferred) or SDLCA_APP_ID via one of the above.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Resolve PEM bytes — inline env var takes precedence over file path.
    pem_bytes: bytes
    if inline_pem:
        pem_bytes = inline_pem.encode("utf-8")
    elif key_path_raw:
        key_path = Path(os.path.expanduser(key_path_raw))
        if not key_path.is_file():
            print(
                f"[mint-app-token] ERROR: private key not found at {key_path}",
                file=sys.stderr,
            )
            sys.exit(2)
        pem_bytes = key_path.read_bytes()
    else:
        print(
            "[mint-app-token] ERROR: App private key not found.\n"
            "  Checked: GITHUB_APP_PRIVATE_KEY env var (inline PEM),\n"
            "           SDLCA_APP_PRIVATE_KEY_PATH env var (file path),\n"
            "           ~/.sdlca/app.conf,\n"
            "           ~/.sdlca/bridge/.env.credentials.\n"
            "  Set GITHUB_APP_PRIVATE_KEY (preferred) or\n"
            "  SDLCA_APP_PRIVATE_KEY_PATH via one of the above.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Simple sanity: the PEM should start with BEGIN marker.
    if b"BEGIN" not in pem_bytes[:40]:
        print(
            "[mint-app-token] ERROR: resolved App private key does not look "
            "like PEM (no BEGIN marker near start).  Check the env var /"
            "file content.",
            file=sys.stderr,
        )
        sys.exit(2)

    return app_id, pem_bytes, " + ".join(source_desc_parts)


def _sign_app_jwt(app_id: str, private_key_pem: bytes) -> str:
    """Sign a short-lived (~10 min) JWT authenticating as the App itself.

    Clock-skew tolerance: -60 seconds on iat.
    Expiry: +540 seconds (9 min); GitHub max is 10 min.

    Accepts raw PEM bytes (either inline from env or read from file by caller).
    """
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    payload = {
        "iat": int((now - _dt.timedelta(seconds=60)).timestamp()),
        "exp": int((now + _dt.timedelta(seconds=540)).timestamp()),
        "iss": app_id,
    }
    return _pyjwt.encode(payload, private_key_pem, algorithm="RS256")


def _http_request(url: str, token: str, method: str = "GET") -> tuple[int, dict]:
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
    """High-level: returns {'token', 'expires_at', 'installation_id', 'source'} dict."""
    app_id, pem_bytes, source_desc = _load_config()
    app_jwt = _sign_app_jwt(app_id, pem_bytes)

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
        "source": source_desc,
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
