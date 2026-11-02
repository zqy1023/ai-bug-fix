from infrastructure.storage.reports import archive_retry_reports
from infrastructure.storage.store import RecordStore


def test_retry_archival_clears_run_and_task_report_views(tmp_path):
    store = RecordStore(tmp_path)
    run_dir = store.run_dir("run-1")
    run_reports = run_dir / "reports"
    task_reports = store.task_dir("run-1", "task-1") / "reports"
    run_reports.mkdir(parents=True)
    task_reports.mkdir(parents=True)
    (run_reports / "final.json").write_text('{"result":"FAILED"}', encoding="utf-8")
    (run_reports / "run_failure.json").write_text('{"status":"FAILED"}', encoding="utf-8")
    (task_reports / "final.json").write_text('{"result":"FAILED"}', encoding="utf-8")

    archived = archive_retry_reports(store, "run-1", "task-1")

    assert not (run_reports / "final.json").exists()
    assert not (run_reports / "run_failure.json").exists()
    assert not (task_reports / "final.json").exists()
    assert (run_reports / "final.json.retry_previous").is_file()
    assert (run_reports / "run_failure.json.retry_previous").is_file()
    assert len(archived) == 3
    assert list((task_reports / "history").glob("final-*.json"))


def test_repeated_retry_archival_preserves_previous_backup(tmp_path):
    store = RecordStore(tmp_path)
    reports = store.run_dir("run-1") / "reports"
    reports.mkdir(parents=True)
    (reports / "final.json").write_text('{"attempt":1}', encoding="utf-8")
    archive_retry_reports(store, "run-1")
    (reports / "final.json").write_text('{"attempt":2}', encoding="utf-8")

    archive_retry_reports(store, "run-1")

    assert (reports / "final.json.retry_previous").read_text(encoding="utf-8") == '{"attempt":2}'
    history = list((reports / "history").glob("previous-final-json-*.retry_previous"))
    assert len(history) == 1
    assert history[0].read_text(encoding="utf-8") == '{"attempt":1}'
