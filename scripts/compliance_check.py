#!/usr/bin/env python3
"""
compliance_check.py — Check issue bodies for template gaps and auto-fix P0 gaps.

Usage:
    python scripts/compliance_check.py --manifest manifest.json --repo REPO

Reads manifest.json. For each issue, fetches the body from GitHub and
checks for P0/P1/P2 compliance gaps. P0 gaps are auto-fixed immediately.
Writes compliance-report.json with a full gap summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Compliance rule patterns
# ---------------------------------------------------------------------------

TDD_SENTINEL = "TDD followed: failing test written BEFORE implementation"
TDD_FULL_LINE = (
    "- [ ] TDD followed: failing test written BEFORE implementation"
    " (Red phase confirmed before writing any production code)"
)

SECURITY_HEADER_RE = re.compile(r"^#{1,4}\s+Security", re.MULTILINE | re.IGNORECASE)
DEPENDENCIES_RE = re.compile(r"^#{1,4}\s+Dependenc", re.MULTILINE | re.IGNORECASE)
ASSUMPTIONS_RE = re.compile(r"^#{1,4}\s+Assumptions", re.MULTILINE | re.IGNORECASE)
MOSCOW_RE = re.compile(r"^#{1,4}\s+MoSCoW", re.MULTILINE | re.IGNORECASE)
SUBTASKS_RE = re.compile(r"^#{1,4}\s+Subtasks", re.MULTILINE | re.IGNORECASE)
IMPL_OPTIONS_RE = re.compile(
    r"^#{1,4}\s+Implementation\s+Options", re.MULTILINE | re.IGNORECASE
)
RELEASE_VALUE_RE = re.compile(
    r"^#{1,4}\s+Release\s+Value", re.MULTILINE | re.IGNORECASE
)
WHY_MATTERS_RE = re.compile(
    r"^#{1,4}\s+Why\s+This\s+Matters", re.MULTILINE | re.IGNORECASE
)
TLDR_RE = re.compile(r"^#{1,4}\s+TL;?DR", re.MULTILINE | re.IGNORECASE)
DONE_WHEN_RE = re.compile(r"I Know I Am Done When", re.IGNORECASE)

MUTATION_KEYWORDS_RE = re.compile(
    r"\b(create|update|delete|resolve|write|set|build|implement)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def check_issue(
    number: int,
    title: str,
    body: str,
    level: str,
    has_blocked_label: bool = False,
) -> list[dict[str, Any]]:
    """Return a list of gap dicts for a single issue body."""
    gaps: list[dict[str, Any]] = []

    # P0-1: Missing TDD language
    if TDD_SENTINEL not in body:
        gaps.append(
            {
                "severity": "P0",
                "rule": "P0-1",
                "description": "Missing TDD language in 'I Know I Am Done When'",
                "fixed": False,
            }
        )

    # P0-2: Missing Security/Compliance on mutation issues
    is_mutation = bool(MUTATION_KEYWORDS_RE.search(title + " " + body))
    has_security = bool(SECURITY_HEADER_RE.search(body))
    if is_mutation and not has_security and level in ("epic", "story", "task"):
        gaps.append(
            {
                "severity": "P0",
                "rule": "P0-2",
                "description": "Mutation issue missing Security/Compliance section",
                "fixed": False,
            }
        )

    # P0-3: Missing dependency table on blocked issues
    has_deps = bool(DEPENDENCIES_RE.search(body))
    if has_blocked_label and not has_deps:
        gaps.append(
            {
                "severity": "P0",
                "rule": "P0-3",
                "description": "Blocked issue missing Dependencies section",
                "fixed": False,
            }
        )

    # P1 rules
    if not ASSUMPTIONS_RE.search(body):
        gaps.append(
            {
                "severity": "P1",
                "rule": "P1-1",
                "description": "Missing Assumptions section",
                "fixed": False,
            }
        )
    if not MOSCOW_RE.search(body):
        gaps.append(
            {
                "severity": "P1",
                "rule": "P1-2",
                "description": "Missing MoSCoW section",
                "fixed": False,
            }
        )
    if level in ("epic", "story") and not SUBTASKS_RE.search(body):
        gaps.append(
            {
                "severity": "P1",
                "rule": "P1-3",
                "description": "Missing Subtasks Needed section",
                "fixed": False,
            }
        )

    # P2 rules
    if level in ("initiative", "epic") and not RELEASE_VALUE_RE.search(body):
        gaps.append(
            {
                "severity": "P2",
                "rule": "P2-1",
                "description": "Missing Release Value section",
                "fixed": False,
            }
        )
    if level == "story" and not WHY_MATTERS_RE.search(body):
        gaps.append(
            {
                "severity": "P2",
                "rule": "P2-2",
                "description": "Missing Why This Matters section",
                "fixed": False,
            }
        )
    if level == "story" and not TLDR_RE.search(body):
        gaps.append(
            {
                "severity": "P2",
                "rule": "P2-3",
                "description": "Missing TL;DR section",
                "fixed": False,
            }
        )

    return gaps


# ---------------------------------------------------------------------------
# Auto-fix for P0 gaps (append-only, never replace)
# ---------------------------------------------------------------------------


def autofix_body(body: str, gaps: list[dict[str, Any]]) -> str:
    """Apply all P0 auto-fixes to body. Returns updated body."""
    for gap in gaps:
        if gap["severity"] != "P0":
            continue
        rule = gap["rule"]

        if rule == "P0-1":
            # Inject TDD line into I Know I Am Done When section
            if DONE_WHEN_RE.search(body):
                # Append after the "I Know I Am Done When" header line
                body = re.sub(
                    r"(I Know I Am Done When\n+)",
                    rf"\1{TDD_FULL_LINE}\n",
                    body,
                    count=1,
                )
            else:
                body += f"\n\n## I Know I Am Done When\n\n{TDD_FULL_LINE}\n"
            gap["fixed"] = True

        elif rule == "P0-2":
            body += (
                "\n\n### Security/Compliance\n\n"
                "- [ ] Input validated before use\n"
                "- [ ] No secrets committed to source\n"
                "- [ ] Least-privilege gh CLI scopes used\n"
            )
            gap["fixed"] = True

        elif rule == "P0-3":
            body += (
                "\n\n### Dependencies\n\n"
                "| Ticket | Description | Status |\n"
                "|--------|-------------|--------|\n"
                "| [BLOCKER] | Add blocking issue reference | Open |\n"
            )
            gap["fixed"] = True

    return body


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if check and result.returncode != 0:
        print(f"[ERROR] {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result


def _get_body(repo: str, number: int) -> str:
    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "body",
            "--jq",
            ".body",
        ]
    )
    return result.stdout.strip()


def _get_labels(repo: str, number: int) -> list[str]:
    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "labels",
            "--jq",
            "[.labels[].name]",
        ]
    )
    return json.loads(result.stdout.strip() or "[]")


def _update_body(repo: str, number: int, body: str) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        _run(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                "--repo",
                repo,
                "--body-file",
                tmp,
            ]
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run_compliance_check(
    manifest: dict[str, Any],
    repo: str,
) -> dict[str, Any]:
    """Run compliance check on all issues in manifest.

    Returns a report dict with summary and per-issue gap details.
    """
    report: dict[str, Any] = {
        "summary": {
            "total_issues": len(manifest),
            "p0_fixed": 0,
            "p1_gaps": 0,
            "p2_gaps": 0,
        },
        "issues": [],
    }

    for title, record in manifest.items():
        number = record["number"]
        level = record["level"]
        body = _get_body(repo, number)
        labels = _get_labels(repo, number)
        has_blocked = "blocked" in labels

        gaps = check_issue(number, title, body, level, has_blocked)

        p0_gaps = [g for g in gaps if g["severity"] == "P0"]
        if p0_gaps:
            fixed_body = autofix_body(body, p0_gaps)
            _update_body(repo, number, fixed_body)
            report["summary"]["p0_fixed"] += sum(1 for g in p0_gaps if g["fixed"])

        report["summary"]["p1_gaps"] += sum(1 for g in gaps if g["severity"] == "P1")
        report["summary"]["p2_gaps"] += sum(1 for g in gaps if g["severity"] == "P2")

        if gaps:
            report["issues"].append(
                {
                    "number": number,
                    "title": title,
                    "gaps": gaps,
                }
            )

        status = "✓" if not p0_gaps else f"fixed {len(p0_gaps)} P0"
        print(f"[check] #{number} {title} — {status}")

    report_path = Path("compliance-report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    print(
        f"[OK] compliance-report.json: "
        f"{s['p0_fixed']} P0 fixed, "
        f"{s['p1_gaps']} P1 gaps, "
        f"{s['p2_gaps']} P2 gaps"
    )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check issue bodies for compliance gaps and auto-fix P0 issues."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(path.read_text(encoding="utf-8"))
    run_compliance_check(manifest, args.repo)


if __name__ == "__main__":
    main()
