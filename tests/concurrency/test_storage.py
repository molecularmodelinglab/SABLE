"""Actual artifact routes and Azure hydration, using an in-memory blob provider."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import multiprocessing
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest


@pytest.fixture
def storage_root():
    # Keep disposable filesystem evidence beside this harness project's reports.
    root = Path("/results/storage") / uuid4().hex
    root.mkdir(parents=True)
    return root

@pytest.mark.parametrize("cached", [True, False], ids=["cached-other-run", "cold-other-run"])
def test_slow_artifact_does_not_block_other_user(
    live_api, two_users, make_run, storage_root, monkeypatch, metrics, cached
):
    from server import storage

    alice, bob = two_users
    first, second = make_run(alice), make_run(bob)
    entered, release = threading.Event(), threading.Event()

    class Container:
        def list_blobs(self, name_starts_with, include):
            return [SimpleNamespace(name=name_starts_with + "results/summary.txt", metadata={})]

        def download_blob(self, name):
            def readinto(handle):
                if first.id in name:
                    entered.set()
                    assert release.wait(5), "Slow download was not released"
                handle.write(("alice" if first.id in name else "bob").encode())
            return SimpleNamespace(readinto=readinto)

    backend = storage.AzureBlobStorageBackend(
        storage.StorageConfig(backend="azure", data_root=storage_root, azure_container="fake"),
        container_client=Container(),
    )
    monkeypatch.setattr(storage.get_storage_service(), "_backend", backend)
    if cached:
        assert backend.summary_txt_path(second.id).read_text() == "bob"

    with httpx.Client(base_url=live_api, timeout=8) as client, ThreadPoolExecutor(2) as pool:
        def fetch(run, user):
            start = time.monotonic()
            response = client.get(f"/runs/{run.id}/artifacts/summary.txt",
                                  headers={"X-Test-User": str(user.id)})
            return response, time.monotonic() - start

        slow = pool.submit(fetch, first, alice)
        assert entered.wait(3)
        fast = pool.submit(fetch, second, bob)
        try:
            fast.result(timeout=1)
            responsive = True
        except TimeoutError:
            responsive = False
        finally:
            release.set()
        slow_response, _ = slow.result(timeout=5)
        response, latency = fast.result(timeout=5)
    metrics(request_latency_seconds=latency, completed_while_download_blocked=responsive,
            other_run_cached=cached, observation_window_seconds=1)
    assert slow_response.status_code == response.status_code == 200
    assert slow_response.text == "alice"
    assert response.text == "bob"
    assert responsive, "Hydrating Alice's run blocked Bob's unrelated artifact"


@pytest.mark.parametrize("separate_backend", [False, True])
def test_same_run_hydration_remains_serialized(storage_root, metrics, separate_backend):
    from server.storage import AzureBlobStorageBackend, StorageConfig

    entered, release, reader_started = threading.Event(), threading.Event(), threading.Event()
    downloads = []

    class Container:
        def list_blobs(self, name_starts_with, include):
            return [SimpleNamespace(name=name_starts_with + "results/summary.txt", metadata={})]

        def download_blob(self, name):
            downloads.append(name)

            def readinto(handle):
                handle.write(b"partial-")
                handle.flush()
                entered.set()
                assert release.wait(5)
                handle.write(b"complete")
            return SimpleNamespace(readinto=readinto)

    backend = AzureBlobStorageBackend(
        StorageConfig(backend="azure", data_root=storage_root, azure_container="fake"),
        container_client=Container(),
    )

    other = AzureBlobStorageBackend(
        StorageConfig(backend="azure", data_root=storage_root / "runs" / "..",
                      azure_container="fake"),
        container_client=Container(),
    ) if separate_backend else backend

    def read(client):
        reader_started.set()
        return client.summary_txt_path("same-run").read_text()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read, backend)
        assert entered.wait(3)
        reader_started.clear()
        second = pool.submit(read, other)
        try:
            assert not (storage_root / "runs" / "same-run" / "results" / "summary.txt").exists()
            assert reader_started.wait(3)
            try:
                premature = second.result(timeout=0.2)
            except TimeoutError:
                premature = None
        finally:
            release.set()
        results = [first.result(timeout=3), second.result(timeout=3)]
    metrics(download_count=len(downloads), premature_artifact_visible=premature is not None)
    assert premature is None, "Same-run reader observed an unfinished download"
    assert results == ["partial-complete", "partial-complete"]
    assert len(downloads) == 1


class MemoryContainer:
    def __init__(self, blobs=None):
        self.blobs = dict(blobs or {})
        self.downloads = []
        self.listings = 0
        self.uploads = []

    def list_blobs(self, name_starts_with, include):
        self.listings += 1
        return [SimpleNamespace(name=name, metadata={})
                for name in sorted(self.blobs) if name.startswith(name_starts_with)]

    def download_blob(self, name):
        self.downloads.append(name)
        return SimpleNamespace(readinto=lambda handle: handle.write(self.blobs[name]))

    def upload_blob(self, name, data, overwrite):
        assert overwrite
        self.blobs[name] = data.read()
        self.uploads.append(name)

    def delete_blob(self, name):
        del self.blobs[name]


def make_backend(root, container):
    from server.storage import AzureBlobStorageBackend, StorageConfig

    return AzureBlobStorageBackend(
        StorageConfig(backend="azure", data_root=root, azure_container="fake"),
        container_client=container,
    )


@pytest.mark.parametrize("failure", ["download", "listing", "publication"])
def test_storage_failed_hydration_retries_without_partial_files(storage_root, failure, monkeypatch):
    from server import storage
    from server.storage_lock import coordination_path

    class FailingContainer(MemoryContainer):
        fail = True

        def list_blobs(self, name_starts_with, include):
            yield from super().list_blobs(name_starts_with, include)
            if self.fail and failure == "listing":
                raise OSError("interrupted listing")

        def download_blob(self, name):
            download = super().download_blob(name)

            def readinto(handle):
                if self.fail and failure == "download":
                    handle.write(b"partial")
                    handle.flush()
                    raise OSError("interrupted download")
                download.readinto(handle)
            return SimpleNamespace(readinto=readinto)

    client = FailingContainer({"runs/retry/results/summary.txt": b"complete"})
    backend = make_backend(storage_root, client)
    base = storage_root / "runs" / "retry"
    target = base / "results" / "summary.txt"
    state = coordination_path(base)
    with monkeypatch.context() as patch:
        if failure == "publication":
            def fail_link(*args):
                raise OSError("interrupted publication")
            patch.setattr(storage.os, "link", fail_link)
        with pytest.raises(OSError, match="interrupted"):
            backend.summary_txt_path("retry")
    assert not (state / "hydrated").exists()
    assert not (state / "staging").exists()
    assert target.exists() == (failure == "listing")
    if target.exists():
        assert target.read_bytes() == b"complete"

    client.fail = False
    assert backend.summary_txt_path("retry").read_bytes() == b"complete"
    assert (state / "hydrated").exists()
    assert not (state / "staging").exists()
    expected = 1 if failure == "listing" else 2
    assert len(client.downloads) == expected
    assert make_backend(storage_root, client).summary_txt_path("retry").read_bytes() == b"complete"
    assert client.listings == 2
    assert len(client.downloads) == expected


@pytest.mark.parametrize("write_during_download", [False, True])
def test_storage_hydration_preserves_worker_writes(storage_root, write_during_download):
    target = storage_root / "runs" / "writing" / "results" / "summary.txt"
    target.parent.mkdir(parents=True)

    class WritingContainer(MemoryContainer):
        def download_blob(self, name):
            download = super().download_blob(name)

            def readinto(handle):
                download.readinto(handle)
                target.write_bytes(b"worker-write")
            return SimpleNamespace(readinto=readinto)

    client = WritingContainer({"runs/writing/results/summary.txt": b"remote"})
    if not write_during_download:
        target.write_bytes(b"worker-write")
    backend = make_backend(storage_root, client)
    assert backend.summary_txt_path("writing").read_bytes() == b"worker-write"
    assert len(client.downloads) == int(write_during_download)
    backend.sync_run("writing")
    assert client.blobs == {"runs/writing/results/summary.txt": b"worker-write"}
    assert client.uploads == ["runs/writing/results/summary.txt"]


def test_storage_empty_missing_run_and_stale_staging_recover(storage_root):
    from server.storage_lock import coordination_path

    client = MemoryContainer()
    backend = make_backend(storage_root, client)
    base = storage_root / "runs" / "initially-missing"
    state = coordination_path(base)
    staging = state / "staging"
    staging.mkdir(parents=True)
    (staging / "download").write_bytes(b"interrupted-old-download")
    (state / "hydrated").write_text("invalid JSON", encoding="utf-8")
    assert backend.run_dir("initially-missing") == base
    assert not base.exists()
    assert not staging.exists()
    assert not (state / "hydrated").exists()

    client.blobs["runs/initially-missing/results/summary.txt"] = b"newly-created"
    assert backend.summary_txt_path("initially-missing").read_bytes() == b"newly-created"
    staging.mkdir()
    (staging / "download").write_bytes(b"stale-after-publication")
    assert make_backend(storage_root, client).summary_txt_path("initially-missing").read_bytes() == b"newly-created"
    assert not staging.exists()
    assert len(client.downloads) == 1


@pytest.mark.parametrize("completed_by_sync", [False, True])
def test_storage_completion_marker_does_not_hide_missing_files(storage_root, completed_by_sync):
    from server.storage_lock import coordination_path

    client = MemoryContainer({
        "runs/evicted/results/summary.txt": b"complete",
        "runs/evicted/results/worker.txt": b"remote",
    })
    backend = make_backend(storage_root, client)
    target = backend.summary_txt_path("evicted")
    other = target.parent / "worker.txt"
    other.write_bytes(b"worker-write")
    if completed_by_sync:
        backend.sync_run("evicted")
    state = coordination_path(target.parent.parent)
    assert (state / "hydrated").exists()
    target.unlink()
    assert make_backend(storage_root, client).summary_txt_path("evicted").read_bytes() == b"complete"
    assert other.read_bytes() == b"worker-write"
    assert len(client.downloads) == 3
    assert (state / "hydrated").exists()
    assert not (state / "staging").exists()


def test_storage_delete_invalidates_other_backend_cache(storage_root):
    from server.storage_lock import coordination_path

    name = "runs/deleted/results/summary.txt"
    client = MemoryContainer({name: b"first"})
    first, second = make_backend(storage_root, client), make_backend(storage_root, client)
    target = first.summary_txt_path("deleted")
    assert second.summary_txt_path("deleted").read_bytes() == b"first"
    state = coordination_path(target.parent.parent)
    second.delete_run("deleted")
    assert not target.exists()
    assert not client.blobs
    assert not (state / "hydrated").exists()
    assert (state / "lock").exists(), "Never unlink a lock inode held by potential waiters"
    client.blobs[name] = b"replacement"
    assert first.summary_txt_path("deleted").read_bytes() == b"replacement"
    assert len(client.downloads) == 2


@pytest.mark.parametrize("operation", ["sync_run", "delete_run"])
def test_storage_mutations_serialize_with_same_run_hydration(storage_root, operation):
    entered, release, mutation_started = threading.Event(), threading.Event(), threading.Event()

    class BlockingContainer(MemoryContainer):
        def download_blob(self, name):
            download = super().download_blob(name)

            def readinto(handle):
                handle.write(b"prefix-")
                handle.flush()
                entered.set()
                assert release.wait(5)
                download.readinto(handle)
            return SimpleNamespace(readinto=readinto)

    client = BlockingContainer({"runs/mutated/results/summary.txt": b"complete"})
    first, second = make_backend(storage_root, client), make_backend(storage_root, client)

    def mutate():
        mutation_started.set()
        getattr(second, operation)("mutated")

    with ThreadPoolExecutor(2) as pool:
        hydration = pool.submit(first.run_dir, "mutated")
        assert entered.wait(3)
        mutation = pool.submit(mutate)
        try:
            assert mutation_started.wait(3)
            with pytest.raises(TimeoutError):
                mutation.result(timeout=0.2)
            assert client.uploads == []
        finally:
            release.set()
        base = hydration.result(timeout=3)
        mutation.result(timeout=3)
    if operation == "sync_run":
        assert client.blobs == {"runs/mutated/results/summary.txt": b"prefix-complete"}
        assert client.uploads == ["runs/mutated/results/summary.txt"]
    else:
        assert client.blobs == {}
        assert not base.exists()


def _process_hydrate(root, run_id, downloads, listings, entered, release, started, connection):
    """Spawn-safe fake blob boundary; no DB, Redis, credentials, or live Azure."""
    class Container:
        def list_blobs(self, name_starts_with, include):
            with listings.get_lock():
                listings.value += 1
            return [SimpleNamespace(name=name_starts_with + "results/summary.txt", metadata={})]

        def download_blob(self, name):
            with downloads.get_lock():
                downloads.value += 1

            def readinto(handle):
                handle.write(b"partial-")
                handle.flush()
                entered.set()
                deadline = time.monotonic() + 10
                while not release.value:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Parent did not release download")
                    time.sleep(0.01)
                handle.write(b"complete")
            return SimpleNamespace(readinto=readinto)

    try:
        backend = make_backend(Path(root), Container())
        started.set()
        result = backend.summary_txt_path(run_id).read_bytes()
        connection.send(("ok", result))
    except BaseException as exc:
        connection.send(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


@pytest.mark.parametrize("terminate_downloader", [False, True], ids=["same-run-dedup", "process-death-retry"])
def test_storage_shared_root_process_coordination(storage_root, metrics, terminate_downloader):
    from server.storage_lock import coordination_path

    context = multiprocessing.get_context("spawn")
    downloads, listings = context.Value("i", 0), context.Value("i", 0)
    entered = context.Event()
    # Killing an Event.wait() caller can corrupt its shared Condition semaphore.
    release = context.RawValue("b", False)
    processes, readers = [], []

    def start(run_id, root, download_counter=downloads, listing_counter=listings,
              download_entered=entered, download_release=release):
        started = context.Event()
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_process_hydrate, args=(
            str(root), run_id, download_counter, listing_counter,
            download_entered, download_release, started, writer,
        ))
        process.start()
        writer.close()
        processes.append(process)
        readers.append(reader)
        assert started.wait(5), "Spawned reader did not start"
        return process, reader

    base = storage_root / "runs" / "shared"
    target = base / "results" / "summary.txt"
    state = coordination_path(base)
    try:
        first, first_result = start("shared", storage_root)
        assert entered.wait(5)
        assert not target.exists(), "Raw filesystem readers must never see partial downloads"
        second, second_result = start("shared", storage_root / "runs" / "..")
        assert not second_result.poll(0.2), "Same-run reader did not wait for publication"
        assert downloads.value == listings.value == 1

        other_release = context.RawValue("b", True)
        _, other_result = start("unrelated", storage_root, context.Value("i", 0),
                                context.Value("i", 0), context.Event(), other_release)
        assert other_result.poll(3), "An unrelated process/run was blocked by hydration"
        assert other_result.recv() == ("ok", b"partial-complete")

        if terminate_downloader:
            first.terminate()
            first.join(timeout=5)
            assert not first.is_alive()
            assert not target.exists()
            assert not (state / "hydrated").exists()
        release.value = True
        if not terminate_downloader:
            assert first_result.poll(5)
            assert first_result.recv() == ("ok", b"partial-complete")
        assert second_result.poll(5)
        assert second_result.recv() == ("ok", b"partial-complete")
        assert target.read_bytes() == b"partial-complete"
        assert downloads.value == listings.value == (2 if terminate_downloader else 1)
        assert not (state / "staging").exists()
        assert (state / "hydrated").exists()
        metrics(downloads=downloads.value, listings=listings.value,
                process_death_recovered=terminate_downloader,
                unrelated_run_completed_while_blocked=True)
    finally:
        release.value = True
        for process in processes:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
        for reader in readers:
            reader.close()
    assert all(not process.is_alive() for process in processes)
    assert all(process.exitcode == 0 for process in processes[1:])
