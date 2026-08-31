from types import SimpleNamespace

from server.storage import AzureBlobStorageBackend, LocalStorageBackend, StorageConfig


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def readinto(self, stream):
        return stream.write(self.data)


class FakeContainerClient:
    def __init__(self, blobs=None):
        self.blobs = dict(blobs or {})

    def list_blobs(self, name_starts_with):
        return [
            SimpleNamespace(name=name)
            for name in sorted(self.blobs)
            if name.startswith(name_starts_with)
        ]

    def download_blob(self, name):
        return FakeDownload(self.blobs[name])

    def upload_blob(self, name, data, overwrite=False):
        assert overwrite is True
        self.blobs[name] = data.read()

    def delete_blob(self, name):
        del self.blobs[name]


def test_platform_structures_are_listed_and_resolved(tmp_path):
    backend = LocalStorageBackend(StorageConfig(backend="local", data_root=tmp_path))
    artifact = tmp_path / "runs" / "run-1" / "artifacts" / "boltz_platform" / "pres_1.cif"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("data_test\n", encoding="utf-8")

    assert backend.list_checkpoints("run-1") == ["pres_1.cif"]
    assert backend.checkpoint_path("run-1", "pres_1.cif") == artifact.resolve()


def test_azure_backend_hydrates_and_synchronizes_run(tmp_path):
    client = FakeContainerClient({
        "sable-runs/run-1/checkpoints/remote.pkl": b"checkpoint",
        "sable-runs/run-1/results/stale.json": b"stale",
    })
    config = StorageConfig(
        backend="azure",
        data_root=tmp_path,
        azure_container="artifacts",
        azure_prefix="sable-runs/",
    )
    backend = AzureBlobStorageBackend(config, container_client=client)

    base = backend.run_dir("run-1")
    assert (base / "checkpoints" / "remote.pkl").read_bytes() == b"checkpoint"

    (base / "results" / "stale.json").unlink()
    result = base / "results" / "results.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_bytes(b"results")
    backend.sync_run("run-1")

    assert client.blobs["sable-runs/run-1/results/results.json"] == b"results"
    assert "sable-runs/run-1/results/stale.json" not in client.blobs

    backend.delete_run("run-1")
    assert not base.exists()
    assert not client.blobs