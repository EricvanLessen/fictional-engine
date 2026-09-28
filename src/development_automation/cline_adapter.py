from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from development_automation.live_adapters import (
    ProviderDispatchResult,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    PullRequestMetadata,
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

DEFAULT_COMMAND_PERMISSIONS = {
    "deny": [
        "rm -rf *",
        "sudo *",
        "git push --force*",
        "git reset --hard*",
        "git clean -f*",
        "* --delete *",
        "env",
        "env *",
        "printenv",
        "printenv *",
        "export *",
    ],
    "allowRedirects": False,
}


class ClineOpenRouterCodingAgent:
    """Run one bounded Cline coding session and require it to open a pull request."""

    def __init__(
        self,
        *,
        repository: str,
        github_token: str,
        openrouter_api_key: str,
        repository_root: Path,
        model: str = "openrouter/auto",
        timeout_seconds: int = 1800,
        retries: int = 3,
        thinking: str = "medium",
        cline_executable: str = "cline",
        github_base_url: str = "https://api.github.com",
        http_client: httpx.Client | None = None,
        command_runner: CommandRunner = subprocess.run,
        data_root: Path | None = None,
    ) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        if timeout_seconds < 1 or timeout_seconds > 3600:
            raise ValueError("timeout_seconds must be between 1 and 3600")
        if retries < 0 or retries > 10:
            raise ValueError("retries must be between 0 and 10")
        if thinking not in {"none", "low", "medium", "high", "xhigh"}:
            raise ValueError("thinking must be a supported Cline reasoning level")

        self._owner, self._repo = repository.split("/", 1)
        self._repository = repository
        self._github_token = github_token
        self._openrouter_api_key = openrouter_api_key
        self._repository_root = repository_root.resolve()
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._retries = retries
        self._thinking = thinking
        self._cline_executable = cline_executable
        self._github_base_url = github_base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=30.0)
        self._command_runner = command_runner
        self._data_root = data_root or Path(tempfile.gettempdir()) / "fictional-engine-cline"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self._github_token,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                f"{self._github_base_url}{path}",
                headers=self._headers(),
                params=params,
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("GitHub request for Cline timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("GitHub request for Cline failed") from exc
        if response.status_code == 429 or (
            response.status_code == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or response.headers.get("retry-after") is not None
            )
        ):
            raise ProviderRateLimitError("GitHub request for Cline was rate limited")
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"GitHub returned HTTP {response.status_code} for Cline request"
            )
        return response

    @staticmethod
    def _marker(name: str, value: str) -> str:
        return f"<!-- development-automation:{name}:{value} -->"

    @staticmethod
    def _pull_request_result(
        correlation_id: str,
        pull_request: dict[str, Any],
    ) -> ProviderDispatchResult:
        number = pull_request.get("number")
        url = pull_request.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str):
            raise ProviderResponseError("GitHub pull request response was malformed")
        return ProviderDispatchResult(
            correlation_id=correlation_id,
            status="accepted",
            provider_run_id=f"pr:{number}",
            task_number=number,
            task_url=url,
        )

    def _find_pull_request_by_branch(self, branch: str) -> dict[str, Any] | None:
        response = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/pulls",
            params={"state": "all", "head": f"{self._owner}:{branch}", "per_page": 10},
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ProviderResponseError("GitHub pull request list must be an array")
        return payload[0] if payload and isinstance(payload[0], dict) else None

    def _find_pull_request_by_correlation(
        self,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        marker = self._marker("correlation_id", correlation_id)
        page = 1
        while True:
            response = self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/pulls",
                params={"state": "all", "per_page": 100, "page": page},
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise ProviderResponseError("GitHub pull request list must be an array")
            for pull_request in payload:
                if not isinstance(pull_request, dict):
                    continue
                body = pull_request.get("body")
                if isinstance(body, str) and marker in body:
                    return pull_request
            if len(payload) < 100:
                return None
            page += 1

    def _find_issue_by_correlation(self, correlation_id: str) -> dict[str, Any] | None:
        marker = self._marker("correlation_id", correlation_id)
        response = self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/issues",
            params={"state": "all", "per_page": 100},
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ProviderResponseError("GitHub issue list must be an array")
        for issue in payload:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            body = issue.get("body")
            if isinstance(body, str) and marker in body:
                return issue
        return None

    @staticmethod
    def _issue_result(
        correlation_id: str,
        issue: dict[str, Any],
    ) -> ProviderDispatchResult:
        number = issue.get("number")
        url = issue.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str):
            raise ProviderResponseError("GitHub issue response was malformed")
        return ProviderDispatchResult(
            correlation_id=correlation_id,
            status="completed",
            provider_run_id=f"issue:{number}",
            task_number=number,
            task_url=url,
        )

    def _prompt(
        self,
        *,
        task_id: str,
        branch: str,
        head_sha: str | None,
        correlation_id: str,
    ) -> str:
        marker = self._marker("correlation_id", correlation_id)
        return f"""You are the only active coding agent for {self._repository}.

Implement {task_id} autonomously. The authoritative instruction is the newest
TASK_INSTRUCTION for {task_id} under control/messages. Read only the control files needed
for this task. Work on the exact branch `{branch}` from the repository default branch.
The triggering head SHA was `{head_sha or 'not supplied'}`.

Constraints:
- Keep the change narrowly scoped to the task and preserve unrelated work.
- Never read, print, commit, or expose credentials or environment variables.
- Do not modify or commit control/messages, control/runs, lock files, or runtime state.
- Do not deploy, trade, merge, force-push, or bypass tests.
- Run the relevant tests, ruff, and mypy before publishing.
- Use one agent only; do not spawn sub-agents or teams.
- Commit the implementation, push `{branch}`, and open one draft pull request against `main`.
- The pull request body must include this exact correlation marker:
  {marker}
- If the task is ambiguous or unsafe, stop without opening a pull request and fail clearly.

Finish only after the draft pull request exists on GitHub.
"""

    def _command(self, prompt: str, correlation_id: str) -> list[str]:
        data_dir = self._data_root / correlation_id
        return [
            self._cline_executable,
            "--json",
            "--yolo",
            "--retries",
            str(self._retries),
            "--timeout",
            str(self._timeout_seconds),
            "--provider",
            "openrouter",
            "--model",
            self._model,
            "--thinking",
            self._thinking,
            "--cwd",
            str(self._repository_root),
            "--data-dir",
            str(data_dir),
            prompt,
        ]

    def run(
        self,
        *,
        task_id: str,
        head_sha: str | None,
        correlation_id: str,
        branch: str,
    ) -> ProviderDispatchResult:
        existing = self._find_pull_request_by_branch(branch)
        if existing is not None:
            return self._pull_request_result(correlation_id, existing)

        prompt = self._prompt(
            task_id=task_id,
            branch=branch,
            head_sha=head_sha,
            correlation_id=correlation_id,
        )
        environment = os.environ.copy()
        environment["OPENROUTER_API_KEY"] = self._openrouter_api_key
        environment["GH_TOKEN"] = self._github_token
        environment["GITHUB_TOKEN"] = self._github_token
        environment.setdefault(
            "CLINE_COMMAND_PERMISSIONS",
            json.dumps(DEFAULT_COMMAND_PERMISSIONS, separators=(",", ":")),
        )
        environment.setdefault("CLINE_LOG_ENABLED", "0")

        try:
            completed = self._command_runner(
                self._command(prompt, correlation_id),
                cwd=self._repository_root,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=self._timeout_seconds + 30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError("Cline coding run exceeded its timeout") from exc
        except OSError as exc:
            raise ProviderError("Cline CLI could not be started") from exc

        if completed.returncode != 0:
            raise ProviderError(f"Cline coding run failed with exit code {completed.returncode}")

        pull_request = self._find_pull_request_by_branch(branch)
        if pull_request is None:
            raise ProviderResponseError(
                "Cline completed without creating the required pull request"
            )
        return self._pull_request_result(correlation_id, pull_request)

    def reconcile(self, correlation_id: str) -> ProviderDispatchResult | None:
        pull_request = self._find_pull_request_by_correlation(correlation_id)
        if pull_request is None:
            return None
        result = self._pull_request_result(correlation_id, pull_request)
        return ProviderDispatchResult(
            correlation_id=result.correlation_id,
            status="completed",
            provider_run_id=result.provider_run_id,
            task_number=result.task_number,
            task_url=result.task_url,
        )

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
        if dispatch:
            raise ProviderError("Cline task dispatch requires a bound run event")
        existing = self._find_issue_by_correlation(correlation_id)
        if existing is not None:
            return self._issue_result(correlation_id, existing)
        marked_body = "\n".join(
            (
                self._marker("managed", task_id),
                self._marker("correlation_id", correlation_id),
                f"Target branch: `{branch}`",
                "",
                body,
            )
        )
        response = self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues",
            json_body={"title": f"[{task_id}] {title}", "body": marked_body},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderResponseError("GitHub issue creation response must be an object")
        return self._issue_result(correlation_id, payload)

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
        response = self._client.get(
            f"{self._github_base_url}/repos/{self._owner}/{self._repo}/pulls/"
            f"{pull_request_number}",
            headers={**self._headers(), "Accept": "application/vnd.github.diff"},
        )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"GitHub returned HTTP {response.status_code} for pull request diff"
            )
        return response.text
