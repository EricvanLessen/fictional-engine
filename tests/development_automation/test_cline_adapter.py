from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from development_automation.cline_adapter import ClineOpenRouterCodingAgent
from development_automation.live_adapters import (
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
)


def _pull_request(branch: str, correlation_id: str = "corr-1") -> dict[str, object]:
    return {
        "number": 17,
        "title": "Implement task",
        "body": f"<!-- development-automation:correlation_id:{correlation_id} -->",
        "html_url": "https://github.com/EricvanLessen/fictional-engine/pull/17",
        "state": "open",
        "head": {"ref": branch, "sha": "a" * 40},
        "base": {"ref": "main"},
    }


def test_cline_run_keeps_secrets_out_of_arguments_and_requires_pr(tmp_path: Path) -> None:
    branch = "feat/agent-task"
    cline_finished = False
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer github-secret"
        if request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[_pull_request(branch)] if cline_finished else [])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def runner(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal cline_finished
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        cline_finished = True
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        command_runner=runner,
        data_root=tmp_path / "cline-data",
    )

    result = agent.run(
        task_id="task-0009",
        head_sha="b" * 40,
        correlation_id="corr-1",
        branch=branch,
    )

    assert result.provider_run_id == "pr:17"
    assert result.status == "accepted"
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == ["cline", "--json", "--yolo"]
    assert "openrouter" in command
    assert "openrouter/auto" in command
    command_text = " ".join(command)
    assert "github-secret" not in command_text
    assert "openrouter-secret" not in command_text
    assert "only active coding agent" in command_text
    assert "--team-name" not in command
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["OPENROUTER_API_KEY"] == "openrouter-secret"
    assert environment["GH_TOKEN"] == "github-secret"
    permissions = json.loads(str(environment["CLINE_COMMAND_PERMISSIONS"]))
    assert "git push --force*" in permissions["deny"]


def test_cline_run_reuses_existing_pull_request_without_process(tmp_path: Path) -> None:
    called = False

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0)

    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=[_pull_request("feat/agent-task")])
            )
        ),
        command_runner=runner,
    )

    result = agent.run(
        task_id="task-0009",
        head_sha=None,
        correlation_id="corr-1",
        branch="feat/agent-task",
    )

    assert result.provider_run_id == "pr:17"
    assert called is False


def test_cline_reconcile_marks_existing_pull_request_complete(tmp_path: Path) -> None:
    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=[_pull_request("feat/agent-task")])
            )
        ),
    )

    result = agent.reconcile("corr-1")

    assert result is not None
    assert result.status == "completed"
    assert result.provider_run_id == "pr:17"


def test_cline_run_fails_if_cli_does_not_create_pull_request(tmp_path: Path) -> None:
    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
        ),
        command_runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )

    with pytest.raises(ProviderResponseError, match="without creating"):
        agent.run(
            task_id="task-0009",
            head_sha=None,
            correlation_id="corr-1",
            branch="feat/agent-task",
        )


def test_cline_run_maps_process_failures_without_leaking_output(tmp_path: Path) -> None:
    def failed_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            2,
            stdout="openrouter-secret",
            stderr="github-secret",
        )

    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
        ),
        command_runner=failed_runner,
    )

    with pytest.raises(ProviderError) as raised:
        agent.run(
            task_id="task-0009",
            head_sha=None,
            correlation_id="corr-1",
            branch="feat/agent-task",
        )
    assert "openrouter-secret" not in str(raised.value)
    assert "github-secret" not in str(raised.value)


def test_cline_run_maps_timeout(tmp_path: Path) -> None:
    def timeout_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("cline", 1)

    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
        ),
        command_runner=timeout_runner,
    )

    with pytest.raises(ProviderTimeoutError):
        agent.run(
            task_id="task-0009",
            head_sha=None,
            correlation_id="corr-1",
            branch="feat/agent-task",
        )


def test_cline_follow_up_task_creates_unassigned_managed_issue(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        payload = json.loads(request.content.decode("utf-8"))
        assert "development-automation:managed:task-0010" in payload["body"]
        assert "assignees" not in payload
        return httpx.Response(
            201,
            json={
                "number": 10,
                "html_url": "https://github.com/EricvanLessen/fictional-engine/issues/10",
            },
        )

    agent = ClineOpenRouterCodingAgent(
        repository="EricvanLessen/fictional-engine",
        github_token="github-secret",
        openrouter_api_key="openrouter-secret",
        repository_root=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = agent.create_or_update_task(
        task_id="task-0010",
        branch="feat/next",
        correlation_id="corr-next",
        title="Next task",
        body="Do the next bounded change.",
        dispatch=False,
    )

    assert result.provider_run_id == "issue:10"
    assert [request.method for request in requests] == ["GET", "POST"]
