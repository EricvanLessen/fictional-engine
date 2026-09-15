from __future__ import annotations

from dataclasses import dataclass, field, replace

from development_automation.errors import (
    InvalidTransitionError,
    StaleHeadError,
    StopBoundaryError,
    TaskLimitError,
)
from development_automation.schemas.v1 import (
    AUTHORITATIVE_CONTROL_REF,
    MAX_AGENT_TURNS_WITHOUT_PROGRESS,
    MAX_REPAIR_ATTEMPTS_PER_TASK,
    STOP_REASON_SECOND_TASK_CREATED,
    LifecycleState,
    ReviewDecision,
    RunEventDocument,
    RunEventType,
)


@dataclass(frozen=True)
class TaskProjection:
    task_id: str
    state: LifecycleState
    branch: str
    current_attempt: int = 1
    repair_attempts: int = 0
    no_progress_count: int = 0
    head_sha: str | None = None
    last_review_decision: ReviewDecision | None = None
    provider_run_ids: tuple[str, ...] = ()
    ci_check_names: tuple[str, ...] = ()
    ci_conclusion: str | None = None
    accepted_criteria: tuple[str, ...] = ()
    expected_head_sha: str | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class ControlWorkflowProjection:
    control_ref: str = AUTHORITATIVE_CONTROL_REF
    tasks: dict[str, TaskProjection] = field(default_factory=dict)
    task_order: tuple[str, ...] = ()
    stop_reason: str | None = None


def _event_sort_key(event: RunEventDocument) -> tuple[str, str]:
    return (event.created_at.isoformat(), event.message_id)


def _get_task(projection: ControlWorkflowProjection, task_id: str) -> TaskProjection:
    try:
        return projection.tasks[task_id]
    except KeyError as exc:
        raise InvalidTransitionError(f"task {task_id!r} has not been created") from exc


def _store_task(
    projection: ControlWorkflowProjection,
    task: TaskProjection,
) -> ControlWorkflowProjection:
    updated_tasks = dict(projection.tasks)
    updated_tasks[task.task_id] = task
    return replace(projection, tasks=updated_tasks)


def _ensure_expected_head(task: TaskProjection, event: RunEventDocument) -> None:
    if event.expected_head_sha is None or task.head_sha is None:
        return
    if event.expected_head_sha != task.head_sha:
        raise StaleHeadError(
            "event "
            f"{event.message_id!r} expected head {event.expected_head_sha} "
            f"but task has {task.head_sha}"
        )


def _ensure_state(task: TaskProjection, expected: LifecycleState, event: RunEventDocument) -> None:
    if task.state != expected:
        raise InvalidTransitionError(
            f"event {event.event_type} requires {expected} but task {task.task_id} is {task.state}"
        )


