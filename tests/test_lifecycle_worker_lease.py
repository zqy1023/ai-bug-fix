from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from infrastructure.storage.lifecycle import LifecycleStore


def test_worker_lease_is_exclusive_across_concurrent_claims(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    barrier = Barrier(2)

    def claim(owner: str) -> bool:
        barrier.wait()
        return lifecycle.claim_worker_lease(
            "run-1", owner_id=owner, lease_token=f"token-{owner}", ttl_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))

    assert sorted(results) == [False, True]


def test_worker_lease_can_be_claimed_after_expiry(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    lifecycle._database("run-1").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
    )

    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    state = lifecycle.read_run("run-1")
    assert state["worker_lease_owner"] == "worker-b"
    assert state["worker_lease_token"] == "token-b"


def test_stale_worker_cannot_renew_or_release_replacement_lease(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    lifecycle._database("run-1").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
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


def test_current_worker_can_heartbeat_and_release(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )

    assert lifecycle.heartbeat_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    assert lifecycle.release_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a",
    )
    state = lifecycle.read_run("run-1")
    assert state["worker_active"] is False
    assert state["worker_lease_token"] == ""
    assert state["worker_lease_expires_at"] is None


def test_expired_worker_cannot_renew_lease(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    lifecycle._database("run-1").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
    )

    assert not lifecycle.heartbeat_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )


def test_graph_node_is_fenced_after_lease_replacement(tmp_path):
    import pytest

    from infrastructure.storage.lifecycle import WorkerLeaseLost
    from infrastructure.storage.store import RecordStore
    from workflow.engine.graph import _guard_worker_lease
    from workflow.engine.runner import WorkflowServices

    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    services = WorkflowServices(metadata={
        "worker_lease_owner": "worker-a",
        "worker_lease_token": "token-a",
    })
    called = []
    guarded = _guard_worker_lease(
        "test_node",
        lambda state: called.append(True) or {"ok": True},
        RecordStore(tmp_path),
        services,
    )

    lifecycle._database("run-1").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    with pytest.raises(WorkerLeaseLost):
        guarded({"run_id": "run-1"})
    assert called == []


def test_graph_node_is_fenced_if_lease_expires_during_execution(tmp_path):
    import pytest

    from infrastructure.storage.lifecycle import WorkerLeaseLost
    from infrastructure.storage.store import RecordStore
    from workflow.engine.graph import _guard_worker_lease
    from workflow.engine.runner import WorkflowServices

    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-1", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    services = WorkflowServices(metadata={
        "worker_lease_owner": "worker-a",
        "worker_lease_token": "token-a",
    })

    def expire_during_node(state):
        lifecycle._database("run-1").update(
            "", "lifecycle",
            lambda value: {
                **value,
                "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
            },
        )
        return {"ok": True}

    guarded = _guard_worker_lease(
        "test_node", expire_during_node, RecordStore(tmp_path), services,
    )
    with pytest.raises(WorkerLeaseLost):
        guarded({"run_id": "run-1"})


def test_recent_legacy_worker_heartbeat_blocks_rollout_claim(tmp_path):
    from datetime import datetime, timezone

    lifecycle = LifecycleStore(tmp_path)
    lifecycle._database("run-1").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_active": True,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert not lifecycle.claim_worker_lease(
        "run-1", owner_id="new-worker", lease_token="new-token", ttl_seconds=30,
    )


def test_write_guard_blocks_lease_takeover_until_persistent_write_finishes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import pytest

    from infrastructure.storage.lifecycle import WorkerLeaseLost

    lifecycle = LifecycleStore(tmp_path)
    database = lifecycle._database("run-guard")
    assert lifecycle.claim_worker_lease(
        "run-guard", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    entered = Event()
    release = Event()

    def protected_write():
        with database.worker_lease_guard(owner_id="worker-a", lease_token="token-a"):
            entered.set()
            assert release.wait(timeout=5)

    def replacement_claim():
        return lifecycle.claim_worker_lease(
            "run-guard", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(protected_write)
        assert entered.wait(timeout=5)
        takeover = pool.submit(replacement_claim)
        assert not takeover.done()
        release.set()
        writer.result(timeout=5)
        assert takeover.result(timeout=5) is False

    # Once the original lease expires and is replaced, stale writes are rejected.
    database.update(
        "", "lifecycle",
        lambda state: {**state, "worker_lease_expires_at": "2000-01-01T00:00:00+00:00"},
    )
    assert lifecycle.claim_worker_lease(
        "run-guard", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    with pytest.raises(WorkerLeaseLost):
        with database.worker_lease_guard(owner_id="worker-a", lease_token="token-a"):
            pass


def test_lifecycle_updates_are_fenced_when_store_has_worker_identity(tmp_path):
    import pytest

    from infrastructure.storage.lifecycle import WorkerLeaseLost

    lifecycle = LifecycleStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-fenced", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    fenced = LifecycleStore(tmp_path, worker_lease=("worker-a", "token-a"))
    fenced.update_run("run-fenced", status="running", phase="worker-a")

    lifecycle._database("run-fenced").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert lifecycle.claim_worker_lease(
        "run-fenced", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    with pytest.raises(WorkerLeaseLost):
        fenced.update_run("run-fenced", status="failed", phase="stale-worker")
    assert lifecycle.read_run("run-fenced")["phase"] == "worker-a"


def test_append_artifact_rejects_stale_worker_after_lease_handoff(tmp_path):
    import pytest

    from infrastructure.storage.lifecycle import WorkerLeaseLost
    from infrastructure.storage.store import RecordStore

    lifecycle = LifecycleStore(tmp_path)
    store = RecordStore(tmp_path)
    assert lifecycle.claim_worker_lease(
        "run-artifact", owner_id="worker-a", lease_token="token-a", ttl_seconds=30,
    )
    store.set_worker_lease("worker-a", "token-a")
    store.append_artifact(
        "run-artifact", "metrics/agent.jsonl", {"call": 1}, "task-1",
    )
    assert len(store.read_metrics("run-artifact", "task-1")) == 1

    lifecycle._database("run-artifact").update(
        "", "lifecycle",
        lambda state: {
            **state,
            "worker_lease_expires_at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert lifecycle.claim_worker_lease(
        "run-artifact", owner_id="worker-b", lease_token="token-b", ttl_seconds=30,
    )
    with pytest.raises(WorkerLeaseLost):
        store.append_artifact(
            "run-artifact", "metrics/agent.jsonl", {"call": "stale"}, "task-1",
        )
    assert len(store.read_metrics("run-artifact", "task-1")) == 1
