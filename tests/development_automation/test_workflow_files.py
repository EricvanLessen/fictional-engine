from __future__ import annotations

from pathlib import Path


def test_dispatcher_workflow_sets_event_path_after_runner_start() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "development-automation-dispatcher.yml"
    )

    workflow_text = workflow_path.read_text(encoding="utf-8")

    assert 'EVENT_PATH: ${{ runner.temp }}/development-automation-event.json' not in workflow_text
    assert (
        '- name: Configure dispatcher paths' in workflow_text
        and 'EVENT_PATH=$RUNNER_TEMP/development-automation-event.json' in workflow_text
        and '$GITHUB_ENV' in workflow_text
    )
    assert 'GITHUB_CONTROL_TOKEN: ${{ github.token }}' in workflow_text
    assert "DEVELOPMENT_AUTOMATION_CODING_PROVIDER: cline" in workflow_text
    assert "DEVELOPMENT_AUTOMATION_REVIEW_PROVIDER: openrouter" in workflow_text
    assert "npm install --global cline@3.0.62" in workflow_text
    assert (
        "CLINE_GITHUB_TOKEN: "
        "${{ secrets.CLINE_GITHUB_TOKEN || secrets.COPILOT_AGENT_TOKEN }}"
    ) in workflow_text
    assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in workflow_text
    assert "OPENAI_API_KEY" not in workflow_text
