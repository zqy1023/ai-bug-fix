from concurrent.futures import ThreadPoolExecutor

from app.infrastructure.eligibility import ExecutionEligibility


def test_claim_is_unique_and_survives_new_instance(tmp_path):
    first = ExecutionEligibility(tmp_path)
    assert first.claim("run-1", "track", "CarrierA", "timeout")
    assert not first.claim("run-1", "track", "CarrierA", "different-error")
    second = ExecutionEligibility(tmp_path)
    assert second.claimed()["run-1|track|CarrierA"] == "timeout"
    assert not second.claim("run-1", "track", "CarrierA", "again")
    first.close()
    second.close()


def test_claims_are_unique_across_instances_and_threads(tmp_path):
    instances = [ExecutionEligibility(tmp_path) for _ in range(8)]
    try:
        def attempt(index):
            return instances[index % len(instances)].claim(
                "run-2", "track", "CarrierB", f"problem-{index}"
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(32)))
        assert sum(results) == 1
    finally:
        for instance in instances:
            instance.close()


def test_legacy_json_is_imported(tmp_path):
    (tmp_path / "eligibility.json").write_text(
        '{"run-3|track|CarrierC":"legacy-problem"}', encoding="utf-8"
    )
    store = ExecutionEligibility(tmp_path)
    try:
        assert store.claimed()["run-3|track|CarrierC"] == "legacy-problem"
        assert not store.claim("run-3", "track", "CarrierC", "new-problem")
    finally:
        store.close()
