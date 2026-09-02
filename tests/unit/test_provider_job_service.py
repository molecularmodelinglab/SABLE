from types import SimpleNamespace

from tools.boltz.models import (
    BoltzExecutionKind,
    BoltzJobStatus,
    BoltzMolecule,
    BoltzProvider,
    BoltzRequest,
)
from server.services.provider_job_service import ProviderJobService


def _request(api_key: str, workspace_id: str = "workspace-1") -> BoltzRequest:
    return BoltzRequest(
        provider=BoltzProvider.PLATFORM,
        execution_kind=BoltzExecutionKind.LIBRARY_SCREEN,
        molecules=[BoltzMolecule(id="mol-1", smiles="CCO")],
        proteins=[{"chain_id": "A", "sequence": "AAAA"}],
        protein_scope_id="scope-1",
        options={"api_key": api_key, "workspace_id": workspace_id},
    )


def test_request_fingerprint_excludes_api_key():
    service = ProviderJobService()

    assert service.request_fingerprint(_request("secret-one")) == service.request_fingerprint(
        _request("secret-two")
    )
    assert service.request_fingerprint(_request("secret", "workspace-1")) != service.request_fingerprint(
        _request("secret", "workspace-2")
    )


def test_load_results_reconstructs_provider_models():
    job = SimpleNamespace(
        protein_scope_id="scope-1",
        results=[SimpleNamespace(
            molecule_id="mol-1",
            provider_result_id="result-1",
            status="succeeded",
            metrics={"optimization_score": 0.84},
            artifact_path=None,
            warnings=["warning"],
            error_message=None,
        )],
    )

    results = ProviderJobService.load_results(job)

    assert results[0].status == BoltzJobStatus.SUCCEEDED
    assert results[0].metrics.optimization_score == 0.84
    assert results[0].protein_scope_id == "scope-1"