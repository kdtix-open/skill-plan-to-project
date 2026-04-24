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

# Regex at module level — WriteBacker uses it for section-surgical
# replacements.  Aliased to avoid shadowing builtins + to keep the import
# grouped with the other module-level imports.
import re as _re  # noqa: E402
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

# Re-use the skill's canonical walker + parser + template + preserve-zone.
from scripts.create_issues import (
    _parse_subsections,
    _walk_existing_hierarchy,
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


# Regex used by both apply_verdict's content-clean step and
# WriteBacker._replace_section_in_body to strip voice-echoed section
# terminators.  Matches a trailing `---` plus any preceding blank
# lines, so we can repeatedly call it to clean multiply-stacked
# terminators.
_TRAILING_SECTION_TERMINATOR_RE = _re.compile(r"(?:\n\s*)*---\s*$")


def _strip_trailing_section_terminator(content: str) -> str:
    """Remove trailing `\\n---` separator(s) from voice-echoed content.

    Voice agents occasionally include the section separator when
    dictating back the proposed improvement, which produces duplicate
    horizontal rules when WriteBacker re-inserts its own terminator.
    Strip conservatively: only drops trailing `---` patterns, never
    touches body content.  Called from SessionManager.apply_verdict and
    from WriteBacker (belt + suspenders).
    """
    stripped = content.rstrip()
    while _TRAILING_SECTION_TERMINATOR_RE.search(stripped):
        stripped = _TRAILING_SECTION_TERMINATOR_RE.sub("", stripped).rstrip()
    return stripped


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
class WriteBackSnapshot:
    """Pre-write + post-write body snapshot for rollback (2026-04-23 P0).

    Captured synchronously inside WriteBacker.write_back_issue so that the
    operator can roll back a bad write without depending on GitHub's
    `userContentEdits` history.  Persisted as part of Session JSON, so
    rollback survives container restarts and process exits.
    """

    before_body: str
    after_body: str
    written_at: str  # ISO-8601 UTC
    improvements_applied: list[str] = dataclasses.field(default_factory=list)
    strategy: str = "surgical"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WriteBackSnapshot:
        return cls(**d)


@dataclasses.dataclass
class Investigation:
    """One async investigation dispatched via the local bridge.

    Persisted in Session.investigations so findings survive container
    restarts.  Phase 1 scaffolding only — the dispatcher is wired in
    Phase 2.
    """

    job_id: str
    tool_kind: Literal["review_repo", "review_plan", "research", "review_issues"]
    prompt: str
    context: dict[str, Any] = dataclasses.field(default_factory=dict)
    model: str = "claude-sonnet-4-5-20250929"
    provider: Literal["claude", "codex", "cursor", "copilot"] = "claude"
    status: Literal["pending", "running", "ready", "consumed", "failed"] = "pending"
    dispatched_at: str = ""
    completed_at: str | None = None
    finding: str | None = None
    error: str | None = None
    cost_usd_estimate: float = 0.0
    from_bookmark_label: str | None = None
    summary: str | None = None
    act_on_suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Investigation:
        return cls(
            **{
                k: v
                for k, v in d.items()
                if k in {f.name for f in dataclasses.fields(cls)}
            }
        )


@dataclasses.dataclass
class Bookmark:
    """A saved cursor position for investigation round-trips.

    Persisted in Session.bookmarks.  The voice agent saves bookmarks
    before dispatching investigations + before jumping to ready findings,
    enabling a safe return to the operator's review progress.
    """

    label: str
    reason: Literal[
        "investigation_dispatched",
        "investigation_return",
        "progress_save",
    ]
    issue_index: int
    subsection_index: int
    issue_number: int = 0
    subsection_key: str = ""
    created_at: str = ""
    linked_investigation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bookmark:
        return cls(
            **{
                k: v
                for k, v in d.items()
                if k in {f.name for f in dataclasses.fields(cls)}
            }
        )


@dataclasses.dataclass
class IssueReview:
    """One issue under review.  Holds the queue of SubsectionReview items."""

    number: int
    title: str
    level: str
    parent_number: int | None = None
    subsections: list[SubsectionReview] = dataclasses.field(default_factory=list)
    write_back_completed: bool = False
    # Chronological history of write-backs for this issue in THIS session.
    # Most recent last.  sbr_rollback_write_back pops from the end.
    write_back_history: list[WriteBackSnapshot] = dataclasses.field(
        default_factory=list
    )

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
        history = [
            WriteBackSnapshot.from_dict(h) for h in d.get("write_back_history", [])
        ]
        return cls(
            number=d["number"],
            title=d["title"],
            level=d["level"],
            parent_number=d.get("parent_number"),
            subsections=subs,
            write_back_completed=d.get("write_back_completed", False),
            write_back_history=history,
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
    investigations: list[Investigation] = dataclasses.field(default_factory=list)
    bookmarks: list[Bookmark] = dataclasses.field(default_factory=list)
    investigations_cost_usd: float = 0.0

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
            "investigations": [inv.to_dict() for inv in self.investigations],
            "bookmarks": [bm.to_dict() for bm in self.bookmarks],
            "investigations_cost_usd": self.investigations_cost_usd,
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
            investigations=[
                Investigation.from_dict(inv) for inv in d.get("investigations", [])
            ],
            bookmarks=[Bookmark.from_dict(bm) for bm in d.get("bookmarks", [])],
            investigations_cost_usd=d.get("investigations_cost_usd", 0.0),
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
            # Defense — strip any trailing `---` terminators the voice
            # agent may have echoed back with the improved content.
            # Without this, WriteBacker produces duplicate horizontal
            # rules (see 2026-04-23 issue #182 morning session which
            # seeded the bug that surfaced in afternoon write-back).
            sub.approved_content = _strip_trailing_section_terminator(improved_content)
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


# Map session subsection keys → the exact `## Header` that delimits that
# section in a rendered issue body.  Write-back replaces content between
# consecutive headers; it never regenerates the full body.
#
# Coverage: scope + initiative + epic + story + task.  Any subsection key
# not in this map triggers a fail-loud (we refuse to write rather than
# risk a misaligned substitution).
_SUBSECTION_HEADERS: dict[str, str] = {
    # scope
    "vision": "## Vision",
    "business_problem": "## Business Problem & Current State",
    "success_criteria": "## Success Criteria",
    "in_scope_capabilities": "## In-Scope Capabilities",
    "assumptions": "## Assumptions",
    "out_of_scope": "## Out of Scope",
    "moscow": "## MoSCoW Classification",
    "done_when": "## I Know I Am Done When",
    # initiative + epic (three-level heading inside "## PRODUCT SECTION")
    "objective": "### Objective",
    "release_value": "### Release Value",
    "feature_scope": "### Feature Scope",
    "dependencies": "### Dependencies",
    # story
    "user_story": "## User Story",
    "tldr": "## TL;DR",
    "why_this_matters": "## Why This Matters",
    "acceptance_criteria": "## Acceptance Criteria",
    "implementation_options": "## Implementation Options",
    "subtasks_needed": "## Subtasks Needed",
    # task
    "summary": "## Summary",
    "context": "## Context",
    "implementation_notes": "## Implementation Notes",
}


def _replace_section_in_body(
    body: str, header: str, new_content: str
) -> tuple[str, bool]:
    """Surgically replace the content under `header` (up to the next
    sibling-or-ancestor header OR the first `---` separator, whichever
    comes first) with `new_content`.

    Returns `(new_body, replaced)`.  When `header` is not present,
    returns `(body, False)` without modification — the caller decides
    whether to raise or skip.

    Matching rule: the section body is everything between `header\n\n`
    and the NEXT occurrence of `\n---\n` (horizontal-rule separator) OR
    the next `^#{1,3} ` heading of equal-or-higher level, whichever is
    nearer.  Most KDTIX issue templates use `---` separators between
    sections, so that's the common case.
    """
    # Anchor on the literal header at line start.
    header_pattern = _re.compile(r"(?m)^" + _re.escape(header) + r"\s*$")
    m = header_pattern.search(body)
    if not m:
        return body, False

    start = m.end()  # char index just past the header line
    # Body content starts after the single blank line that follows a
    # header; tolerate variable whitespace.
    after_header = body[start:]

    # Find the terminator: next `---` separator or next `^#{1,3} ` heading.
    hr_pattern = _re.compile(r"\n---\s*\n")
    next_heading_pattern = _re.compile(r"(?m)^#{1,3}\s")

    hr_match = hr_pattern.search(after_header)
    next_heading_match = next_heading_pattern.search(after_header)

    # Pick the NEAREST terminator; if neither is found, treat
    # rest-of-body as the section.
    candidates = []
    if hr_match:
        candidates.append(("hr", hr_match.start(), hr_match.end()))
    if next_heading_match:
        candidates.append(
            ("heading", next_heading_match.start(), next_heading_match.start())
        )

    if not candidates:
        terminator_start = terminator_end = len(after_header)
    else:
        candidates.sort(key=lambda t: t[1])
        _kind, terminator_start, terminator_end = candidates[0]

    # Preserve exactly what was there structurally — strip the old body
    # but keep the header + the terminator (if any).
    before = body[:start]
    after = body[start + terminator_end :]
    # Normalize: trim leading/trailing blank lines + strip any trailing
    # `---` the voice agent echoed back with the improved content.
    # Without the terminator-strip, re-adding our own terminator
    # produces duplicate `---` separators (2026-04-23 issue #182
    # morning session bug — fixed at both apply_verdict-time AND
    # write-back-time for defense in depth).
    new_body_section = _strip_trailing_section_terminator(new_content)
    terminator_text = after_header[terminator_start:terminator_end]
    # Reassemble.  `before` ends with the header line (no trailing \n
    # because the regex anchors $ before the newline).  terminator_text
    # already starts with `\n` for the `---` case and with nothing for
    # the heading case, so we don't need to synthesize more framing.
    if terminator_text.startswith("\n"):
        # terminator begins with its own leading \n (common "---" case)
        rebuilt = f"{before}\n\n{new_body_section}{terminator_text}{after}"
    else:
        # terminator is a bare heading — inject one blank line before it.
        rebuilt = f"{before}\n\n{new_body_section}\n\n{terminator_text}{after}"
    return rebuilt, True


class WriteBacker:
    """Commit a reviewed issue's verdicts back to GitHub.

    SURGICAL STRATEGY (2026-04-23 P0 fix, resolves data-loss bug where
    approved sections were being blanked to template placeholders):

    * For each `improved` subsection: replace ONLY that section's content
      between its `## Header` and the next `---` separator.  Everything
      outside the touched sections — including every approved section,
      every skipped section, every pending section, plus operator-authored
      prefix/suffix content — is preserved byte-for-byte.
    * For each `approved` subsection: DO NOTHING.  The operator said "leave
      as-is", so the existing body content is the correct content.
    * For each `skipped` / `pending`: DO NOTHING.

    This replaces the prior "regenerate whole body from template + fill
    subsections" strategy, which silently destroyed content when
    `_load_template` returned empty (e.g. asset files missing from the
    pip install) and the fallback `_body_scope` stub emitted placeholder
    text where real content had been.  The regression was first reproduced
    on issue 182 (9,977 chars → 1,088 chars, all approved content blanked
    to `[Vision statement]` / `[Criterion 1]` / etc. stubs).

    ROLLBACK ARCHITECTURE (2026-04-23 follow-up): every write-back records
    a `WriteBackSnapshot(before_body, after_body, written_at, ...)` in
    `issue.write_back_history` before calling `update_issue_body`.  The
    snapshot persists with the session JSON so `rollback_write_back` can
    restore the original body without depending on GitHub's edit-history
    APIs.

    Why we snapshot rather than query GitHub: the GitHub REST + GraphQL
    public APIs do NOT expose issue body or comment edit history as
    queryable text — it's a UI-only feature.  Public surfaces that DO
    exist (timeline, reactions) don't return the pre-edit body.  Any
    programmatic rollback of body text MUST be backed by an external
    snapshot (our session JSON here; webhooks with
    `issues.edited`/`changes.body.from` are the other canonical pattern
    for org-wide coverage).  See operator research 2026-04-23.
    """

    @staticmethod
    def write_back_issue(session: Session, issue: IssueReview) -> dict[str, Any]:
        """Commit the reviewed verdicts for a single issue.  Returns a
        diff summary for operator review.

        Raises ValueError if an improved subsection's header cannot be
        located in the current body — better to fail loud than silently
        skip an improvement.
        """
        current_body = get_issue_body(session.repo, issue.number)
        body = current_body

        improvements_applied: list[str] = []
        improvements_skipped: list[str] = []

        for sub in issue.subsections:
            if sub.verdict != "improved":
                # Approved, skipped, and pending sections are not touched.
                # The existing body content stands.
                continue
            if not sub.approved_content:
                # Defensive — improved verdict without content should not
                # happen, but skip rather than write an empty section.
                improvements_skipped.append(f"{sub.key}(empty)")
                continue

            header = _SUBSECTION_HEADERS.get(sub.key)
            if header is None:
                raise ValueError(
                    f"Cannot write back subsection '{sub.key}' on issue "
                    f"#{issue.number}: no known header mapping.  Add "
                    f"'{sub.key}' to _SUBSECTION_HEADERS in scripts/sbr/"
                    f"api.py, or downgrade its verdict to 'skipped' to "
                    f"unblock the write-back."
                )

            body, replaced = _replace_section_in_body(
                body, header, sub.approved_content
            )
            if replaced:
                improvements_applied.append(sub.key)
            else:
                improvements_skipped.append(f"{sub.key}(header-not-found)")

        if improvements_skipped:
            # Fail loud on header-not-found — otherwise we silently leave
            # the operator's improvement unrecorded.
            header_missing = [
                s for s in improvements_skipped if s.endswith("(header-not-found)")
            ]
            if header_missing:
                raise ValueError(
                    f"Write-back for issue #{issue.number} could not locate "
                    f"section headers for: {', '.join(header_missing)}.  "
                    f"The issue body may not match the KDTIX template "
                    f"shape; skip these subsections or fix the body "
                    f"manually before retrying."
                )

        if body != current_body:
            update_issue_body(session.repo, issue.number, body)
            # Snapshot BEFORE + AFTER state for in-tool rollback.  Stored
            # in the session JSON so recovery survives restarts.
            snapshot = WriteBackSnapshot(
                before_body=current_body,
                after_body=body,
                written_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                improvements_applied=list(improvements_applied),
                strategy="surgical",
            )
            issue.write_back_history.append(snapshot)
        issue.write_back_completed = True
        return {
            "issue_number": issue.number,
            "chars_before": len(current_body),
            "chars_after": len(body),
            "improvements_applied": improvements_applied,
            "improvements_skipped": improvements_skipped,
            "strategy": "surgical",
            "rollback_available": bool(issue.write_back_history),
            "write_back_index": len(issue.write_back_history) - 1
            if issue.write_back_history
            else None,
        }

    @staticmethod
    def rollback_write_back(
        session: Session,
        issue: IssueReview,
        write_back_index: int | None = None,
    ) -> dict[str, Any]:
        """Restore the issue body to the state BEFORE a prior write-back.

        By default rolls back the MOST RECENT write-back (history tail).
        Pass `write_back_index` to target a specific earlier entry.

        This is an in-tool workflow — it does NOT query GitHub's edit
        history.  Recovery works only for write-backs that happened during
        the session currently persisted.  If the session file is lost or
        the operator wants to restore an older state, they'll need to
        pull from GitHub's `userContentEdits` manually.

        Returns a diff summary.  Raises ValueError if there's no history
        to roll back.
        """
        if not issue.write_back_history:
            raise ValueError(
                f"Issue #{issue.number} has no write-back history in this "
                f"session — nothing to roll back.  (Sessions only capture "
                f"snapshots for write-backs performed after 2026-04-23.)"
            )
        idx = (
            write_back_index
            if write_back_index is not None
            else len(issue.write_back_history) - 1
        )
        if not (0 <= idx < len(issue.write_back_history)):
            raise ValueError(
                f"write_back_index {idx} out of range; issue #{issue.number} "
                f"has {len(issue.write_back_history)} snapshot(s)."
            )
        snap = issue.write_back_history[idx]

        # Capture CURRENT body before rollback so the rollback itself is
        # reversible via another rollback (symmetric).
        current_body = get_issue_body(session.repo, issue.number)
        update_issue_body(session.repo, issue.number, snap.before_body)
        # Record the rollback as its own history entry so the operator
        # can un-rollback if they changed their mind.
        rollback_snap = WriteBackSnapshot(
            before_body=current_body,
            after_body=snap.before_body,
            written_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            improvements_applied=[f"rollback_of_index_{idx}"],
            strategy="rollback",
        )
        issue.write_back_history.append(rollback_snap)
        return {
            "issue_number": issue.number,
            "rolled_back_index": idx,
            "rolled_back_written_at": snap.written_at,
            "chars_before_rollback": len(current_body),
            "chars_after_rollback": len(snap.before_body),
            "strategy": "rollback",
            "new_history_length": len(issue.write_back_history),
        }
