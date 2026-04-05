#!/usr/bin/env python3
"""
Schrödinger Issue — create a GitHub issue from current git changes.

Usage:
    create_issue.py --dry-run
    create_issue.py --title "feat: my work" --body-file /tmp/body.md \
        [--label enhancement] [--commits 10]

Options:
    --dry-run           Collect and print context JSON only; do not create issue.
    --title TEXT        Issue title (required unless --dry-run).
    --body-file PATH    Markdown file to use as the issue body.
    --label TEXT        Label to apply (repeatable; skipped if it doesn't exist).
    --commits N         Number of recent commits to include (default: 10).
    --repo TEXT         GitHub repo (owner/name). Defaults to current repo via gh.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(cmd: str, check: bool = True, capture: bool = True) -> str:
    """Run a shell command and return stdout as a string."""
    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}", file=sys.stderr)
        if capture:
            print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout.strip() if capture else ""


def collect_context(num_commits: int = 10) -> dict[str, Any]:
    """Collect git context: branch, diffs, commits, changed files."""
    branch = run("git rev-parse --abbrev-ref HEAD")
    staged_diff = run("git diff --cached", check=False)
    unstaged_diff = run("git diff", check=False)

    # Recent commit log with short diff stats
    log_format = "%h %s"
    commit_cmd = (
        "git log --oneline " f"-n {num_commits} " f"--pretty=format:'{log_format}'"
    )
    commit_log_raw = run(commit_cmd, check=False)
    commits = []
    for line in commit_log_raw.splitlines():
        if line.strip():
            commits.append(line.strip("'").strip())

    # Changed files (staged + unstaged + recent commits)
    staged_files = run("git diff --cached --name-status", check=False)
    unstaged_files = run("git diff --name-status", check=False)
    diff_range = (
        f"git diff --name-status HEAD~{num_commits} HEAD 2>/dev/null || "
        "git diff --name-status $(git rev-list --max-parents=0 HEAD) HEAD 2>/dev/null"
    )
    recent_commit_files = run(diff_range, check=False)

    return {
        "branch": branch,
        "staged_diff": staged_diff or "(none)",
        "unstaged_diff": unstaged_diff or "(none)",
        "recent_commits": commits,
        "staged_files": staged_files or "(none)",
        "unstaged_files": unstaged_files or "(none)",
        "recent_commit_files": recent_commit_files or "(none)",
    }


def create_issue(
    title: str,
    body_file: str,
    labels: list[str],
    repo: str | None = None,
) -> tuple[str, str]:
    """Create a GitHub issue using gh CLI."""
    # Verify gh auth
    auth_check = subprocess.run(
        "gh auth status", shell=True, capture_output=True, text=True
    )
    if auth_check.returncode != 0:
        print("[ERROR] gh is not authenticated. Run: gh auth login", file=sys.stderr)
        sys.exit(1)

    body_path = Path(body_file)
    if not body_path.exists():
        print(f"[ERROR] Body file not found: {body_file}", file=sys.stderr)
        sys.exit(1)

    repo_flag = f"--repo {repo}" if repo else ""
    label_flags = " ".join(f'--label "{lbl}"' for lbl in labels) if labels else ""

    cmd = (
        f"gh issue create {repo_flag} "
        f'--title "{title}" '
        f'--body-file "{body_path}" '
        f"{label_flags}"
    )

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        # Labels may not exist; retry without labels
        label_missing = (
            "label" in result.stderr.lower() or "not found" in result.stderr.lower()
        )
        if labels and label_missing:
            print(
                "[WARN] One or more labels not found; retrying without labels: "
                f"{labels}",
                file=sys.stderr,
            )
            cmd_no_labels = (
                f"gh issue create {repo_flag} "
                f'--title "{title}" '
                f'--body-file "{body_path}"'
            )
            result2 = subprocess.run(
                cmd_no_labels, shell=True, capture_output=True, text=True
            )
            if result2.returncode != 0:
                print(
                    f"[ERROR] gh issue create failed:\n{result2.stderr}",
                    file=sys.stderr,
                )
                sys.exit(1)
            issue_url = result2.stdout.strip()
        else:
            print(
                f"[ERROR] gh issue create failed:\n{result.stderr}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        issue_url = result.stdout.strip()

    issue_number = issue_url.rstrip("/").split("/")[-1]
    print(f"Created issue #{issue_number}: {issue_url}")
    return issue_url, issue_number


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schrödinger Issue — create a GitHub issue from git changes."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print collected context as JSON only; do not create issue.",
    )
    parser.add_argument("--title", help="Issue title (required unless --dry-run)")
    parser.add_argument("--body-file", help="Path to markdown file with issue body")
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="labels",
        help="Label to apply (repeatable)",
    )
    parser.add_argument(
        "--commits",
        type=int,
        default=10,
        help="Number of recent commits to include in context (default: 10)",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repo (owner/name). Defaults to current repo.",
    )
    args = parser.parse_args()

    ctx = collect_context(num_commits=args.commits)

    if args.dry_run:
        print(json.dumps(ctx, indent=2))
        return

    if not args.title:
        print("[ERROR] --title is required unless --dry-run is set.", file=sys.stderr)
        sys.exit(1)
    if not args.body_file:
        print(
            "[ERROR] --body-file is required unless --dry-run is set.",
            file=sys.stderr,
        )
        sys.exit(1)

    create_issue(args.title, args.body_file, args.labels, repo=args.repo)


if __name__ == "__main__":
    main()
