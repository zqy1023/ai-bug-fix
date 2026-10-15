from __future__ import annotations

import pytest

from app.infrastructure.eligibility import ExecutionEligibility


def test_claim_is_unique_for_carrier_in_one_run(tmp_path) -> None:
    first = ExecutionEligibility(tmp_path)
    assert first.claim("run-1", "track", "DHL", "problem-a") is True
    assert first.claim("run-1", "track", "DHL", "problem-b") is False

    # 资格按 run + biztype + carrier 区分，不按问题指纹或日期区分。
    assert first.claim("run-1", "track", "UPS", "problem-c") is True
    assert first.claim("run-1", "pickup", "DHL", "problem-d") is True


def test_claim_survives_service_recreation(tmp_path) -> None:
    assert ExecutionEligibility(tmp_path).claim("run-2", "track", "DHL", "problem-a")
    reopened = ExecutionEligibility(tmp_path)
    assert reopened.claim("run-2", "track", "DHL", "problem-b") is False


def test_corrupt_eligibility_file_fails_closed(tmp_path) -> None:
    (tmp_path / "eligibility.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="登记文件损坏"):
        ExecutionEligibility(tmp_path)
