from infrastructure.storage.lifecycle import LifecycleStore


def test_worker_lease_is_exclusive_until_expiry(tmp_path):
    lifecycle = LifecycleStore(tmp_path)

    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    assert not lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    assert lifecycle.heartbeat_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )


def test_stale_worker_cannot_renew_or_release_replaced_lease(tmp_path):
    lifecycle = LifecycleStore(tmp_path)

    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=5,
    )
    # Simulate lease expiry without sleeping by moving the persisted deadline.
    database = lifecycle._database("run-1")
    database.update(
        "", "lifecycle",
        lambda value: {**value, "worker_lease_expires_at": "2000-01-01T00:00:00+00:00"},
    )
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )

    assert not lifecycle.heartbeat_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    assert not lifecycle.release_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a",
    )
    state = lifecycle.read_run("run-1")
    assert state["worker_lease_owner"] == "worker-b"
    assert state["worker_lease_token"] == "token-b"


def test_current_worker_can_release_lease(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )

    assert lifecycle.release_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a",
    )
    state = lifecycle.read_run("run-1")
    assert state["worker_active"] is False
    assert state["worker_lease_token"] == ""
    assert state["worker_lease_expires_at"] is None

def test_concurrent_workers_cannot_claim_same_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    lifecycle = LifecycleStore(tmp_path)
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait()
        return lifecycle.claim_worker_lease(
            "run-race", owner_id=owner, lease_token=f"token-{owner}", ttl_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("a", "b")))

    assert sorted(results) == [False, True]
