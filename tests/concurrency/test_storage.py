"""Actual artifact routes and Azure hydration, using an in-memory blob provider."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

@pytest.mark.parametrize("cached", [True, False], ids=["cached-other-run", "cold-other-run"])
def test_slow_artifact_does_not_block_other_user(
    live_api, two_users, make_run, tmp_path, monkeypatch, metrics, cached
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
        storage.StorageConfig(backend="azure", data_root=tmp_path, azure_container="fake"),
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


def test_same_run_hydration_remains_serialized(tmp_path, metrics):
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
        StorageConfig(backend="azure", data_root=tmp_path, azure_container="fake"),
        container_client=Container(),
    )

    def read():
        reader_started.set()
        return backend.summary_txt_path("same-run").read_text()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert entered.wait(3)
        reader_started.clear()
        second = pool.submit(read)
        try:
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
