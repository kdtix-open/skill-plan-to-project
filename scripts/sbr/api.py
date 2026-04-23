"""SBR API — canonical business logic shared by CLI + MCP server.

Five primary components:
 - `SessionManager`  — start / resume / pause / advance / terminate.
                       JSON-persisted at ~/.sbr/sessions/<id>.json.
 - `IssueWalker`     — wraps the skill's `_walk_existing_hierarchy` from
                       FR #34 Stage 5 / PR #37 for consistent iteration.
 - `SubsectionReviewer` — parses issue body via the skill's
                       `_parse_subsections`, iterates in template order,
                       yields per-subsection review context.
 - `LLMPromptBuilder` — produces voice-friendly per-subsection prompts.
 - `WriteBacker`     — merges approved verdicts + commits via `gh issue edit`
                       atomically.  Reuses FR #34 Stage 2.5
                       `_preserve_outside_template_zone`.

Stage 1 MVP focuses on the review orchestration primitives.  Stage 2 UI
imports this module.  Stage 3 closed-loop wraps the WriteBacker with
plan-file write-back.  EP-033 Vector/RAG hooks into LLMPromptBuilder.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

# Re-use the skill's canonical walker + parser + template + preserve-zone.
from scripts.create_issues import (
    _parse_subsections,
    _preserve_outside_template_zone,
    _walk_existing_hierarchy,
    generate_body,
)
from scripts.gh_helpers import get_issue_body, update_issue_body

# Verdict types for per-subsection operator decisions.
Verdict = Literal["pending", "approved", "improved", "skipped"]

# Subsection iteration order per level — Stage 1 MVP uses the canonical
# template order so review flows like a reader would naturally encounter
# the content.
_SUBSECTION_ORDER_BY_LEVEL: dict[str, list[str]] = {
    "scope": [
        "vision",
        "business_problem",
        "success_criteria",
        "in_scope_capabilities",
        "assumptions",
        "out_of_scope",
        "moscow",
        "done_when",
    ],
    "initiative": [
        "objective",
        "release_value",
        "success_criteria",
        "feature_scope",
        "assumptions",
        "dependencies",
        "out_of_scope",
        "artifacts",
        "done_when",
    ],
    "epic": [
        "objective",
        "release_value",
        "success_criteria",
        "feature_scope",
        "assumptions",
        "dependencies",
        "done_when",
        "code_areas",
        "questions_tech_lead",
        "security_compliance",
    ],
    "story": [
        "user_story",
        "tldr",
        "why_this_matters",
        "assumptions",
        "moscow",
        "dependencies",
        "done_when",
        "acceptance_criteria",
        "constraints",
        "implementation_notes",
        "security_compliance",
        "subtasks_needed",
    ],
    "task": [
        "summary",
        "context",
        "done_when",
        "implementation_notes",
        "security_compliance",
    ],
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SubsectionReview:
    """One subsection under review.  Stored per-issue in session state."""

    key: str
    verdict: Verdict = "pending"
    original_content: str = ""
    approved_content: str = ""  # operator's final content (for 'improved' / approved)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SubsectionReview:
        return cls(**d)


@dataclasses.dataclass
class IssueReview:
    """One issue under review.  Holds the queue of SubsectionReview items."""

    number: int
    title: str
    level: str
    parent_number: int | None = None
    subsections: list[SubsectionReview] = dataclasses.field(default_factory=list)
    write_back_completed: bool = False

    @property
    def pending_count(self) -> int:
        return sum(1 for s in self.subsections if s.verdict == "pending")

    @property
    def approved_count(self) -> int:
        return sum(1 for s in self.subsections if s.verdict == "approved")

    @property
    def improved_count(self) -> int:
        return sum(1 for s in self.subsections if s.verdict == "improved")

    @property
    def skipped_count(self) -> int:
        return sum(1 for s in self.subsections if s.verdict == "skipped")

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IssueReview:
        subs = [SubsectionReview.from_dict(s) for s in d.get("subsections", [])]
        return cls(
            number=d["number"],
            title=d["title"],
            level=d["level"],
            parent_number=d.get("parent_number"),
            subsections=subs,
            write_back_completed=d.get("write_back_completed", False),
        )


@dataclasses.dataclass
class Session:
    """Top-level SBR session state."""

    session_id: str
    scope_issue_number: int
    repo: str
    created_at: str
    status: Literal["active", "paused", "completed", "terminated"] = "active"
    current_issue_index: int = 0
    current_subsection_index: int = 0
    skip_issues: list[int] = dataclasses.field(default_factory=list)
    issues: list[IssueReview] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scope_issue_number": self.scope_issue_number,
            "repo": self.repo,
            "created_at": self.created_at,
            "status": self.status,
            "current_issue_index": self.current_issue_index,
            "current_subsection_index": self.current_subsection_index,
            "skip_issues": list(self.skip_issues),
            "issues": [i.to_dict() for i in self.issues],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        return cls(
            session_id=d["session_id"],
            scope_issue_number=d["scope_issue_number"],
            repo=d["repo"],
            created_at=d["created_at"],
            status=d.get("status", "active"),
            current_issue_index=d.get("current_issue_index", 0),
            current_subsection_index=d.get("current_subsection_index", 0),
            skip_issues=list(d.get("skip_issues", [])),
            issues=[IssueReview.from_dict(i) for i in d.get("issues", [])],
        )


# ---------------------------------------------------------------------------
# IssueWalker — delegate to the skill's walker
# ---------------------------------------------------------------------------


class IssueWalker:
    """Walks the sub-issue tree rooted at a Scope issue number.

    Delegates to the skill's `_walk_existing_hierarchy` for consistency —
    whatever `refresh` sees, SBR sees.  Honors an optional skip-issues set.
    """

    @staticmethod
    def walk(
        repo: str, scope_issue_number: int, skip_issues: Iterable[int] | None = None
    ) -> list[dict[str, Any]]:
        skip = set(skip_issues or set())
        results = _walk_existing_hierarchy(repo, scope_issue_number)
        if skip:
            results = [r for r in results if r["number"] not in skip]
        return results


# ---------------------------------------------------------------------------
# SubsectionReviewer — parse + iterate
# ---------------------------------------------------------------------------


def _extract_raw_section(body: str, key: str) -> str:
    """Fallback extractor: returns the raw markdown between the heading
    matching `key` and the next heading (or end of body).  Used when
    _parse_subsections returns an empty/tiny structured result for
    subsections that are laid out as markdown tables (MoSCoW, Feature
    Scope etc.) which the structured parser doesn't fully decode.

    Keeps the literal table text so the voice agent can at least read it
    aloud instead of receiving a 2-char "{}" string.
    """
    # Lazy import so test envs without the create_issues module can
    # still use SBR's core API (CLI, sessions).
    try:
        from scripts.create_issues import SUBSECTION_HEADINGS
    except ImportError:  # pragma: no cover
        return ""

    aliases = []
    for level_dict in SUBSECTION_HEADINGS.values():
        aliases.extend(level_dict.get(key, []))
    if not aliases:
        return ""
    aliases_lower = [a.lower() for a in aliases]

    lines = body.split("\n")
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Only match markdown headings #### or ### that start a section.
        if stripped.startswith("#"):
            heading_text = stripped.lstrip("#").strip().lower()
            if any(
                heading_text == a or heading_text.startswith(a) for a in aliases_lower
            ):
                start = i + 1
                break
    if start is None:
        return ""

    collected: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("#"):
            break
        collected.append(line)
    return "\n".join(collected).strip()


class SubsectionReviewer:
    """Parses an issue body + produces the ordered list of subsections.

    Stage 1 MVP: iteration order is template-order per level (see
    `_SUBSECTION_ORDER_BY_LEVEL`).  Iterator yields `(key, original_content)`
    tuples.  LLMPromptBuilder consumes these to produce narratable
    summaries + improvement proposals.

    Placeholder detection is deferred to compliance_check's P0-4 scanner;
    the SubsectionReviewer doesn't re-implement it.
    """

    @staticmethod
    def ordered_subsections(level: str, body: str) -> list[SubsectionReview]:
        """Parse `body` + return a list of SubsectionReview stubs in template
        order.  Each stub's `original_content` is the parsed value for that
        key (str / list / dict serialized to string for storage).

        Fallback for structured subsections (e.g. MoSCoW, Feature Scope)
        whose parse returns a tiny/empty dict: extract the raw markdown
        between this heading and the next so the voice agent has SOMETHING
        to read instead of the useless "{}" string.
        """
        parsed = _parse_subsections(body, level)
        order = _SUBSECTION_ORDER_BY_LEVEL.get(level, [])
        result: list[SubsectionReview] = []
        for key in order:
            value = parsed.get(key)
            if value is None:
                original = ""
            elif isinstance(value, str):
                original = value
            elif isinstance(value, list):
                if value:
                    original = "\n".join(f"- {v}" for v in value)
                else:
                    original = _extract_raw_section(body, key)
            elif isinstance(value, dict):
                serialized = json.dumps(value)
                # If the structured parser came back with nearly-empty
                # dict (common for MoSCoW tables the parser doesn't
                # fully understand), fall back to the raw markdown so
                # the voice agent at least has the literal table.
                if len(serialized) <= 4 or all(not v for v in value.values()):
                    raw = _extract_raw_section(body, key)
                    original = raw if raw else serialized
                else:
                    original = serialized
            else:
                original = str(value)
            result.append(SubsectionReview(key=key, original_content=original))
        return result


# ---------------------------------------------------------------------------
# SessionManager — JSON persistence + state machine
# ---------------------------------------------------------------------------


def _sessions_dir() -> Path:
    """Returns the per-operator sessions dir.  Override via SBR_SESSIONS_DIR."""
    override = os.environ.get("SBR_SESSIONS_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".sbr" / "sessions"


class SessionManager:
    """Manages the SBR session lifecycle + atomic JSON persistence.

    Primary entry points:
     - `start(scope_issue_number, repo, skip_issues)` → new session
     - `resume(session_id)` → loads from disk
     - `get_current_subsection(session_id)` → returns
       (issue_number, title, level, subsection_key, original_content)
       for the next pending subsection, or None if the session is complete.
     - `apply_verdict(session_id, verdict, improved_content=None)`
       → advances the cursor + persists.
     - `pause(session_id)` / `resume(session_id)` / `terminate(session_id)`
       → state transitions.

    All writes go through `_atomic_write` which writes to a tmp file +
    renames to prevent partial-write corruption.
    """

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = sessions_dir or _sessions_dir()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _atomic_write(self, session: Session) -> None:
        path = self._session_path(session.session_id)
        # Write to tmp in the same dir (for atomic rename across FS)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.sessions_dir,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(session.to_dict(), tmp, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)

    def start(
        self,
        scope_issue_number: int,
        repo: str,
        skip_issues: Iterable[int] | None = None,
    ) -> Session:
        """Walk the sub-issue tree + build fresh session.  Fetches issue
        bodies lazily (IssueWalker does NOT fetch bodies — those come
        later via `get_issue_body` when advancing to each issue)."""
        walked = IssueWalker.walk(repo, scope_issue_number, skip_issues)
        # Convert walked entries to IssueReview stubs; subsections empty
        # until we fetch the body + parse in SubsectionReviewer.
        issue_reviews: list[IssueReview] = []
        for entry in walked:
            issue_reviews.append(
                IssueReview(
                    number=entry["number"],
                    title=entry["title"],
                    level=entry["level"],
                    parent_number=entry.get("parent_number"),
                    subsections=[],  # populated on first visit
                )
            )
        session = Session(
            session_id=str(uuid.uuid4()),
            scope_issue_number=scope_issue_number,
            repo=repo,
            created_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
            status="active",
            skip_issues=list(skip_issues or []),
            issues=issue_reviews,
        )
        self._atomic_write(session)
        return session

    def load(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.is_file():
            raise FileNotFoundError(f"Session not found: {session_id}")
        with path.open(encoding="utf-8") as f:
            return Session.from_dict(json.load(f))

    def _populate_current_issue_subsections(self, session: Session) -> None:
        """Lazily fetch current issue's body + populate subsection stubs."""
        if session.current_issue_index >= len(session.issues):
            return
        current = session.issues[session.current_issue_index]
        if current.subsections:
            return  # already populated
        body = get_issue_body(session.repo, current.number)
        current.subsections = SubsectionReviewer.ordered_subsections(
            current.level, body
        )

    def get_current_subsection(
        self, session: Session
    ) -> tuple[IssueReview, SubsectionReview] | None:
        """Return the (issue, subsection) currently under review, or None
        if the session has no more pending subsections."""
        if session.status != "active":
            return None
        while session.current_issue_index < len(session.issues):
            current_issue = session.issues[session.current_issue_index]
            self._populate_current_issue_subsections(session)
            if session.current_subsection_index < len(current_issue.subsections):
                current_sub = current_issue.subsections[
                    session.current_subsection_index
                ]
                return current_issue, current_sub
            # Advance to next issue
            session.current_issue_index += 1
            session.current_subsection_index = 0
        session.status = "completed"
        return None

    def apply_verdict(
        self,
        session: Session,
        verdict: Verdict,
        improved_content: str | None = None,
    ) -> bool:
        """Mark current subsection with verdict + advance cursor + persist.

        Returns:
            True if the verdict was applied (cursor advanced); False if
            the call was a no-op (session not active OR queue empty).
            Callers use the return value to report the real outcome
            instead of silently claiming success — the Stage 1.5 UAT
            2026-04-23 surfaced a bug where sbr_approve responded
            "approved" on a paused session while the server quietly
            dropped the verdict.
        """
        pair = self.get_current_subsection(session)
        if pair is None:
            return False
        _issue, sub = pair
        sub.verdict = verdict
        if verdict == "improved" and improved_content is not None:
            sub.approved_content = improved_content
        elif verdict == "approved":
            sub.approved_content = sub.original_content
        # Advance cursor
        session.current_subsection_index += 1
        self._atomic_write(session)
        return True

    def go_back(self, session: Session) -> None:
        """Move cursor back one subsection (across issue boundaries).

        Unlike apply_verdict/advance which walks forward through the
        queue, go_back lets the operator revisit + amend a subsection
        they've already verdicted.  The previous subsection's verdict
        is cleared so the operator can re-approve / re-improve / re-skip
        without a stuck state.
        """
        # Move to previous subsection within the current issue, or to
        # the last subsection of the previous issue.
        if session.current_subsection_index > 0:
            session.current_subsection_index -= 1
        elif session.current_issue_index > 0:
            session.current_issue_index -= 1
            prev_issue = session.issues[session.current_issue_index]
            # Ensure subsections are populated for the previous issue
            # so the cursor lands on a real subsection.
            self._populate_current_issue_subsections(session)
            session.current_subsection_index = max(0, len(prev_issue.subsections) - 1)
        else:
            # Already at the beginning — no-op, but unmark current
            # just in case the caller expected state mutation.
            pass
        # Clear the verdict on the subsection we just moved back to so
        # the operator can re-verdict.  Keep approved_content in case
        # they want to reuse it.
        pair = self.get_current_subsection(session)
        if pair is not None:
            _issue, sub = pair
            sub.verdict = "pending"
        # If the session was completed, resuming via go_back should
        # revive it to active.
        if session.status == "completed":
            session.status = "active"
        self._atomic_write(session)

    def goto(
        self,
        session: Session,
        issue_number: int,
        subsection_key: str | None = None,
    ) -> bool:
        """Jump the cursor to a specific issue (and optional subsection).

        Returns True if the target was found + cursor set, False if the
        issue_number isn't in the session queue.  Clears the verdict
        on the target subsection for re-verdicting.
        """
        for idx, issue in enumerate(session.issues):
            if issue.number == issue_number:
                session.current_issue_index = idx
                self._populate_current_issue_subsections(session)
                if subsection_key:
                    for sub_idx, sub in enumerate(issue.subsections):
                        if sub.key == subsection_key:
                            session.current_subsection_index = sub_idx
                            sub.verdict = "pending"
                            self._atomic_write(session)
                            return True
                    # Subsection not found in this issue — fall through
                    # + land on index 0.
                session.current_subsection_index = 0
                if issue.subsections:
                    issue.subsections[0].verdict = "pending"
                if session.status == "completed":
                    session.status = "active"
                self._atomic_write(session)
                return True
        return False

    def pause(self, session: Session) -> None:
        session.status = "paused"
        self._atomic_write(session)

    def resume_session(self, session: Session) -> None:
        if session.status == "paused":
            session.status = "active"
            self._atomic_write(session)

    def terminate(self, session: Session) -> None:
        session.status = "terminated"
        self._atomic_write(session)


