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
import re
import sys
from pathlib import Path
from typing import Any

from scripts.constants import MUTATION_KEYWORDS, SECURITY_SECTION, TDD_SENTINEL
from scripts.gh_helpers import (
    GitHubAPIError,
    get_issue_body,
    get_issue_labels,
    update_issue_body,
)

# ---------------------------------------------------------------------------
# Compliance rule patterns
# ---------------------------------------------------------------------------

TDD_SENTINEL_CHECK = "TDD followed: failing test written BEFORE implementation"

SECURITY_HEADER_RE = re.compile(r"^#{1,4}\s+Security", re.MULTILINE | re.IGNORECASE)
DEPENDENCIES_RE = re.compile(r"^#{1,4}\s+Dependenc", re.MULTILINE | re.IGNORECASE)
ASSUMPTIONS_RE = re.compile(r"^#{1,4}\s+Assumptions", re.MULTILINE | re.IGNORECASE)
MOSCOW_RE = re.compile(r"^#{1,4}\s+MoSCoW", re.MULTILINE | re.IGNORECASE)
SUBTASKS_RE = re.compile(r"^#{1,4}\s+Subtasks", re.MULTILINE | re.IGNORECASE)
RELEASE_VALUE_RE = re.compile(
    r"^#{1,4}\s+Release\s+Value", re.MULTILINE | re.IGNORECASE
)
WHY_MATTERS_RE = re.compile(
    r"^#{1,4}\s+Why\s+This\s+Matters", re.MULTILINE | re.IGNORECASE
)
TLDR_RE = re.compile(r"^#{1,4}\s+TL;?DR", re.MULTILINE | re.IGNORECASE)
DONE_WHEN_RE = re.compile(r"I Know I Am Done When", re.IGNORECASE)

# P0-4 (FR #34 Stage 1): unreplaced template placeholder scanner.
#
# Detects literal [PLACEHOLDER] strings that `scripts/create_issues.py`
# _render_template() failed to replace — e.g. [CRITERION 1], [ASSUMPTION 1],
# [ITEM 1], [DESCRIPTION], [PROJECT-SPECIFIC CRITERION], [Describe the
# problem ...], etc.  The scanner is intentionally conservative: it matches
# strings that look like the template's placeholder pattern (square-bracketed
# content starting with uppercase, containing only uppercase / digits / space
# / the punctuation characters the templates actually use) and excludes
# legitimate markdown constructs.
#
# Allowlist:
#   - [ ]  + [x]  + [X]  — task-list checkboxes (any length, handled
#     separately by the regex's minimum-content requirement)
#   - bare numeric references like [N]  NOT allowlisted — those are legit
#     placeholders too ("issue #[N]") and should trigger P0-4
#
# The regex requires at least one uppercase letter at the start of the
# bracket contents + at least 2 characters total, which naturally excludes
# empty brackets + checkbox markers without requiring special cases.
PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9 _,/\-\[\]—\.]+\]")

