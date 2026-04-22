"""SBR CLI — thin argparse wrapper over api.

Mirror-surface of the MCP server's 10 canonical tools, shell-friendly
for CI scripts + automation.  Primary operator UI is the MCP server
(Claude App with Voice); the CLI is for scripted / headless use.

Subcommands (Stage 1 MVP):
  start      — begin a new review session
  next       — advance to next pending subsection; print summary
  verbatim   — print current subsection verbatim (voice-unfriendly but explicit)
  approve    — accept current subsection content; advance
  improve    — replace current subsection with given text; advance
  skip       — leave current subsection untouched; advance
  status     — print session progress
  pause      — halt session (resumes from same cursor on next `resume`)
  resume     — re-activate a paused session
  terminate  — stop session without write-back
  write-back — commit approved verdicts for all completed issues

The CLI uses a sticky session via ~/.sbr/current-session.txt so operators
don't need to retype the session id.  Pass `--session <id>` to override.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from scripts.sbr.api import Session, SessionManager


def _current_session_file() -> Path:
    override = os.environ.get("SBR_CURRENT_SESSION_FILE", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".sbr" / "current-session.txt"


def _store_current_session(session_id: str) -> None:
    path = _current_session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id, encoding="utf-8")


def _load_current_session() -> str | None:
    path = _current_session_file()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _resolve_session(mgr: SessionManager, args: argparse.Namespace) -> Session:
    session_id = getattr(args, "session", None) or _load_current_session()
    if not session_id:
        print(
            "[sbr] No session ID provided and no sticky session found.\n"
            "  Start a session with `sbr start --scope N --repo owner/name`\n"
            "  or pass `--session <id>` explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        return mgr.load(session_id)
    except FileNotFoundError as exc:
        print(f"[sbr] {exc}", file=sys.stderr)
        sys.exit(2)


def _render_output(payload: dict, fmt: str) -> None:
    """Render a payload in the requested format.

    `text` — human-narratable one-liner (voice-friendly for LLM speech).
    `json` — machine-readable for scripted pipelines.
    """
    if fmt == "json":
        print(json.dumps(payload, indent=2))
        return
    # text format: prefer narratable summary if present
    if "narrative" in payload:
        print(payload["narrative"])
    else:
        # Fallback: flat key=value lines
        for k, v in payload.items():
            print(f"{k}: {v}")


def _cmd_start(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    skip = set(args.skip_issue or [])
    session = mgr.start(args.scope, args.repo, skip_issues=skip)
    _store_current_session(session.session_id)
    total = len(session.issues)
    narrative = (
        f"Session started.  {total} issues queued under scope "
        f"#{session.scope_issue_number} in {session.repo}.  "
        f"Session id: {session.session_id}."
    )
    _render_output(
        {
            "session_id": session.session_id,
            "queue_size": total,
            "scope_issue_number": session.scope_issue_number,
            "repo": session.repo,
            "narrative": narrative,
        },
        args.format,
    )
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    pair = mgr.get_current_subsection(session)
    if pair is None:
        _render_output(
            {
                "status": session.status,
                "narrative": (
                    "Session complete.  Run `sbr write-back` to commit "
                    "approved verdicts, or `sbr terminate` to discard."
                ),
            },
            args.format,
        )
        return 0
    # Re-persist in case lazy populate created subsections
    mgr._atomic_write(session)
    issue, sub = pair
    has_content = bool(sub.original_content.strip())
    narrative = f"Issue #{issue.number}: {sub.key}.  " + (
        "Content present; review + approve or improve."
        if has_content
        else "This subsection is empty.  Propose an improvement."
    )
    _render_output(
        {
            "has_next": True,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "subsection_key": sub.key,
            "has_content": has_content,
            "content_length": len(sub.original_content),
            "narrative": narrative,
        },
        args.format,
    )
    return 0


def _cmd_verbatim(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    pair = mgr.get_current_subsection(session)
    if pair is None:
        print("[sbr] No current subsection — session may be complete.", file=sys.stderr)
        return 1
    _issue, sub = pair
    # Verbatim content always prints directly (no JSON wrapping) so Voice
    # narration reads the exact source text.
    print(sub.original_content or "(empty)")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    mgr.apply_verdict(session, "approved")
    _render_output(
        {"status": "approved", "narrative": "Approved.  Advancing."}, args.format
    )
    return 0


def _cmd_improve(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    content = args.content
    # Support reading from stdin when content is "-"
    if content == "-":
        content = sys.stdin.read()
    mgr.apply_verdict(session, "improved", improved_content=content)
    _render_output(
        {
            "status": "improved",
            "content_length": len(content),
            "narrative": "Improved.  Advancing.",
        },
        args.format,
    )
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    mgr.apply_verdict(session, "skipped")
    _render_output(
        {"status": "skipped", "narrative": "Skipped.  Advancing."}, args.format
    )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    completed = sum(1 for i in session.issues if i.pending_count == 0)
    total_issues = len(session.issues)
    total_sections = sum(len(i.subsections) for i in session.issues)
    approved = sum(i.approved_count for i in session.issues)
    improved = sum(i.improved_count for i in session.issues)
    skipped = sum(i.skipped_count for i in session.issues)
    narrative = (
        f"Session {session.status}.  "
        f"Issue {session.current_issue_index + 1} of {total_issues}.  "
        f"{approved} approved, {improved} improved, {skipped} skipped."
    )
    _render_output(
        {
            "session_id": session.session_id,
            "status": session.status,
            "current_issue_index": session.current_issue_index,
            "total_issues": total_issues,
            "issues_completed": completed,
            "total_sections": total_sections,
            "approved": approved,
            "improved": improved,
            "skipped": skipped,
            "narrative": narrative,
        },
        args.format,
    )
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    mgr.pause(session)
    _render_output({"status": "paused", "narrative": "Session paused."}, args.format)
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    mgr.resume_session(session)
    _render_output({"status": "active", "narrative": "Session resumed."}, args.format)
    return 0


def _cmd_terminate(args: argparse.Namespace) -> int:
    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    mgr.terminate(session)
    _render_output(
        {"status": "terminated", "narrative": "Session terminated."}, args.format
    )
    return 0


def _cmd_write_back(args: argparse.Namespace) -> int:
    from scripts.sbr.api import WriteBacker

    mgr = SessionManager()
    session = _resolve_session(mgr, args)
    results: list[dict] = []
    for issue in session.issues:
        if issue.write_back_completed:
            continue
        # Only write-back issues that have at least one approved/improved verdict
        if not any(s.verdict in ("approved", "improved") for s in issue.subsections):
            continue
        result = WriteBacker.write_back_issue(session, issue)
        results.append(result)
    mgr._atomic_write(session)
    _render_output(
        {
            "write_back_count": len(results),
            "results": results,
            "narrative": f"Wrote back {len(results)} issue(s).",
        },
        args.format,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sbr",
        description="Sprint Backlog Review — voice-friendly backlog review CLI.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text, voice-friendly).",
    )
    parser.add_argument(
        "--session",
        help="Session ID (defaults to ~/.sbr/current-session.txt).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start a new review session.")
    p_start.add_argument("--scope", required=True, type=int, help="Scope issue number.")
    p_start.add_argument("--repo", required=True, help="owner/name.")
    p_start.add_argument(
        "--skip-issue",
        action="append",
        type=int,
        default=None,
        help="Issue number(s) to skip (repeatable).",
    )
    p_start.set_defaults(func=_cmd_start)

    p_next = sub.add_parser("next", help="Advance to next pending subsection.")
    p_next.set_defaults(func=_cmd_next)

    p_verbatim = sub.add_parser("verbatim", help="Print current subsection verbatim.")
    p_verbatim.set_defaults(func=_cmd_verbatim)

    p_approve = sub.add_parser("approve", help="Approve current subsection + advance.")
    p_approve.set_defaults(func=_cmd_approve)

    p_improve = sub.add_parser("improve", help="Replace current subsection + advance.")
    p_improve.add_argument("content", help="New content (or `-` to read from stdin).")
    p_improve.set_defaults(func=_cmd_improve)

    p_skip = sub.add_parser(
        "skip", help="Skip current subsection (no change) + advance."
    )
    p_skip.set_defaults(func=_cmd_skip)

    p_status = sub.add_parser("status", help="Print session progress.")
    p_status.set_defaults(func=_cmd_status)

    p_pause = sub.add_parser("pause", help="Pause session.")
    p_pause.set_defaults(func=_cmd_pause)

    p_resume = sub.add_parser("resume", help="Resume paused session.")
    p_resume.set_defaults(func=_cmd_resume)

    p_terminate = sub.add_parser(
        "terminate", help="Terminate session without write-back."
    )
    p_terminate.set_defaults(func=_cmd_terminate)

    p_wb = sub.add_parser("write-back", help="Commit approved verdicts to GitHub.")
    p_wb.set_defaults(func=_cmd_write_back)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
