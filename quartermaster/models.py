"""Shared data types and the Jira state machine enum.

These dataclasses are the contract every module agrees on: the poller produces
Tickets/Jobs, the Claude runner produces ClaudeResults, the pipeline produces a
PipelineResult, and the broker acts on Tickets.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

# Stable namespace so uuid5(KEY) is deterministic across processes/restarts.
SESSION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "quartermaster.sessions")


class Status(str, Enum):
    """Logical statuses. Mapped to your Jira workflow names via Settings."""
    TODO = "todo"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    NEEDS_DECISION = "needs_decision"
    BLOCKED = "blocked"
    IN_REVIEW = "in_review"
    DONE = "done"


class Outcome(str, Enum):
    """What a pipeline run concluded for a ticket."""
    PR_OPENED = "pr_opened"
    NEEDS_DECISION = "needs_decision"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class Ticket:
    key: str
    summary: str
    description: str = ""
    status: Status = Status.TODO
    assignee: str = ""
    priority: str = "Medium"
    labels: list[str] = field(default_factory=list)

    @property
    def is_hard(self) -> bool:
        """`arch`/`hard` tickets route to the top model tier."""
        lowered = {l.lower() for l in self.labels}
        return bool(lowered & {"arch", "hard"})

    @property
    def budget_override(self) -> Optional[float]:
        """A `budget: N` label caps this ticket's spend at N USD."""
        for label in self.labels:
            low = label.lower().replace(" ", "")
            if low.startswith("budget:"):
                try:
                    return float(low.split(":", 1)[1])
                except ValueError:
                    return None
        return None

    @property
    def slug(self) -> str:
        words = "".join(c if c.isalnum() or c == " " else " " for c in self.summary)
        return "-".join(words.lower().split())[:40] or "task"


@dataclass
class Job:
    """A unit of work on the queue. Serialised to JSON for Redis."""
    ticket_key: str
    priority: int = 5  # lower = more urgent
    attempts: int = 0
    enqueued_at: float = 0.0
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


@dataclass
class ClaudeResult:
    """Parsed output of a single `claude -p ... --output-format json` call."""
    session_id: str
    cost_usd: float
    structured: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    error: Optional[str] = None
    # Token accounting (for cost analytics + cache-hit tracking).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)

    @property
    def cache_hit_ratio(self) -> float:
        billed_input = self.input_tokens + self.cache_read_tokens
        if billed_input <= 0:
            return 0.0
        return self.cache_read_tokens / billed_input


@dataclass
class PipelineResult:
    ticket_key: str
    outcome: Outcome
    cost_usd: float = 0.0
    pr_url: Optional[str] = None
    notes: str = ""
