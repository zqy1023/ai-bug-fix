def test_route_common_reuses_canonical_runtime_and_control_services():
    import server.context as context
    import server.control_store as control_store
    import server.lifecycle as lifecycle
    import server.routes.common as common

    assert common._config is context._config
    assert common._require_current_workflow is context._require_current_workflow
    assert common._ID_PATTERN is context._ID_PATTERN
    assert common._store is context._store
    assert common._run_lock is context._run_lock
    assert common._RUN_LOCKS is context._RUN_LOCKS
    assert common._RUN_LOCKS_GUARD is context._RUN_LOCKS_GUARD
    assert common._control_db is control_store._control_db
    assert common._audit_control is control_store._audit_control
    assert common._start_lifecycle_heartbeat is lifecycle._start_lifecycle_heartbeat
    assert common._record_background_failure is lifecycle._record_background_failure
