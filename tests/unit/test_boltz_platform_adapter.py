from types import SimpleNamespace

import pytest

from tools.boltz.models import (
    BoltzExecutionKind,
    BoltzJobStatus,
    BoltzMolecule,
    BoltzProvider,
    BoltzRequest,
)
from tools.boltz.platform import BoltzPlatformAdapter, BoltzPlatformError


class FakePage:
    def __init__(self, data, next_page=None):
        self.data = data
        self._next_page = next_page

    def has_next_page(self):
        return self._next_page is not None

    def get_next_page(self):
        return self._next_page


class FakeLibraryScreen:
    def __init__(self):
        self.calls = []
        metrics = SimpleNamespace(
            binding_confidence=0.81,
            optimization_score=0.72,
            structure_confidence=0.93,
            ptm=0.61,
            iptm=0.62,
            complex_plddt=0.63,
            complex_iplddt=0.64,
        )
        result = SimpleNamespace(
            id="result-1",
            external_id="mol-1",
            smiles="CCO",
            metrics=metrics,
            artifacts=SimpleNamespace(structure=SimpleNamespace(url="https://example.test/1.cif")),
            warnings=[SimpleNamespace(message="low alignment depth")],
        )
        self.results = FakePage([], FakePage([result]))

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        return SimpleNamespace(id="job-1", status="pending")

    def retrieve(self, job_id):
        self.calls.append(("retrieve", job_id))
        return SimpleNamespace(status="succeeded")

    def list_results(self, job_id, limit):
        self.calls.append(("list_results", job_id, limit))
        return self.results

    def stop(self, job_id):
        self.calls.append(("stop", job_id))


class FakeClient:
    def __init__(self, library_screen):
        self.small_molecule = SimpleNamespace(library_screen=library_screen)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def platform_request():
    return BoltzRequest(
        provider=BoltzProvider.PLATFORM,
        execution_kind=BoltzExecutionKind.LIBRARY_SCREEN,
        molecules=[BoltzMolecule(id="mol-1", smiles="CCO")],
        proteins=[{"sequence": "mk tv", "chain_id": "B"}],
        protein_scope_id="scope-1",
        options={"workspace_id": "workspace-1"},
    )


@pytest.mark.asyncio
async def test_platform_adapter_maps_request_and_lifecycle():
    resource = FakeLibraryScreen()
    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(resource),
    )

    submission = await adapter.submit(platform_request())
    status = await adapter.poll(submission)
    results = await adapter.collect_results(submission)
    await adapter.cancel(submission)

    start_call = resource.calls[0]
    assert start_call[1]["molecules"] == [{"id": "mol-1", "smiles": "CCO"}]
    assert start_call[1]["target"] == {
        "entities": [
            {"type": "protein", "chain_ids": ["B"], "value": "MKTV"},
        ],
        "type": "no_template",
    }
    assert start_call[1]["workspace_id"] == "workspace-1"
    assert submission.provider_job_id == "job-1"
    assert status == BoltzJobStatus.SUCCEEDED
    assert results[0].molecule_id == "mol-1"
    assert results[0].protein_scope_id == "scope-1"
    assert results[0].metrics.optimization_score == 0.72
    assert results[0].metrics.affinity is None
    assert results[0].warnings == ["low alignment depth"]
    assert resource.calls[-1] == ("stop", "job-1")


@pytest.mark.asyncio
async def test_platform_adapter_resolves_uniprot_protein_sequence():
    resource = FakeLibraryScreen()
    fetched_ids = []
    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(resource),
        sequence_fetcher=lambda uniprot_id: fetched_ids.append(uniprot_id) or "mktv",
    )
    request = platform_request().model_copy(
        update={"proteins": [{"uniprot_id": "P21453", "chain_id": "A"}]}
    )

    await adapter.submit(request)

    assert fetched_ids == ["P21453"]
    assert resource.calls[0][1]["target"]["entities"] == [
        {"type": "protein", "chain_ids": ["A"], "value": "MKTV"}
    ]


@pytest.mark.asyncio
async def test_platform_adapter_rejects_missing_protein_identity():
    adapter = BoltzPlatformAdapter("secret", client_factory=lambda **kwargs: None)
    request = platform_request().model_copy(update={"proteins": [{}]})

    with pytest.raises(ValueError, match="protein sequence or UniProt ID"):
        await adapter.submit(request)


@pytest.mark.asyncio
async def test_platform_adapter_sanitizes_sdk_errors():
    class BrokenResource(FakeLibraryScreen):
        def start(self, **kwargs):
            raise RuntimeError("response body containing secret")

    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(BrokenResource()),
    )

    with pytest.raises(BoltzPlatformError) as error:
        await adapter.submit(platform_request())

    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_platform_adapter_surfaces_safe_sdk_error_details():
    class ProviderError(RuntimeError):
        status_code = 422
        body = {"detail": "Target sequence is not supported"}
        response = SimpleNamespace(headers={"x-request-id": "req-123"})

    class BrokenResource(FakeLibraryScreen):
        def start(self, **kwargs):
            raise ProviderError("raw SDK error")

    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(BrokenResource()),
    )

    with pytest.raises(BoltzPlatformError) as error:
        await adapter.submit(platform_request())

    assert str(error.value) == (
        "Boltz Platform start request failed (HTTP 422): "
        "Target sequence is not supported [request ID: req-123]"
    )


@pytest.mark.asyncio
async def test_platform_adapter_identifies_connection_errors():
    class APIConnectionError(RuntimeError):
        pass

    class BrokenResource(FakeLibraryScreen):
        def start(self, **kwargs):
            raise APIConnectionError("raw transport details")

    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(BrokenResource()),
    )

    with pytest.raises(BoltzPlatformError) as error:
        await adapter.submit(platform_request())

    assert str(error.value) == "Boltz Platform start request failed: could not connect to Boltz"


@pytest.mark.asyncio
async def test_platform_adapter_identifies_underlying_dns_errors():
    class APIConnectionError(RuntimeError):
        pass

    class BrokenResource(FakeLibraryScreen):
        def start(self, **kwargs):
            try:
                raise OSError("[Errno 11001] getaddrinfo failed")
            except OSError as cause:
                raise APIConnectionError("raw transport details") from cause

    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(BrokenResource()),
    )

    with pytest.raises(BoltzPlatformError) as error:
        await adapter.submit(platform_request())

    assert str(error.value) == (
        "Boltz Platform start request failed: could not connect to Boltz "
        "(DNS resolution failed)"
    )


@pytest.mark.asyncio
async def test_platform_adapter_materializes_structure_artifact(tmp_path):
    resource = FakeLibraryScreen()

    def download(url, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("structure")

    adapter = BoltzPlatformAdapter(
        "secret",
        client_factory=lambda **kwargs: FakeClient(resource),
        artifact_root=tmp_path,
        artifact_downloader=download,
    )

    submission = await adapter.submit(platform_request())
    results = await adapter.collect_results(submission)

    assert results[0].structure_path == str(tmp_path / "result-1.cif")
    assert (tmp_path / "result-1.cif").read_text() == "structure"