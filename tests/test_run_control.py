import sqlite3

from app.infrastructure.run_control import cancellation_requested, pause_requested


def test_pause_and_cancel_flags_are_read_from_control_database(tmp_path):
    database = tmp_path / "workbench-control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE run_control (run_id TEXT PRIMARY KEY, pause_requested INTEGER NOT NULL, "
            "cancel_requested INTEGER NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO run_control VALUES ('run-1', 1, 0, 'now')")

    assert pause_requested(tmp_path, "run-1") is True
    assert cancellation_requested(tmp_path, "run-1") is False
