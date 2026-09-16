from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from development_automation.errors import DevelopmentAutomationError
from development_automation.schemas.v1 import ReviewDecision

LOGGER = logging.getLogger(__name__)


class ProviderError(DevelopmentAutomationError):
    """Raised when a live provider call cannot be completed safely."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider call times out."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider applies throttling."""


class ProviderResponseError(ProviderError):
    """Raised when a provider response is malformed or unusable."""


@dataclass(frozen=True)
class ProviderDispatchResult:
    correlation_id: str
    status: str
    provider_run_id: str
    task_number: int | None = None
    task_url: str | None = None


class PullRequestMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    title: str
    body: str | None = None
    html_url: str
    head_sha: str
    base_ref: str
    head_ref: str


class ReviewContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    task_id: str
    attempt: int
    branch: str
    task_message: str
    result_message: str
    pull_request: PullRequestMetadata
    pull_request_diff: str
    ci_summary: tuple[str, ...]
    control_documents: dict[str, str]


class OpenAIReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    next_task_title: str | None = None
    next_task_body: str | None = None
    accepted_criteria: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_follow_up_shape(self) -> OpenAIReviewResult:
        if self.decision == ReviewDecision.NEXT_TASK:
            if not self.next_task_title or not self.next_task_body:
                raise ValueError("NEXT_TASK requires next_task_title and next_task_body")
            return self
        if self.next_task_title is not None or self.next_task_body is not None:
            raise ValueError("follow-up task fields are only allowed for NEXT_TASK")
        return self


@dataclass(frozen=True)
class OpenAIReviewOutcome:
    provider_run_id: str
    result: OpenAIReviewResult


class OpenAIReviewAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json",
        }

    def _request_payload(self, context: ReviewContext) -> dict[str, object]:
        schema = OpenAIReviewResult.model_json_schema()
        return {
            "model": self._model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are the bounded reviewer for one GitHub-driven implementation "
                                "cycle. Return exactly one structured decision. Use "
                                "HUMAN_DECISION_REQUIRED for ambiguity."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": context.model_dump_json(indent=2),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "development_automation_review",
                    "strict": True,
                    "schema": schema,
                }
            },
        }

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = payload.get("output")
        if not isinstance(output, list):
            raise ProviderResponseError("OpenAI response did not contain structured output")
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        raise ProviderResponseError("OpenAI response did not contain any text output")

    def review(self, context: ReviewContext) -> OpenAIReviewOutcome:
        request_payload = self._request_payload(context)
        try:
            response = self._client.post(
                f"{self._base_url}/responses",
                headers=self._headers(),
                json=request_payload,
            )
        except httpx.TimeoutException as exc:
            LOGGER.warning(
                "OpenAI review timed out for task %s attempt %s",
                context.task_id,
                context.attempt,
            )
            raise ProviderTimeoutError("OpenAI review request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("OpenAI review request failed") from exc

        if response.status_code == 429:
            raise ProviderRateLimitError("OpenAI review request was rate limited")
        if response.status_code >= 400:
            raise ProviderResponseError(f"OpenAI returned HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("OpenAI response body must be an object")
        response_id = payload.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise ProviderResponseError("OpenAI response is missing its response ID")

        try:
            result_payload = json.loads(self._extract_output_text(payload))
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("OpenAI structured output was not valid JSON") from exc
        try:
            result = OpenAIReviewResult.model_validate(result_payload)
        except ValidationError as exc:
            raise ProviderResponseError(
                "OpenAI structured output failed schema validation"
            ) from exc
        return OpenAIReviewOutcome(provider_run_id=response_id, result=result)


class GitHubCopilotCodingAgent:
    def __init__(
        self,
        *,
        repository: str,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
        copilot_assignee: str = "copilot-swe-agent",
    ) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        owner, repo = repository.split("/", 1)
        self._owner = owner
        self._repo = repo
        self._repository = repository
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._copilot_assignee = copilot_assignee
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def _headers(self, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Accept": accept,
            "Authorization": "Bearer " + self._token,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _marker(name: str, value: str) -> str:
        return f"<!-- development-automation:{name}:{value} -->"

    def _request(
        self,
        method: str,
        path: str,
        *,
        accept: str = "application/vnd.github+json",
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers(accept=accept),
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("GitHub provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("GitHub provider request failed") from exc
        if response.status_code == 429:
            raise ProviderRateLimitError("GitHub provider request was rate limited")
        if response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after") is not None
            or response.headers.get("x-ratelimit-reset") is not None
        ):
            raise ProviderRateLimitError("GitHub provider request was rate limited")
        if response.status_code >= 400:
            raise ProviderResponseError(f"GitHub returned HTTP {response.status_code} for {path}")
        return response

    def _find_issue_by_correlation(self, correlation_id: str) -> dict[str, Any] | None:
        marker = self._marker("correlation_id", correlation_id)
        page = 1
        while True:
            response = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/issues?state=all&per_page=100&page={page}",
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise ProviderResponseError("GitHub issues list response must be a list")
            issue_candidates = [item for item in payload if isinstance(item, dict)]
            for issue in issue_candidates:
                body = issue.get("body")
                if isinstance(body, str) and marker in body:
                    return issue
            if len(issue_candidates) < 100:
                break
            page += 1
        return None

    def _render_issue_body(
        self,
        *,
        task_id: str,
        branch: str,
        correlation_id: str,
        title: str,
        body: str,
        dispatch: bool,
    ) -> str:
        dispatch_state = "dispatch" if dispatch else "record-only"
        return (
            f"{self._marker('task_id', task_id)}\n"
            f"{self._marker('branch', branch)}\n"
            f"{self._marker('correlation_id', correlation_id)}\n"
            f"{self._marker('repository', self._repository)}\n"
            f"{self._marker('mode', dispatch_state)}\n\n"
            f"# {title}\n\n"
            f"{body.strip()}\n"
        )

    @staticmethod
    def _issue_reference(issue: dict[str, Any]) -> ProviderDispatchResult:
        number = issue.get("number")
        html_url = issue.get("html_url")
        if not isinstance(number, int):
            raise ProviderResponseError("GitHub issue response is missing number")
        if not isinstance(html_url, str):
            raise ProviderResponseError("GitHub issue response is missing html_url")
        return ProviderDispatchResult(
            correlation_id="",
            status="running",
            provider_run_id=f"issue:{number}",
            task_number=number,
            task_url=html_url,
        )

    def _has_assignee(self, issue: dict[str, Any], assignee: str) -> bool:
        assignees = issue.get("assignees")
        if not isinstance(assignees, list):
            return False
        for entry in assignees:
            if isinstance(entry, dict) and entry.get("login") == assignee:
                return True
        return False

    def create_or_update_task(
        self,
        *,
        task_id: str,
        branch: str,
        correlation_id: str,
        title: str,
        body: str,
        dispatch: bool,
    ) -> ProviderDispatchResult:
        rendered_body = self._render_issue_body(
            task_id=task_id,
            branch=branch,
            correlation_id=correlation_id,
            title=title,
            body=body,
            dispatch=dispatch,
        )
        existing = self._find_issue_by_correlation(correlation_id)
        if existing is None:
            response = self._request(
                "POST",
                f"/repos/{self._owner}/{self._repo}/issues",
                json_body={"title": f"[{task_id}] {title}", "body": rendered_body},
            )
            issue = response.json()
        else:
            issue = existing
            number = issue.get("number")
            current_title = issue.get("title")
            current_body = issue.get("body")
            if not isinstance(number, int):
                raise ProviderResponseError("GitHub issue response is missing number")
            if current_title != f"[{task_id}] {title}" or current_body != rendered_body:
                response = self._request(
                    "PATCH",
                    f"/repos/{self._owner}/{self._repo}/issues/{number}",
                    json_body={"title": f"[{task_id}] {title}", "body": rendered_body},
                )
                issue = response.json()

        if not isinstance(issue, dict):
            raise ProviderResponseError("GitHub issue response must be an object")
        result = self._issue_reference(issue)

        if dispatch and not self._has_assignee(issue, self._copilot_assignee):
            number = result.task_number
            if number is None:
                raise ProviderResponseError("GitHub issue response is missing issue number")
            self._request(
                "POST",
                f"/repos/{self._owner}/{self._repo}/issues/{number}/assignees",
                json_body={"assignees": [self._copilot_assignee]},
            )

        return ProviderDispatchResult(
            correlation_id=correlation_id,
            status="running" if dispatch else "completed",
            provider_run_id=result.provider_run_id,
            task_number=result.task_number,
            task_url=result.task_url,
        )

    def run(
        self,
        *,
        task_id: str | None,
        head_sha: str | None,
        correlation_id: str,
        branch: str | None,
    ) -> ProviderDispatchResult:
        if task_id is None or branch is None:
            raise ProviderResponseError("GitHub coding-agent dispatch requires task_id and branch")
        title = "Copilot implementation request"
        body = (
            f"Repository: {self._repository}\n"
            f"Task: {task_id}\n"
            f"Branch: {branch}\n"
            f"Expected head: {head_sha or '[none yet]'}\n"
            f"Assigned agent: {self._copilot_assignee}\n"
        )
        return self.create_or_update_task(
            task_id=task_id,
            branch=branch,
            correlation_id=correlation_id,
            title=title,
            body=body,
            dispatch=True,
        )

    def reconcile(self, correlation_id: str) -> ProviderDispatchResult | None:
        issue = self._find_issue_by_correlation(correlation_id)
        if issue is None:
            return None
        result = self._issue_reference(issue)
        state = issue.get("state")
        status = "completed" if state == "closed" else "running"
        return ProviderDispatchResult(
            correlation_id=correlation_id,
            status=status,
            provider_run_id=result.provider_run_id,
            task_number=result.task_number,
            task_url=result.task_url,
        )

    def get_pull_request(self, pull_request_number: int) -> PullRequestMetadata:
        response = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/pulls/{pull_request_number}",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("GitHub pull request response must be an object")
        head = payload.get("head")
        base = payload.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise ProviderResponseError("GitHub pull request response is missing refs")
        return PullRequestMetadata(
            number=int(payload["number"]),
            title=str(payload["title"]),
            body=payload.get("body"),
            html_url=str(payload["html_url"]),
            head_sha=str(head["sha"]),
            head_ref=str(head["ref"]),
            base_ref=str(base["ref"]),
        )

    def get_pull_request_diff(self, pull_request_number: int) -> str:
        response = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/pulls/{pull_request_number}",
            accept="application/vnd.github.diff",
        )
        return response.text
