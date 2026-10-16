from __future__ import annotations

from typing import Any

from app.infrastructure.es.logs import ESLogAdapter


def test_message_without_explicit_error_preserves_complete_message() -> None:
    message = (
        '2026-10-16 04:25:55,554 [0] ERROR gather.py.track - 时间格式错误:未识别的时间格式, '
        'model:{"datetime":null,"format":null,"raw":"15 10 202600:01 - 23:59"}, '
        'key:shipment_facts.estimate_delivery_info.from_date, carrier:Almex, '
        'req:{"number":"07083416520","param":"","source":"gather-www",'
        '"gather_action":"TRACK_SUBSCRIBE","gather_channel":"Default"}'
    )

    parsed = ESLogAdapter.parse_message(message)

    assert parsed is not None
    assert parsed["carrier"] == "Almex"
    assert parsed["error"] == message
    assert parsed["req"] == (
        '{"number":"07083416520","param":"","source":"gather-www",'
        '"gather_action":"TRACK_SUBSCRIBE","gather_channel":"Default"}'
    )


def test_message_with_explicit_error_field_extracts_error_value() -> None:
    message = (
        '2026-10-16 04:25:55,554 [0] ERROR gather.py.track - tracking failed, '
        'carrier:Almex, req:{"number":"123"}, error: upstream unavailable'
    )

    parsed = ESLogAdapter.parse_message(message)

    assert parsed is not None
    assert parsed["error"] == "upstream unavailable"


def test_fetch_excludes_redis_connection_errors_at_es_query_time() -> None:
    captured: dict[str, Any] = {}

    def scanner(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    adapter = ESLogAdapter(
        client=object(),
        scanner=scanner,
        index_provider=lambda prefix, days: [f"{prefix}2026.10.16"],
        tz_offset=8,
    )

    list(
        adapter.fetch(
            service_name="gather.py.track",
            service_prefix="gather",
            excluded_messages=[],
            excluded_biztypes=[],
            days=1,
            limit=10,
        )
    )

    must_not = captured["query"]["query"]["bool"]["must_not"]
    assert {"match_phrase": {"message": "redis.exceptions.ConnectionError"}} in must_not
