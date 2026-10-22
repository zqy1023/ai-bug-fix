"""小时级滚动窗口与全量扫描契约，不连接 ES。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.errors import StepFailed
from app.infrastructure.es.logs import ESLogAdapter


@pytest.mark.parametrize(
    "hours, days, expected",
    [(None, None, 19), ("", "2", 48), ("6", "invalid", 6), ("  ", "", 19)],
)
def test_log_window_config_priority(
    hours: str | None,
    days: str | None,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小时覆盖天数；未配置时使用集中默认值，不暗藏固定窗口。"""
    from app.infrastructure import config

    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(config, "settings", replace(config.settings, default_log_hours=19))
    for name, value in (("LOG_PULL_HOURS", hours), ("LOG_PULL_DAYS", days)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert config.load_config().log_hours == expected


def adapter_for(count: int, captured: dict[str, Any]) -> ESLogAdapter:
    """生成可观测、可关闭的模拟扫描器。"""
    def scanner(**kwargs: Any) -> Any:
        captured.update(kwargs)
        try:
            for _ in range(count):
                yield {}
        finally:
            captured["closed"] = True

    def indices(prefix: str, days: int) -> list[str]:
        captured["index_days"] = days
        return [f"{prefix}test"]

    return ESLogAdapter(None, scanner, indices, 8)


def fetch(adapter: ESLogAdapter, **kwargs: Any) -> Any:
    """调用实际扫描入口。"""
    return adapter.fetch(service_name="test", service_prefix="test", excluded_messages=[], excluded_biztypes=[], hours=kwargs.pop("hours", 24), limit=kwargs.pop("limit", 0), **kwargs)


def test_zero_limit_processes_all_439738_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    adapter = adapter_for(439_738, captured)
    monkeypatch.setattr(adapter, "parse_hit", lambda _: {"carrier": "Carrier"})
    stats: dict[str, int] = {}
    updates: list[dict[str, int]] = []
    assert sum(1 for _ in fetch(adapter, stats=stats, progress=updates.append)) == 439_738
    assert stats["es_hits"] == 439_738
    assert stats["limit_reached"] == 0 and stats["record_limit"] == 0
    assert "scan_limit" not in stats
    assert captured["closed"] is True
    assert updates[0]["es_hits"] == 0 and updates[-1]["es_hits"] == 439_738


@pytest.mark.parametrize("hours, index_days", [(1, 2), (6, 2), (24, 2), (25, 3), (720, 31)])
def test_hour_window_is_exact_and_covers_midnight(hours: int, index_days: int, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infrastructure.es.logs as logs

    now = datetime(2026, 10, 21, 0, 30, tzinfo=timezone.utc)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now

    monkeypatch.setattr(logs, "datetime", FixedClock)
    captured: dict[str, Any] = {}
    assert list(fetch(adapter_for(0, captured), hours=hours)) == []
    window = captured["query"]["query"]["bool"]["filter"][2]["range"]["@timestamp"]
    assert datetime.fromisoformat(window["lt"]) == now
    assert datetime.fromisoformat(window["gte"]) == now - timedelta(hours=hours)
    assert captured["index_days"] == index_days


def test_explicit_limit_above_ten_thousand_has_no_hidden_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    adapter = adapter_for(20_000, captured)
    monkeypatch.setattr(adapter, "parse_hit", lambda _: {"carrier": "Carrier"})
    assert sum(1 for _ in fetch(adapter, limit=15_000)) == 15_000
    assert captured["closed"] is True


def test_cancellation_also_checks_unparseable_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infrastructure.es.logs as logs

    captured: dict[str, Any] = {}
    adapter = adapter_for(100, captured)
    monkeypatch.setattr(adapter, "parse_hit", lambda _: None)
    clock = iter(range(10_000))
    monkeypatch.setattr(logs.time, "monotonic", lambda: next(clock))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(StepFailed) as raised:
        list(fetch(adapter, cancel_requested=cancelled))
    assert raised.value.result == "manual" and captured["closed"] is True


def test_run_creation_records_hours_and_zero_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.entrypoint as entrypoint
    from app.infrastructure.config import load_config
    import workbench.api as api
    from workbench.api import V1StartRunRequest

    config = replace(load_config(), runtime_dir=tmp_path, source_repo=tmp_path, limit=77)
    monkeypatch.setattr(entrypoint, "load_config", lambda: config)
    monkeypatch.setattr(api, "_config", lambda: config)
    _, resolved, store = entrypoint.prepare_run("hours-test", overrides={"log_hours": 6, "log_limit": 0}, queued=True)
    assert resolved.log_hours == 6 and resolved.limit == 0
    assert store.load_run("hours-test")["log_hours"] == 6
    assert "log_days" not in store.load_run("hours-test")
    assert V1StartRunRequest(log_hours=6).log_limit == 77
    assert V1StartRunRequest(log_hours=6, log_limit=0).log_limit == 0
    assert V1StartRunRequest(log_hours=24, log_limit=439_738).log_limit == 439_738
    with pytest.raises(ValidationError):
        V1StartRunRequest(log_days=1)


def test_old_discovery_record_can_be_read_as_hours() -> None:
    from app.workflow.steps.discover_logs.models import DiscoverInput

    query = DiscoverInput.model_validate({"service_name": "test", "service_prefix": "test", "days": 2, "limit": 0})
    assert query.hours == 48
    assert "days" not in query.model_dump()
