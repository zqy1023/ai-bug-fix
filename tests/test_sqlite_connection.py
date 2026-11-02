import sqlite3

import pytest

from infrastructure.storage.sqlite import open_sqlite


def test_open_sqlite_applies_explicit_connection_options(tmp_path):
    path = tmp_path / "nested" / "store.sqlite3"
    connection = open_sqlite(
        path,
        timeout=2,
        busy_timeout_ms=2500,
        row_factory=sqlite3.Row,
        journal_mode="WAL",
    )
    try:
        assert path.is_file()
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 2500
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")
        assert connection.execute("SELECT value FROM sample").fetchone()["value"] == "ok"
    finally:
        connection.close()


def test_open_sqlite_rejects_unknown_journal_mode(tmp_path):
    with pytest.raises(ValueError, match="journal_mode"):
        open_sqlite(tmp_path / "store.sqlite3", journal_mode="NOT_A_MODE")
