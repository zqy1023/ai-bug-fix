import zipfile

from infrastructure.workspace.workspace import WorkspaceManager


def test_shared_snapshot_keeps_runtime_rule_archive(tmp_path):
    loader = tmp_path / "framework/result_format/formatters/status_formatter/loaders"
    loader.mkdir(parents=True)
    archive = loader / "py_rules_new.zip"
    with zipfile.ZipFile(archive, "w") as rules:
        rules.writestr("TEST/En.csv", "sub_status,rule_type\nDelivered,text\n")
    original = archive.read_bytes()
    (loader / "credentials.json").write_text("{}", encoding="utf-8")
    (loader / "loader.pyc").write_bytes(b"bytecode")

    WorkspaceManager._sanitize_shared_tree(tmp_path)

    assert archive.read_bytes() == original
    with zipfile.ZipFile(archive) as rules:
        assert rules.testzip() is None
    assert not (loader / "credentials.json").exists()
    assert not (loader / "loader.pyc").exists()


def test_shared_snapshot_keeps_provider_code_mapping_without_credentials(tmp_path):
    folder = tmp_path / "framework/result_format/formatters/provider_formatter"
    folder.mkdir(parents=True)
    mapping = folder / "local_provider_mapping.csv"
    mapping.write_text("Carrier,LocalProvider,LocalProviderKey\nTEST,Post,100\n", encoding="utf-8")
    credential = folder / "provider_credentials.csv"
    credential.write_text("token\nprivate\n", encoding="utf-8")
    WorkspaceManager._sanitize_shared_tree(tmp_path)
    assert mapping.read_text(encoding="utf-8").endswith("TEST,Post,100\n")
    assert not credential.exists()


def test_shared_publish_retries_transient_windows_directory_lock(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.rename
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'data.csv').write_text('real data', encoding='utf-8')
    calls = []

    def locked_once(path, destination):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError('temporary directory lock')
        return original(path, destination)

    monkeypatch.setattr(Path, 'rename', locked_once)
    WorkspaceManager._publish_directory(source, target)
    assert (target / 'data.csv').read_text(encoding='utf-8') == 'real data'
    assert len(calls) == 2
