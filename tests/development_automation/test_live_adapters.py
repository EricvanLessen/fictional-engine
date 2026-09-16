from __future__ import annotations

import json
import logging

import httpx
import pytest

from development_automation.live_adapters import (
    GitHubCopilotCodingAgent,
    OpenAIReviewAdapter,
    OpenAIReviewResult,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    PullRequestMetadata,
    ReviewContext,
)


def _review_context() -> ReviewContext:
    return ReviewContext(
        repository="EricvanLessen/fictional-engine",
        task_id="task-0009",
        attempt=1,
        branch="feat/development-automation-live-adapters",
        task_message="# Objective\n\nImplement Increment C.\n",
        result_message="# Result\n\nPR opened.\n",
        pull_request=PullRequestMetadata(
            number=10,
            title="Implement Increment C",
            body="Bounded proof cycle.",
            html_url="https://github.com/EricvanLessen/fictional-engine/pull/10",
            head_sha="0123456789abcdef0123456789abcdef01234567",
            base_ref="main",
            head_ref="feat/development-automation-live-adapters",
        ),
        pull_request_diff="diff --git a/file b/file\n+change\n",
        ci_summary=("ruff:completed:success:0123456789abcdef0123456789abcdef01234567",),
        control_documents={
            "GOAL.md": "goal",
            "ARCHITECTURE.md": "arch",
            "CURRENT_STATE.md": "state",
            "DECISIONS.md": "decisions",
        },
    )


def test_openai_review_accepts_valid_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["Authorization"]
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "gpt-5-mini"
        assert "task-0009" in json.dumps(payload)
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "output_text": json.dumps(
                    {
                        "decision": "NEXT_TASK",
                        "summary": "One follow-up task is needed.",
                        "rationale": "Increment C proof should stop after task two is created.",
                        "next_task_title": "Increment D notification path",
                        "next_task_body": "Implement notifications only after proof stop.",
                        "accepted_criteria": [
                            "second task exists",
                            "second task is not dispatched",
                        ],
                    }
                ),
            },
        )

    adapter = OpenAIReviewAdapter(
        api_key="openai-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    outcome = adapter.review(_review_context())

    assert outcome.provider_run_id == "resp_123"
    assert outcome.result == OpenAIReviewResult(
        decision="NEXT_TASK",
        summary="One follow-up task is needed.",
        rationale="Increment C proof should stop after task two is created.",
        next_task_title="Increment D notification path",
        next_task_body="Implement notifications only after proof stop.",
        accepted_criteria=("second task exists", "second task is not dispatched"),
    )


def test_openai_review_rejects_malformed_structured_output() -> None:
    adapter = OpenAIReviewAdapter(
        api_key="openai-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "id": "resp_123",
                        "output_text": json.dumps(
                            {
                                "decision": "NEXT_TASK",
                                "summary": "Missing follow-up fields",
                                "rationale": "invalid",
                            }
                        ),
                    },
                )
            )
        ),
    )

    with pytest.raises(ProviderResponseError):
        adapter.review(_review_context())


def test_openai_review_handles_timeout_rate_limit_and_provider_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = _review_context()
    caplog.set_level(logging.WARNING)

    timeout_adapter = OpenAIReviewAdapter(
        api_key="openai-secret-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("boom"))
            )
        ),
    )
    with pytest.raises(ProviderTimeoutError):
        timeout_adapter.review(context)
    assert "openai-secret-token" not in caplog.text

    rate_limited = OpenAIReviewAdapter(
        api_key="openai-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, json={"error": "slow down"})
            )
        ),
    )
    with pytest.raises(ProviderRateLimitError):
        rate_limited.review(context)

    provider_error = OpenAIReviewAdapter(
        api_key="openai-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: (_ for _ in ()).throw(httpx.ConnectError("offline"))
            )
        ),
    )
    with pytest.raises(ProviderError):
        provider_error.review(context)


