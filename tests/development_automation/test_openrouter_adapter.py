from __future__ import annotations

import json

import httpx
import pytest

from development_automation.live_adapters import (
    OpenAIReviewResult,
    ProviderRateLimitError,
    ProviderResponseError,
    PullRequestMetadata,
    ReviewContext,
)
from development_automation.openrouter_adapter import OpenRouterReviewAdapter


def _context() -> ReviewContext:
    return ReviewContext(
        repository="EricvanLessen/fictional-engine",
        task_id="task-0009",
        attempt=1,
        branch="feat/agent-task",
        task_message="# Task\n",
        result_message="# Result\n",
        pull_request=PullRequestMetadata(
            number=17,
            title="Implement task",
            body="Done",
            html_url="https://github.com/EricvanLessen/fictional-engine/pull/17",
            head_sha="a" * 40,
            base_ref="main",
            head_ref="feat/agent-task",
        ),
        pull_request_diff="diff --git a/a.py b/a.py\n+pass\n",
        ci_summary=("checks:completed:success:" + "a" * 40,),
        control_documents={"GOAL.md": "goal"},
    )


def test_openrouter_review_uses_auto_router_and_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/chat/completions"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "openrouter/auto"
        assert payload["plugins"] == [
            {"id": "auto-router", "cost_quality_tradeoff": 7}
        ]
        assert payload["provider"]["require_parameters"] is True
        assert payload["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "id": "gen-123",
                "model": "example/selected-model",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "ACCEPT",
                                    "summary": "Implementation is complete.",
                                    "rationale": "Tests and criteria pass.",
                                    "accepted_criteria": ["tests pass"],
                                }
                            )
                        }
                    }
                ],
            },
        )

    adapter = OpenRouterReviewAdapter(
        api_key="openrouter-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    outcome = adapter.review(_context())

    assert outcome.provider_run_id == "gen-123"
    assert outcome.result == OpenAIReviewResult(
        decision="ACCEPT",
        summary="Implementation is complete.",
        rationale="Tests and criteria pass.",
        accepted_criteria=("tests pass",),
    )


def test_openrouter_review_rejects_invalid_output() -> None:
    adapter = OpenRouterReviewAdapter(
        api_key="openrouter-secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "id": "gen-123",
                        "choices": [{"message": {"content": "not json"}}],
                    },
                )
            )
        ),
    )

    with pytest.raises(ProviderResponseError):
        adapter.review(_context())


def test_openrouter_review_maps_rate_limit() -> None:
    adapter = OpenRouterReviewAdapter(
        api_key="openrouter-secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(429))
        ),
    )

    with pytest.raises(ProviderRateLimitError):
        adapter.review(_context())