# ---------------------------------------------------------------------------
# LLMPromptBuilder — Stage 1 MVP scaffolding
# ---------------------------------------------------------------------------


class LLMPromptBuilder:
    """Produces voice-friendly per-subsection prompts for an LLM.

    Stage 1 MVP: returns static, narratable summary + improvement-proposal
    templates.  Stage 2+ will integrate with the actual LLM provider (the
    MCP client's LLM handles the completion; SBR just produces well-shaped
    prompts).

    EP-033 integration point: `build_improvement_prompt()` will augment
    prompts with RAG context once the KnowledgeIndex service is available.
    """

    @staticmethod
    def build_summary_prompt(level: str, subsection_key: str, content: str) -> str:
        """Prompt the LLM to narrate a voice-friendly summary."""
        return (
            f"Summarize the '{subsection_key}' subsection of this "
            f"{level}-level issue in 1-2 short sentences suitable for voice "
            f"narration.  If the content contains template placeholders "
            f"(bracketed uppercase strings), mention how many need operator "
            f"attention first.\n\n"
            f"Content:\n\n{content or '(empty)'}"
        )

    @staticmethod
    def build_improvement_prompt(
        level: str, subsection_key: str, content: str, rag_context: str = ""
    ) -> str:
        """Prompt the LLM to propose an improved version of the subsection."""
        rag_block = (
            f"\nRelevant prior approvals:\n{rag_context}\n" if rag_context else ""
        )
        return (
            f"Improve the '{subsection_key}' subsection of this "
            f"{level}-level issue.  Match the template's expected shape "
            f"(bullet list for criteria, Given/When/Then for acceptance, "
            f"paragraph for narrative, etc.).  Replace any template "
            f"placeholders with real content.  Keep the writing concrete + "
            f"voice-friendly.{rag_block}\n\n"
            f"Current content:\n\n{content or '(empty)'}"
        )


