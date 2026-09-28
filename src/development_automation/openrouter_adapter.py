from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from development_automation.live_adapters import (
    OpenAIReviewOutcome,
    OpenAIReviewResult,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ReviewContext,
)


class OpenRouterReviewAdapter:
    """Review a completed implementation through OpenRouter's structured API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openrouter/auto",
        cost_quality_tradeoff: int = 7,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not 0 <= cost_quality_tradeoff <= 10:
            raise ValueError("cost_quality_tradeoff must be between 0 and 10")
        self._api_key = api_key
        self._model = model
        self._cost_quality_tradeoff = cost_quality_tradeoff
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json",
            "X-Title": "fictional-engine development automation",
        }

    def _request_payload(self, context: ReviewContext) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the bounded reviewer for one GitHub-driven implementation "
                        "cycle. Return exactly one structured decision. Use "
                        "HUMAN_DECISION_REQUIRED for ambiguity."
                    ),
                },
                {"role": "user", "content": context.model_dump_json(indent=2)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "development_automation_review",
                    "strict": True,
                    "schema": OpenAIReviewResult.model_json_schema(),
                },
            },
            "provider": {"require_parameters": True},
            "session_id": f"{context.task_id}:{context.attempt}:{context.pull_request.head_sha}",
        }
        if self._model == "openrouter/auto":
            payload["plugins"] = [
                {
                    "id": "auto-router",
                    "cost_quality_tradeoff": self._cost_quality_tradeoff,
                }
            ]
        return payload

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError("OpenRouter response did not contain choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderResponseError("OpenRouter response choice was malformed")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ProviderResponseError("OpenRouter response did not contain a message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderResponseError("OpenRouter response did not contain text")
        return content

    def review(self, context: ReviewContext) -> OpenAIReviewOutcome:
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=self._request_payload(context),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("OpenRouter review request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("OpenRouter review request failed") from exc

        if response.status_code == 429:
            raise ProviderRateLimitError("OpenRouter review request was rate limited")
        if response.status_code >= 400:
            raise ProviderResponseError(f"OpenRouter returned HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("OpenRouter response body must be an object")
        response_id = payload.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise ProviderResponseError("OpenRouter response is missing its response ID")
        try:
            result_payload = json.loads(self._extract_content(payload))
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("OpenRouter structured output was not valid JSON") from exc
        try:
            result = OpenAIReviewResult.model_validate(result_payload)
        except ValidationError as exc:
            raise ProviderResponseError(
                "OpenRouter structured output failed schema validation"
            ) from exc
        return OpenAIReviewOutcome(provider_run_id=response_id, result=result)