def reduce_run_events(events: list[RunEventDocument]) -> ControlWorkflowProjection:
    projection = ControlWorkflowProjection()
    seen_message_ids: set[str] = set()

    for event in sorted(events, key=_event_sort_key):
        if event.message_id in seen_message_ids:
            continue
        seen_message_ids.add(event.message_id)

        if (
            projection.stop_reason == STOP_REASON_SECOND_TASK_CREATED
            and event.task_id not in projection.task_order[:1]
        ):
            raise StopBoundaryError("task two must not be dispatched in Increment A")

        if event.event_type == RunEventType.TASK_CREATED:
            if event.task_id in projection.tasks:
                raise InvalidTransitionError(f"task {event.task_id!r} already exists")
            task = TaskProjection(
                task_id=event.task_id,
                state=LifecycleState.READY_FOR_COPILOT,
                branch=event.branch,
                current_attempt=event.attempt,
            )
            projection = _store_task(projection, task)
            projection = replace(projection, task_order=(*projection.task_order, event.task_id))
            continue

        task = _get_task(projection, event.task_id)

        if event.event_type == RunEventType.COPILOT_DISPATCHED:
            _ensure_state(task, LifecycleState.READY_FOR_COPILOT, event)
            provider_run_ids = task.provider_run_ids
            if event.provider_run_id and event.provider_run_id not in provider_run_ids:
                provider_run_ids = (*provider_run_ids, event.provider_run_id)
            projection = _store_task(
                projection,
                replace(
                    task,
                    state=LifecycleState.COPILOT_RUNNING,
                    current_attempt=event.attempt,
                    provider_run_ids=provider_run_ids,
                ),
            )
            continue

        if event.event_type == RunEventType.COPILOT_RESULT_RECORDED:
            _ensure_state(task, LifecycleState.COPILOT_RUNNING, event)
            no_progress_count = 0 if event.demonstrates_progress() else task.no_progress_count + 1
            if no_progress_count > MAX_AGENT_TURNS_WITHOUT_PROGRESS:
                raise TaskLimitError("max_agent_turns_without_progress exceeded")
            projection = _store_task(
                projection,
                replace(
                    task,
                    state=LifecycleState.WAITING_FOR_CI,
                    current_attempt=event.attempt,
                    no_progress_count=no_progress_count,
                    head_sha=event.head_sha,
                    accepted_criteria=task.accepted_criteria + event.accepted_criteria_delta,
                    expected_head_sha=event.head_sha,
                ),
            )
            continue

        if event.event_type == RunEventType.CI_EVIDENCE_RECORDED:
            _ensure_state(task, LifecycleState.WAITING_FOR_CI, event)
            _ensure_expected_head(task, event)
            projection = _store_task(
                projection,
                replace(
                    task,
                    state=LifecycleState.WAITING_FOR_OPENAI_REVIEW,
                    ci_check_names=event.ci_check_names,
                    ci_conclusion=event.ci_conclusion,
                ),
            )
            continue

        if event.event_type == RunEventType.OPENAI_REVIEW_RECORDED:
            _ensure_state(task, LifecycleState.WAITING_FOR_OPENAI_REVIEW, event)
            _ensure_expected_head(task, event)
            decision = event.review_decision
            if decision is None:
                raise InvalidTransitionError("review decision is required")
            if decision == ReviewDecision.FIX_REQUIRED:
                repair_attempts = task.repair_attempts + 1
                if repair_attempts > MAX_REPAIR_ATTEMPTS_PER_TASK:
                    raise TaskLimitError("max_repair_attempts_per_task exceeded")
                projection = _store_task(
                    projection,
                    replace(
                        task,
                        state=LifecycleState.READY_FOR_COPILOT,
                        repair_attempts=repair_attempts,
                        last_review_decision=decision,
                    ),
                )
                continue
            if decision in {ReviewDecision.BLOCKED, ReviewDecision.HUMAN_DECISION_REQUIRED}:
                projection = _store_task(
                    projection,
                    replace(
                        task,
                        state=LifecycleState.PAUSED,
                        last_review_decision=decision,
                    ),
                )
                continue
            projection = _store_task(
                projection,
                replace(
                    task,
                    state=LifecycleState.COMPLETED,
                    last_review_decision=decision,
                ),
            )
            continue

        if event.event_type == RunEventType.NEXT_TASK_CREATED:
            if task.state != LifecycleState.COMPLETED:
                raise InvalidTransitionError("next task can only be created after completion")
            next_task_id = event.next_task_id
            if next_task_id is None:
                raise InvalidTransitionError("next task ID is required")
            if next_task_id in projection.tasks:
                raise InvalidTransitionError(f"task {next_task_id!r} already exists")
            next_task = TaskProjection(
                task_id=next_task_id,
                state=LifecycleState.READY_FOR_COPILOT,
                branch=event.branch,
            )
            projection = _store_task(projection, next_task)
            projection = _store_task(
                projection,
                replace(task, stop_reason=STOP_REASON_SECOND_TASK_CREATED),
            )
            projection = replace(
                projection,
                task_order=(*projection.task_order, next_task_id),
                stop_reason=STOP_REASON_SECOND_TASK_CREATED,
            )
            continue

        if event.event_type == RunEventType.TASK_PAUSED:
            projection = _store_task(projection, replace(task, state=LifecycleState.PAUSED))
            continue

        if event.event_type == RunEventType.TASK_COMPLETED:
            projection = _store_task(projection, replace(task, state=LifecycleState.COMPLETED))
            continue

        raise InvalidTransitionError(f"unsupported event type: {event.event_type}")

    return projection