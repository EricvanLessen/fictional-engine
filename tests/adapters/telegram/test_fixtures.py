from __future__ import annotations

from pathlib import Path

from fictional_engine.adapters.telegram.fixtures import load_fixture_envelopes


def test_fixture_loader_sorts_by_occurrence_timestamp_and_message_order(
    replay_fixture_path: Path,
) -> None:
    envelopes = load_fixture_envelopes(replay_fixture_path)

    ordered_pairs = [
        (envelope.raw_message.occurrence_timestamp.isoformat(), envelope.raw_message.message_id)
        for envelope in envelopes
    ]

    assert ordered_pairs == [
        ("2026-09-09T12:10:00+00:00", 101),
        ("2026-09-09T12:10:00+00:00", 101),
        ("2026-09-09T12:37:00+00:00", 102),
        ("2026-09-09T12:38:00+00:00", 102),
        ("2026-09-09T14:13:00+00:00", 103),
        ("2026-09-09T14:14:00+00:00", 104),
    ]


def test_fixture_loader_preserves_original_text_and_metadata(replay_fixture_path: Path) -> None:
    first_envelope = load_fixture_envelopes(replay_fixture_path)[0]

    assert "SELL STOP" in (first_envelope.raw_message.text or "")
    assert first_envelope.raw_message.telegram_metadata["source"] == "sample-messages"
    assert first_envelope.source_payload["channel_title"] == "Trading Busters Signals"
