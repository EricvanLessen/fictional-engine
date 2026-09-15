from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AUTHORITATIVE_CONTROL_REF = "refs/heads/main"
MAX_REPAIR_ATTEMPTS_PER_TASK = 3
MAX_AGENT_TURNS_WITHOUT_PROGRESS = 5
STOP_REASON_SECOND_TASK_CREATED = "SECOND_TASK_CREATED"
STOP_REASON_REPAIR_ATTEMPT_LIMIT_REACHED = "REPAIR_ATTEMPT_LIMIT_REACHED"
STOP_REASON_NO_PROGRESS_LIMIT_REACHED = "NO_PROGRESS_LIMIT_REACHED"

TASK_ID_PATTERN = re.compile(r"^task-\d{4,}$")
MESSAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{7,}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class Actor(StrEnum):
    ERIC = "eric"
    COPILOT = "copilot"
    OPENAI = "openai"
    GITHUB = "github"
    CI = "ci"
    SYSTEM = "system"


class DocumentType(StrEnum):
    TASK_INSTRUCTION = "TASK_INSTRUCTION"
    TASK_RESULT = "TASK_RESULT"
    REVIEW_DECISION = "REVIEW_DECISION"
    STATE_UPDATE = "STATE_UPDATE"
    RUN_EVENT = "RUN_EVENT"


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RECORDED = "RECORDED"
    FINAL = "FINAL"


class LifecycleState(StrEnum):
    READY_FOR_COPILOT = "READY_FOR_COPILOT"
    COPILOT_RUNNING = "COPILOT_RUNNING"
    WAITING_FOR_CI = "WAITING_FOR_CI"
    WAITING_FOR_OPENAI_REVIEW = "WAITING_FOR_OPENAI_REVIEW"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ReviewDecision(StrEnum):
    ACCEPT = "ACCEPT"
    FIX_REQUIRED = "FIX_REQUIRED"
    NEXT_TASK = "NEXT_TASK"
    BLOCKED = "BLOCKED"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"