def test_github_copilot_task_assignment_is_idempotent() -> None:
    calls: list[tuple[str, str]] = []
    issue_body = (
        "<!-- development-automation:correlation_id:corr-1 -->\n"
        "# Copilot implementation request\n"
    )
    issue_payload = {
        "number": 42,
        "title": "[task-0009] Copilot implementation request",
        "body": issue_body,
        "html_url": "https://github.com/EricvanLessen/fictional-engine/issues/42",
        "state": "open",
        "assignees": [{"login": "copilot-swe-agent"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[issue_payload])
        if request.method == "POST" and request.url.path.endswith("/issues"):
            request_payload = json.loads(request.content.decode("utf-8"))
            created = dict(issue_payload)
            created["assignees"] = []
            created["body"] = request_payload["body"]
            return httpx.Response(200, json=created)
        if request.method == "PATCH" and request.url.path.endswith("/issues/42"):
            request_payload = json.loads(request.content.decode("utf-8"))
            updated = dict(issue_payload)
            updated["body"] = request_payload["body"]
            updated["assignees"] = issue_payload["assignees"]
            return httpx.Response(200, json=updated)
        if request.method == "POST" and request.url.path.endswith("/assignees"):
            return httpx.Response(201, json=issue_payload)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    agent = GitHubCopilotCodingAgent(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    first = agent.create_or_update_task(
        task_id="task-0009",
        branch="feat/development-automation-live-adapters",
        correlation_id="corr-1",
        title="Copilot implementation request",
        body="Do the work.",
        dispatch=True,
    )
    second = agent.create_or_update_task(
        task_id="task-0009",
        branch="feat/development-automation-live-adapters",
        correlation_id="corr-1",
        title="Copilot implementation request",
        body="Do the work.",
        dispatch=True,
    )

    assert first.provider_run_id == "issue:42"
    assert second.provider_run_id == "issue:42"
    assert calls.count(("POST", "/repos/EricvanLessen/fictional-engine/issues")) == 1
    assert calls.count(("POST", "/repos/EricvanLessen/fictional-engine/issues/42/assignees")) == 1


def test_github_reconcile_finds_existing_issue_without_new_assignment() -> None:
    issue_payload = {
        "number": 42,
        "title": "[task-0009] Copilot implementation request",
        "body": "<!-- development-automation:correlation_id:corr-2 -->",
        "html_url": "https://github.com/EricvanLessen/fictional-engine/issues/42",
        "state": "open",
        "assignees": [{"login": "copilot-swe-agent"}],
    }

    agent = GitHubCopilotCodingAgent(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[issue_payload]))
        ),
    )

    reconciled = agent.reconcile("corr-2")

    assert reconciled is not None
    assert reconciled.status == "running"
    assert reconciled.provider_run_id == "issue:42"


def test_github_rate_limit_403_is_retryable() -> None:
    agent = GitHubCopilotCodingAgent(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    403,
                    headers={"x-ratelimit-remaining": "0", "retry-after": "60"},
                    json={"message": "secondary rate limit"},
                )
            )
        ),
    )

    with pytest.raises(ProviderRateLimitError):
        agent.reconcile("corr-3")


def test_github_issue_lookup_paginates_until_match() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": index,
                        "title": f"Issue {index}",
                        "body": "unrelated",
                        "html_url": f"https://github.com/EricvanLessen/fictional-engine/issues/{index}",
                        "state": "open",
                        "assignees": [],
                    }
                    for index in range(1, 101)
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "number": 101,
                    "title": "Matched issue",
                    "body": "<!-- development-automation:correlation_id:corr-101 -->",
                    "html_url": "https://github.com/EricvanLessen/fictional-engine/issues/101",
                    "state": "open",
                    "assignees": [],
                }
            ],
        )

    agent = GitHubCopilotCodingAgent(
        repository="EricvanLessen/fictional-engine",
        token="github-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    reconciled = agent.reconcile("corr-101")

    assert reconciled is not None
    assert reconciled.provider_run_id == "issue:101"
    assert any("page=2" in call for call in calls)
