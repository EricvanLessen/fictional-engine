from __future__ import annotations

from development_automation.dispatcher_models import CheckRunEvidence
from development_automation.errors import DevelopmentAutomationError


class CIGateError(DevelopmentAutomationError):
    """Raised when CI evidence is insufficient to approve a head SHA."""


def validate_ci_for_exact_head(
    expected_head_sha: str,
    checks: tuple[CheckRunEvidence, ...],
    required_check_names: tuple[str, ...],
    trusted_workflow_refs: tuple[str, ...],
) -> None:
    if not checks:
        raise CIGateError("no CI checks were provided")
    if not required_check_names:
        raise CIGateError("required check names are not configured")

    checks_by_name = {check.name: check for check in checks}
    missing_names = [name for name in required_check_names if name not in checks_by_name]
    if missing_names:
        raise CIGateError(f"missing required CI checks: {', '.join(sorted(missing_names))}")

    for required_name in required_check_names:
        check = checks_by_name[required_name]
        if check.head_sha != expected_head_sha:
            raise CIGateError(
                f"CI check {required_name!r} is for stale head {check.head_sha}, "
                f"expected {expected_head_sha}"
            )
        if trusted_workflow_refs:
            if check.workflow_ref is None or check.workflow_ref not in trusted_workflow_refs:
                raise CIGateError(
                    f"CI check {required_name!r} has untrusted workflow {check.workflow_ref!r}"
                )
        if check.status != "completed":
            raise CIGateError(f"CI check {required_name!r} is not completed")
        if check.conclusion != "success":
            raise CIGateError(
                f"CI check {required_name!r} did not succeed: {check.conclusion!r}"
            )