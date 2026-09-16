from __future__ import annotations

import pytest

from development_automation.ci_gate import CIGateError, validate_ci_for_exact_head
from development_automation.dispatcher_models import CheckRunEvidence


def _check(
    name: str,
    head_sha: str,
    status: str = "completed",
    conclusion: str = "success",
) -> CheckRunEvidence:
    return CheckRunEvidence(
        name=name,
        status=status,
        conclusion=conclusion,
        head_sha=head_sha,
        workflow_ref="trusted/workflow.yml@refs/heads/main",
    )


def test_ci_gate_rejects_zero_checks() -> None:
    with pytest.raises(CIGateError):
        validate_ci_for_exact_head(
            expected_head_sha="a" * 40,
            checks=(),
            required_check_names=("ruff",),
            trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        )


def test_ci_gate_rejects_missing_required_check() -> None:
    with pytest.raises(CIGateError):
        validate_ci_for_exact_head(
            expected_head_sha="a" * 40,
            checks=(_check("ruff", "a" * 40),),
            required_check_names=("ruff", "pytest"),
            trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        )


def test_ci_gate_rejects_non_success_conclusion() -> None:
    with pytest.raises(CIGateError):
        validate_ci_for_exact_head(
            expected_head_sha="a" * 40,
            checks=(
                _check("ruff", "a" * 40),
                _check("pytest", "a" * 40, conclusion="failure"),
            ),
            required_check_names=("ruff", "pytest"),
            trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        )


def test_ci_gate_rejects_stale_head() -> None:
    with pytest.raises(CIGateError):
        validate_ci_for_exact_head(
            expected_head_sha="a" * 40,
            checks=(
                _check("ruff", "a" * 40),
                _check("pytest", "b" * 40),
            ),
            required_check_names=("ruff", "pytest"),
            trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        )


def test_ci_gate_rejects_untrusted_workflow() -> None:
    with pytest.raises(CIGateError):
        validate_ci_for_exact_head(
            expected_head_sha="a" * 40,
            checks=(
                _check("ruff", "a" * 40),
                CheckRunEvidence(
                    name="pytest",
                    status="completed",
                    conclusion="success",
                    head_sha="a" * 40,
                    workflow_ref="untrusted.yml@refs/heads/main",
                ),
            ),
            required_check_names=("ruff", "pytest"),
            trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        )


def test_ci_gate_accepts_expected_head_and_required_checks() -> None:
    validate_ci_for_exact_head(
        expected_head_sha="a" * 40,
        checks=(
            _check("ruff", "a" * 40),
            _check("mypy", "a" * 40),
            _check("pytest", "a" * 40),
        ),
        required_check_names=("ruff", "mypy", "pytest"),
        trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
    )