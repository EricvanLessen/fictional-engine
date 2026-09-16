from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MockAgentResult:
    correlation_id: str
    status: str
    provider_run_id: str


class MockCodingAgent:
    def __init__(self) -> None:
        self.dispatch_calls: list[tuple[str | None, str | None, str, str | None]] = []
        self.reconcile_results: dict[str, MockAgentResult] = {}
        self.results_by_correlation_id: dict[str, MockAgentResult] = {}
        self.next_status: str = "completed"

    def run(
        self,
        *,
        task_id: str | None,
        head_sha: str | None,
        correlation_id: str,
        branch: str | None,
    ) -> MockAgentResult:
        existing = self.results_by_correlation_id.get(correlation_id)
        if existing is not None:
            return existing

        self.dispatch_calls.append((task_id, head_sha, correlation_id, branch))
        result = MockAgentResult(
            correlation_id=correlation_id,
            status=self.next_status,
            provider_run_id=f"mock-run-{len(self.dispatch_calls)}",
        )
        self.results_by_correlation_id[correlation_id] = result
        return result

    def reconcile(self, correlation_id: str) -> MockAgentResult | None:
        return self.reconcile_results.get(correlation_id)