# ---------------------------------------------------------------------------
# WriteBacker — merge + commit
# ---------------------------------------------------------------------------


class WriteBacker:
    """Assembles the final issue body from a completed IssueReview +
    commits via `gh issue edit`.

    Reuses FR #34 Stage 2.5 `_preserve_outside_template_zone` so operator
    content outside the template (HTML comments, trailing signatures,
    sequence-order blockquotes) survives refresh.

    Stage 1 MVP: single-issue write-back.  Callers iterate issues
    themselves after session completion.
    """

    @staticmethod
    def write_back_issue(session: Session, issue: IssueReview) -> dict[str, Any]:
        """Commit the approved verdicts for a single issue.  Returns a
        diff summary for operator review."""
        # Build the item dict expected by generate_body from the session's
        # approved/improved content.
        subsections: dict[str, Any] = {}
        for sub in issue.subsections:
            if sub.verdict in ("approved", "improved") and sub.approved_content:
                subsections[sub.key] = sub.approved_content
        item = {
            "title": issue.title,
            "description": "",  # subsections carry the content
            "priority": "P1",
            "size": "M",
            "subsections": subsections,
        }
        new_body = generate_body(item, issue.level)

        # Fetch current body + preserve outside-zone content.
        current_body = get_issue_body(session.repo, issue.number)
        merged_body, preserved = _preserve_outside_template_zone(current_body, new_body)
        update_issue_body(session.repo, issue.number, merged_body)
        issue.write_back_completed = True
        return {
            "issue_number": issue.number,
            "chars_before": len(current_body),
            "chars_after": len(merged_body),
            "preserved_prefix_lines": preserved.get("prefix", "").count("\n"),
            "preserved_suffix_lines": preserved.get("suffix", "").count("\n"),
        }
