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
