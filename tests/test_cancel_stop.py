from langgraph.graph import END

from workflow.engine.graph import RUN_TASK, after_dispatch, collect


def test_cancelled_task_stops_dispatch_instead_of_fanning_out_cancelled_rows():
    state = {
        "tasks": [{"task_id": "a"}, {"task_id": "b"}],
        "task_cursor": 0,
        "task": {"task_id": "a"},
        "terminal_status": "CANCELLED",
    }

    patch = collect(state)

    assert patch["task_cursor"] == 1
    assert patch["stop_requested"] is True
    assert after_dispatch({**state, **patch}) == END


def test_normal_task_keeps_dispatching():
    state = {
        "tasks": [{"task_id": "a"}, {"task_id": "b"}],
        "task_cursor": 0,
        "task": {"task_id": "a"},
        "terminal_status": "SUCCESS",
    }

    patch = collect(state)

    assert "stop_requested" not in patch
    assert after_dispatch({**state, **patch}) == RUN_TASK