class RunEventType(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    COPILOT_DISPATCHED = "COPILOT_DISPATCHED"
    COPILOT_RESULT_RECORDED = "COPILOT_RESULT_RECORDED"
    CI_EVIDENCE_RECORDED = "CI_EVIDENCE_RECORDED"
    OPENAI_REVIEW_RECORDED = "OPENAI_REVIEW_RECORDED"
    NEXT_TASK_CREATED = "NEXT_TASK_CREATED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_COMPLETED = "TASK_COMPLETED"


class ControlDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str
    message_id: str
    task_id: str
    from_actor: Actor = Field(alias="from")
    to_actor: Actor = Field(alias="to")
    type: DocumentType
    status: DocumentStatus
    branch: str
    created_at: datetime
    in_reply_to: str | None = None
    attempt: int = 1
    expected_head_sha: str | None = None
    pull_request_url: str | None = None
    commit_sha: str | None = None
    body: str

    @field_validator("message_id")
    @classmethod
    def validate_message_id(cls, value: str) -> str:
        if not MESSAGE_ID_PATTERN.fullmatch(value):
            raise ValueError("message_id must be globally unique and path-safe")
        return value

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        if not TASK_ID_PATTERN.fullmatch(value):
            raise ValueError("task_id must use the task-0042 convention")
        return value

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if not BRANCH_PATTERN.fullmatch(value):
            raise ValueError("branch must be a safe Git ref name")
        if ".." in value or value.startswith("/") or value.endswith("/"):
            raise ValueError("branch must not contain path traversal")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone aware")
        return value.astimezone(UTC)

    @field_validator("expected_head_sha", "commit_sha")
    @classmethod
    def validate_optional_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not SHA_PATTERN.fullmatch(value):
            raise ValueError("SHA values must be 7 to 40 lowercase hex characters")
        return value

    @field_validator("in_reply_to")
    @classmethod
    def validate_in_reply_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not MESSAGE_ID_PATTERN.fullmatch(value):
            raise ValueError("in_reply_to must reference a valid message_id")
        return value

    @field_validator("attempt")
    @classmethod
    def validate_attempt(cls, value: int) -> int:
        if value < 1:
            raise ValueError("attempt must be >= 1")
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("body must contain Markdown content")
        return stripped + "\n"

    def front_matter(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude={"body"})


class CorrespondenceDocument(ControlDocument):
    schema_version: str = "control-message.v1"
    type: DocumentType = Field(
        default=DocumentType.TASK_INSTRUCTION,
        validate_default=True,
    )

    @model_validator(mode="after")
    def validate_correspondence_type(self) -> CorrespondenceDocument:
        if self.type == DocumentType.RUN_EVENT:
            raise ValueError("Correspondence documents cannot use RUN_EVENT type")
        return self


class RunEventDocument(ControlDocument):
    schema_version: str = "control-run.v1"
    type: DocumentType = Field(default=DocumentType.RUN_EVENT, frozen=True)
    status: DocumentStatus = Field(default=DocumentStatus.RECORDED)
    event_type: RunEventType
    lifecycle_state: LifecycleState | None = None
    head_sha: str | None = None
    provider_run_id: str | None = None
    ci_conclusion: str | None = None
    ci_check_names: tuple[str, ...] = ()
    review_decision: ReviewDecision | None = None
    accepted_criteria_delta: tuple[str, ...] = ()
    verified_diff_summary: str | None = None
    progress_verified: bool = False
    next_task_id: str | None = None
    stop_reason: str | None = None

    @field_validator("head_sha")
    @classmethod
    def validate_head_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not SHA_PATTERN.fullmatch(value):
            raise ValueError("head_sha must be 7 to 40 lowercase hex characters")
        return value

    @field_validator("next_task_id")
    @classmethod
    def validate_next_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not TASK_ID_PATTERN.fullmatch(value):
            raise ValueError("next_task_id must use the task-0042 convention")
        return value

    @field_validator("ci_conclusion")
    @classmethod
    def validate_ci_conclusion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"success", "failure", "cancelled", "skipped", "neutral"}
        if value not in allowed:
            raise ValueError("ci_conclusion must be a supported GitHub check conclusion")
        return value

    @model_validator(mode="after")
    def validate_event_shape(self) -> RunEventDocument:
        requires_implementation_sha = {
            RunEventType.COPILOT_RESULT_RECORDED,
            RunEventType.CI_EVIDENCE_RECORDED,
            RunEventType.OPENAI_REVIEW_RECORDED,
        }

        def _require_full_sha(name: str, value: str | None) -> str:
            if value is None:
                raise ValueError(f"{self.event_type} requires {name}")
            if not FULL_SHA_PATTERN.fullmatch(value):
                raise ValueError(f"{self.event_type} requires full 40-char {name}")
            return value

        if self.event_type in requires_implementation_sha and self.commit_sha is None:
            raise ValueError(f"{self.event_type} requires commit_sha")
        if self.event_type == RunEventType.COPILOT_RESULT_RECORDED:
            commit_sha = _require_full_sha("commit_sha", self.commit_sha)
            head_sha = _require_full_sha("head_sha", self.head_sha)
            if self.expected_head_sha is not None:
                raise ValueError("COPILOT_RESULT_RECORDED must not set expected_head_sha")
            if commit_sha != head_sha:
                raise ValueError("COPILOT_RESULT_RECORDED requires commit_sha == head_sha")
        if self.event_type == RunEventType.CI_EVIDENCE_RECORDED:
            commit_sha = _require_full_sha("commit_sha", self.commit_sha)
            expected_head_sha = _require_full_sha("expected_head_sha", self.expected_head_sha)
            if self.head_sha is not None:
                raise ValueError("CI_EVIDENCE_RECORDED must not set head_sha")
            if commit_sha != expected_head_sha:
                raise ValueError("CI_EVIDENCE_RECORDED requires commit_sha == expected_head_sha")
        if self.event_type == RunEventType.OPENAI_REVIEW_RECORDED:
            if self.review_decision is None:
                raise ValueError("OPENAI_REVIEW_RECORDED requires review_decision")
            commit_sha = _require_full_sha("commit_sha", self.commit_sha)
            expected_head_sha = _require_full_sha("expected_head_sha", self.expected_head_sha)
            if self.head_sha is not None:
                raise ValueError("OPENAI_REVIEW_RECORDED must not set head_sha")
            if commit_sha != expected_head_sha:
                raise ValueError("OPENAI_REVIEW_RECORDED requires commit_sha == expected_head_sha")
        if self.event_type == RunEventType.NEXT_TASK_CREATED and self.next_task_id is None:
            raise ValueError("NEXT_TASK_CREATED requires next_task_id")
        return self

    def demonstrates_progress(self) -> bool:
        return bool(
            self.progress_verified or self.accepted_criteria_delta or self.verified_diff_summary
        )