# Additional placeholder patterns the templates use that don't match the
# all-caps rule above (e.g. "[Describe the problem ...]", "[1-sentence
# summary...]", "[Why this initiative exists...]").  These all start with a
# capital then descend to lowercase — match them with a separate regex.
PLACEHOLDER_DESCRIPTIVE_RE = re.compile(
    r"\[(?:Describe|Why|List|Vision|Objective|Backend|1-sentence|1-3 sentences|"
    r"DESCRIPTION|ITEM|CRITERION|ASSUMPTION|PROJECT-SPECIFIC|What|VERSION|POINTS|HOURS|"
    r"P0/P1/P2|POINTS\] pts|HOURS\] hrs|CODE)"
    r"[^\]]*\]"
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
    if TDD_SENTINEL_CHECK not in body:
        gaps.append(
            {
                "severity": "P0",
                "rule": "P0-1",
                "description": "Missing TDD language in 'I Know I Am Done When'",
                "fixed": False,
            }
        )

    # P0-2: Missing Security/Compliance on mutation issues
    is_mutation = bool(MUTATION_KEYWORDS.search(title + " " + body))
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

    # P0-4 (FR #34 Stage 1): Unreplaced template placeholders
    # Detect literal [PLACEHOLDER] strings that _render_template() failed to
    # replace.  Collect all matches + report counts.  Not auto-fixed —
    # placeholders require the structured-parser (Stage 2) or operator input
    # (Stage 3 interactive) to fill.  Reported as P0 to fail the ship.
    placeholder_matches = set()
    for m in PLACEHOLDER_RE.findall(body):
        placeholder_matches.add(m)
    for m in PLACEHOLDER_DESCRIPTIVE_RE.findall(body):
        placeholder_matches.add(m)
    if placeholder_matches:
        # Sort for stable ordering in the gap report
        sample = sorted(placeholder_matches)[:5]
        total = len(placeholder_matches)
        more_suffix = f" (+ {total - 5} more)" if total > 5 else ""
        gaps.append(
            {
                "severity": "P0",
                "rule": "P0-4",
                "description": (
                    f"{total} unreplaced template placeholder(s): "
                    f"{', '.join(sample)}{more_suffix}"
                ),
                "fixed": False,
                "placeholders": sorted(placeholder_matches),
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
                body = re.sub(
                    r"(I Know I Am Done When\n+)",
                    rf"\1{TDD_SENTINEL}\n",
                    body,
                    count=1,
                )
            else:
                body += f"\n\n## I Know I Am Done When\n\n{TDD_SENTINEL}\n"
            gap["fixed"] = True

        elif rule == "P0-2":
            body += SECURITY_SECTION
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
# Main logic
# ---------------------------------------------------------------------------


def run_compliance_check(
    manifest: dict[str, Any],
    repo: str,
    output_dir: Path | None = None,
    allow_placeholders: bool = False,
) -> dict[str, Any]:
    """Run compliance check on all issues in manifest.

    Returns a report dict with summary and per-issue gap details.

    FR #34 Stage 1: P0-4 unreplaced-placeholder gaps are NOT auto-fixable
    (they require the source plan's structured subsections or operator
    input), so they are reported but not auto-fixed.  When any P0-4 gap is
    present + `allow_placeholders=False`, the report includes
    `placeholder_gate: "failed"` so callers can fail the ship (CI exit 1).
    """
    out = output_dir or Path(".")
    report: dict[str, Any] = {
        "summary": {
            "total_issues": len(manifest),
            "p0_fixed": 0,
            "p0_placeholders": 0,  # P0-4 gaps — reported, not auto-fixed
            "p1_gaps": 0,
            "p2_gaps": 0,
        },
        "issues": [],
    }

    for title, record in manifest.items():
        number = record["number"]
        level = record["level"]
        display_title = record.get("title", title)
        body = get_issue_body(repo, number)
        labels = get_issue_labels(repo, number)
        has_blocked = "blocked" in labels

        gaps = check_issue(number, display_title, body, level, has_blocked)

        # Separate P0-4 (placeholder) gaps from auto-fixable P0 gaps.
        # autofix_body only knows about P0-1/P0-2/P0-3 remediations;
        # placeholders are operator/parser work per FR #34 Stage 2+.
        autofixable_p0 = [g for g in gaps if g["severity"] == "P0" and g["rule"] != "P0-4"]
        placeholder_p0 = [g for g in gaps if g["rule"] == "P0-4"]

        if autofixable_p0:
            fixed_body = autofix_body(body, autofixable_p0)
            update_issue_body(repo, number, fixed_body)
            report["summary"]["p0_fixed"] += sum(1 for g in autofixable_p0 if g["fixed"])

        report["summary"]["p0_placeholders"] += len(placeholder_p0)
        report["summary"]["p1_gaps"] += sum(1 for g in gaps if g["severity"] == "P1")
        report["summary"]["p2_gaps"] += sum(1 for g in gaps if g["severity"] == "P2")

        if gaps:
            report["issues"].append(
                {
                    "number": number,
                    "title": display_title,
                    "gaps": gaps,
                }
            )

        if placeholder_p0:
            status_extra = f", {len(placeholder_p0)} P0-4 placeholder(s)"
        else:
            status_extra = ""
        status = (
            "✓"
            if not autofixable_p0 and not placeholder_p0
            else f"fixed {len(autofixable_p0)} P0{status_extra}"
        )
        print(f"[check] #{number} {display_title} — {status}")

    # Gate on placeholders: if any P0-4 gap exists AND --allow-placeholders
    # is not set, mark the report's placeholder_gate = "failed".  Caller
    # should exit non-zero.
    if report["summary"]["p0_placeholders"] > 0 and not allow_placeholders:
        report["placeholder_gate"] = "failed"
    else:
        report["placeholder_gate"] = "passed"

    report_path = out / "compliance-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    print(
        f"[OK] compliance-report.json: "
        f"{s['p0_fixed']} P0 fixed, "
        f"{s['p0_placeholders']} P0-4 placeholder gaps, "
        f"{s['p1_gaps']} P1 gaps, "
        f"{s['p2_gaps']} P2 gaps "
        f"(placeholder_gate: {report['placeholder_gate']})"
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
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help=(
            "Allow shipping issues that contain unreplaced [PLACEHOLDER] "
            "strings.  Use ONLY when you intentionally want to ship "
            "incomplete issue bodies (e.g. operator will hand-fill later "
            "via PR #34 Stage 5 'refresh' mode).  Default: fail on any P0-4 "
            "placeholder gap so the structured-parser (Stage 2) or "
            "interactive mode (Stage 3) must fill them first."
        ),
    )
    args = parser.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"[ERROR] manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(path.read_text(encoding="utf-8"))
    out = Path(args.output_dir) if args.output_dir else None
    try:
        report = run_compliance_check(
            manifest,
            args.repo,
            output_dir=out,
            allow_placeholders=args.allow_placeholders,
        )
    except GitHubAPIError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    # Exit non-zero when placeholder gate failed + not overridden.  This lets
    # CI (or a `plan-to-project create` wrapper) fail the ship when issues
    # still carry unreplaced [PLACEHOLDER] strings.
    if report.get("placeholder_gate") == "failed":
        print(
            "[FAIL] placeholder_gate: unreplaced [PLACEHOLDER] strings "
            "remain in issue bodies.  Run with --allow-placeholders to "
            "override OR fill the placeholders via structured subsections "
            "in the source plan (Stage 2) / interactive mode (Stage 3) / "
            "refresh mode (Stage 5 — FR #34).",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
