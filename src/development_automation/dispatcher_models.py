from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_WEBHOOK_BODY_BYTES = 256_000


class DispatchEventType(str):
    PUSH = "push"
    PULL_REQUEST = "pull_request"
    WORKFLOW_RUN = "workflow_run"
    CHECK_RUN = "check_run"
    ISSUES = "issues"
    ISSUE_COMMENT = "issue_comment"


SUPPORTED_EVENT_TYPES = {
    DispatchEventType.PUSH,
    DispatchEventType.PULL_REQUEST,
    DispatchEventType.WORKFLOW_RUN,
    DispatchEventType.CHECK_RUN,
    DispatchEventType.ISSUES,
    DispatchEventType.ISSUE_COMMENT,
}


class EventSource(str):
    WEBHOOK = "webhook"
    ACTIONS = "actions"


class CheckRunEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    conclusion: str | None = None
    head_sha: str
    workflow_ref: str | None = None


class DispatcherPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowlisted_repositories: tuple[str, ...]
    allowlisted_actors: tuple[str, ...]
    expected_check_names: tuple[str, ...] = ()
    trusted_workflow_refs: tuple[str, ...] = ()
    trusted_actions_refs: tuple[str, ...] = ()
    ignored_actors: tuple[str, ...] = ("github-actions[bot]",)
    max_webhook_body_bytes: int = MAX_WEBHOOK_BODY_BYTES


class DispatcherEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str
    event_type: str
    source: str
    repository: str
    actor: str
    task_id: str | None = None
    branch: str | None = None
    pull_request_number: int | None = None
    head_sha: str | None = None
    comment_body: str | None = None
    checks: tuple[CheckRunEvidence, ...] = ()
    raw_body: bytes = Field(default=b"")
    signature_sha256: str | None = None
    actions_workflow_ref: str | None = None
    actions_authenticated: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)

    def semantic_key(self) -> str:
        task_id = self.task_id or "none"
        head_sha = self.head_sha or "none"
        pr_number = self.pull_request_number or 0
        return f"{self.event_type}:{self.repository}:{task_id}:{head_sha}:{pr_number}"


def verify_webhook_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if signature_header is None:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return hmac.compare_digest(signature_header, expected)


def is_supported_event_type(event_type: str) -> bool:
    return event_type in SUPPORTED_EVENT_TYPES


def serialize_event_payload(event: DispatcherEvent) -> str:
    return json.dumps(event.payload, sort_keys=True)