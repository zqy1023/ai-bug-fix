"""ESLogAdapter 解析与扫描恢复逻辑的离线测试（不依赖真实 ES）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.errors import StepFailed
from app.infrastructure.es.logs import ESLogAdapter


def _message(req: str = '{"carrier":"CT","k":1}', tail: str = "error: boom") -> str:
    return f"svc alarm req:{req} {tail}"


def _hit(hit_id: str, message: str) -> dict[str, Any]:
    return {
        "_id": hit_id,
        "_index": "logstash-gather-2026.10.24",
        "_source": {
            "service_name": "gather.py.track",
            "biztype": "track.biz",
            "message": message,
        },
    }


def test_full_message_survives_normalization_and_deduplication():
    from app.workflow.steps.normalize_error.node import _normalize
    from app.workflow.steps.deduplicate_error.node import write_groups

    message = _message(tail="error: boom\nTraceback: complete original detail")
    parsed = ESLogAdapter.parse_hit(_hit("full-message", message))
    assert parsed["message"] == message
    normalized = _normalize(parsed, 0)
    assert normalized["message"] == message
    grouped = write_groups(iter([normalized]), ())
    assert grouped["records"][0]["problem"]["message"] == message


class _FakeHits:
    """模拟 elasticsearch scan 返回的可关闭迭代器。"""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self._hits = list(hits)
        self.closed = False

    def __iter__(self) -> Any:
        return iter(self._hits)

    def close(self) -> None:
        self.closed = True


def _adapter(
    hits: list[dict[str, Any]],
    captured: dict[str, Any] | None = None,
) -> ESLogAdapter:
    def scanner(**kwargs: Any) -> _FakeHits:
        if captured is not None:
            captured.update(kwargs)
        return _FakeHits(hits)

    return ESLogAdapter(
        client=object(),
        scanner=scanner,
        index_provider=lambda prefix, days: [f"{prefix}d{offset}" for offset in range(days)],
        index_prefix="logstash-gather-",
        scan_size=10,
        scroll="2m",
        excluded_messages=["noise"],
        request_timeout=5,
    )


class TestParseMessage:
    def test_standard_format(self) -> None:
        row = ESLogAdapter.parse_message(_message())
        assert row is not None
        assert row["carrier"] == "CT"
        assert row["req"] == '{"carrier":"CT","k":1}'
        assert row["error"] == "boom"

    def test_track_config_and_response_extracted(self) -> None:
        message = (
            'svc req:{"carrier":"CT"} track_config:{"model":"gpt"} '
            'response:{"ok":1} error: bad gateway'
        )
        row = ESLogAdapter.parse_message(message)
        assert row is not None
        assert row["carrier"] == "CT"
        assert row["track_config"] == '{"model":"gpt"}'
        assert row["response"] == '{"ok":1}'
        assert row["error"] == "bad gateway"

    def test_carrier_from_prefix_when_missing_in_req(self) -> None:
        row = ESLogAdapter.parse_message("alarm carrier:CT req:{\"k\":1} error: x")
        assert row is not None
        assert row["carrier"] == "CT"

    def test_missing_carrier_rejected(self) -> None:
        assert ESLogAdapter.parse_message("alarm req:{\"k\":1} error: x") is None

    def test_missing_req_marker_rejected(self) -> None:
        assert ESLogAdapter.parse_message("alarm no marker error: x") is None

    def test_broken_req_json_rejected(self) -> None:
        assert ESLogAdapter.parse_message("alarm req:{not json} error: x") is None

    def test_missing_error_falls_back_to_full_message(self) -> None:
        message = 'alarm req:{"carrier":"CT"}'
        row = ESLogAdapter.parse_message(message)
        assert row is not None
        assert row["error"] == message.strip()


class TestFetchFunnel:
    def test_parse_and_unparseable_counts(self) -> None:
        hits = [
            _hit("1", _message()),
            _hit("2", "no marker"),
            _hit("3", _message(req='{"carrier":"CU"}')),
        ]
        stats: dict[str, int] = {}
        rows = list(_adapter(hits).fetch(
            service_name="gather.py.track", service_prefix="gather.py",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
            stats=stats,
        ))
        assert len(rows) == 2
        assert stats["es_hits"] == 3
        assert stats["parsed"] == 2
        assert stats["unparseable"] == 1
        assert stats["limit_reached"] == 0

    def test_query_conditions(self) -> None:
        captured: dict[str, Any] = {}
        list(_adapter([], captured).fetch(
            service_name="gather.py.track", service_prefix="gather.py",
            excluded_messages=["biz_noise"], excluded_biztypes=["b1"],
            hours=12, limit=5,
        ))
        filters = captured["query"]["query"]["bool"]["filter"]
        must_not = captured["query"]["query"]["bool"]["must_not"]
        assert {"prefix": {"service.name": "gather.py"}} in filters
        assert {"match_phrase": {"service_name": "gather.py.track"}} in filters
        phrases = [rule["match_phrase"]["message"] for rule in must_not
                   if "message" in rule["match_phrase"]]
        assert "biz_noise" in phrases and "noise" in phrases
        assert {"match_phrase": {"biztype": "b1"}} in must_not
        assert captured["size"] == 10

    def test_limit_stops_scan(self) -> None:
        hits = [_hit(str(i), _message(req=f'{{"carrier":"C{i}"}}')) for i in range(5)]
        stats: dict[str, int] = {}
        rows = list(_adapter(hits).fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=2,
            stats=stats,
        ))
        assert len(rows) == 2
        assert stats["limit_reached"] == 1
        assert stats["parsed"] == 2

    def test_invalid_args_raise_step_failed(self) -> None:
        with pytest.raises(StepFailed):
            list(_adapter([]).fetch(
                service_name="s", service_prefix="p",
                excluded_messages=[], excluded_biztypes=[], hours=0, limit=0,
            ))
        with pytest.raises(StepFailed):
            list(_adapter([]).fetch(
                service_name="s", service_prefix="p",
                excluded_messages=[], excluded_biztypes=[], hours=1, limit=-1,
            ))

    def test_cancel_request_raises(self) -> None:
        adapter = _adapter([_hit("1", _message())])
        with pytest.raises(StepFailed):
            list(adapter.fetch(
                service_name="s", service_prefix="p",
                excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
                cancel_requested=lambda: True,
            ))


class TestResumeState:
    def test_seen_ids_prevent_refetch_on_resume(self) -> None:
        hits = [
            _hit("1", _message(req='{"carrier":"C1"}')),
            _hit("2", _message(req='{"carrier":"C2"}')),
        ]
        scan_state: dict[str, Any] = {}
        adapter = _adapter(hits)
        stream = adapter.fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
            scan_state=scan_state,
        )
        first = next(stream)
        stream.close()  # 模拟暂停：触发 finally 落盘 seen_ids
        assert first["carrier"] == "C1"
        assert scan_state["seen_ids"] == {"logstash-gather-2026.10.24/1": True}

        stats: dict[str, int] = {}
        rows = list(adapter.fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
            stats=stats, scan_state=scan_state,
        ))
        # 恢复后只产出未见过的记录，时间窗口沿用暂停前的值。
        assert [row["carrier"] for row in rows] == ["C2"]
        assert scan_state["start"] == scan_state["end"] or scan_state["start"]
        assert stats["es_hits"] == 2  # 恢复前的 1 + 新命中的 1
        assert stats["parsed"] == 2

    def test_window_kept_across_resume(self) -> None:
        scan_state: dict[str, Any] = {}
        adapter = _adapter([])
        list(adapter.fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
            scan_state=scan_state,
        ))
        saved_start, saved_end = scan_state["start"], scan_state["end"]
        captured: dict[str, Any] = {}
        list(_adapter([], captured).fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=99, limit=0,
            scan_state=scan_state,
        ))
        window = captured["query"]["query"]["bool"]["filter"][-1]["range"]["@timestamp"]
        assert window["gte"] == saved_start
        assert window["lt"] == saved_end

    def test_parse_failures_recorded_in_state(self) -> None:
        scan_state: dict[str, Any] = {}
        list(_adapter([_hit("1", "broken")]).fetch(
            service_name="s", service_prefix="p",
            excluded_messages=[], excluded_biztypes=[], hours=1, limit=0,
            scan_state=scan_state,
        ))
        failures = scan_state["parse_failures"]
        assert failures["counts"]["missing_req_marker"] == 1
        assert len(failures["samples"]) == 1
