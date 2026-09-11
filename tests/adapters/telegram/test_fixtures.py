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


def test_fixture_loader_preserves_original_unknown_payload_fields(
    source_payload_regression_fixture_path: Path,
) -> None:
    envelope = load_fixture_envelopes(source_payload_regression_fixture_path)[0]

    assert envelope.source_payload == {
        "fixture_id": "source-payload-regression-001",
        "channel_id": 777000,
        "message_id": 501,
        "message_date": "2026-09-10T08:00:00+00:00",
        "sender_name": "Trading Busters",
        "channel_title": "Trading Busters Signals",
        "text": "Regression payload preservation sample",
        "media_metadata": {"kind": "document"},
        "telegram_metadata": {"source": "sample-messages", "timezone": "UTC"},
        "unknown_nested_export": {"outer": {"inner": [1, 2, {"keep": True}]}} ,
        "unknown_scalar": "preserve-me",
    }
