from server.storage import LocalStorageBackend, StorageConfig


def test_platform_structures_are_listed_and_resolved(tmp_path):
    backend = LocalStorageBackend(StorageConfig(backend="local", data_root=tmp_path))
    artifact = tmp_path / "runs" / "run-1" / "artifacts" / "boltz_platform" / "pres_1.cif"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("data_test\n", encoding="utf-8")

    assert backend.list_checkpoints("run-1") == ["pres_1.cif"]
    assert backend.checkpoint_path("run-1", "pres_1.cif") == artifact.resolve()