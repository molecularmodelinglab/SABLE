import asyncio
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
from urllib.parse import urlparse
from urllib.request import urlopen

from boltz_api import Boltz

from tools.boltz.models import (
    BoltzExecutionKind,
    BoltzJobStatus,
    BoltzMetricSet,
    BoltzMoleculeResult,
    BoltzProvider,
    BoltzRequest,
    BoltzSubmission,
)


class BoltzPlatformError(RuntimeError):
    """A provider failure safe to surface without SDK response details."""


class BoltzPlatformAdapter:
    def __init__(
        self,
        api_key: str,
        client_factory: Callable[..., Any] = Boltz,
        artifact_root: Path | None = None,
        artifact_downloader: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._client_factory = client_factory
        self._artifact_root = artifact_root
        self._artifact_downloader = artifact_downloader or self._download_artifact

    async def submit(self, request: BoltzRequest) -> BoltzSubmission:
        if request.provider != BoltzProvider.PLATFORM:
            raise ValueError("BoltzPlatformAdapter only accepts platform requests")
        if request.execution_kind != BoltzExecutionKind.LIBRARY_SCREEN:
            raise ValueError("Boltz Platform adapter currently supports library screens only")

        entities = [self._protein_entity(protein) for protein in request.proteins]
        molecules = [
            {"id": molecule.id, "smiles": molecule.smiles}
            for molecule in request.molecules
        ]
        target: Dict[str, Any] = {"entities": entities, "type": "no_template"}
        for key in ("bonds", "constraints", "pocket_residues", "reference_ligands"):
            if key in request.options:
                target[key] = request.options[key]

        kwargs: Dict[str, Any] = {"molecules": molecules, "target": target}
        for key in ("molecule_filters", "workspace_id", "idempotency_key"):
            if key in request.options:
                kwargs[key] = request.options[key]

        response = await self._call("start", **kwargs)
        return BoltzSubmission(
            provider=BoltzProvider.PLATFORM,
            execution_kind=BoltzExecutionKind.LIBRARY_SCREEN,
            provider_job_id=response.id,
            protein_scope_id=request.protein_scope_id,
            status=self._status(response.status),
            metadata={
                "molecule_ids_by_smiles": {
                    molecule.smiles: molecule.id for molecule in request.molecules
                },
            },
        )

    async def poll(self, submission: BoltzSubmission) -> BoltzJobStatus:
        self._validate_submission(submission)
        response = await self._call("retrieve", submission.provider_job_id)
        return self._status(response.status)

    async def collect_results(
        self,
        submission: BoltzSubmission,
    ) -> List[BoltzMoleculeResult]:
        self._validate_submission(submission)
        provider_results = await self._list_results(submission.provider_job_id)
        molecule_ids_by_smiles = submission.metadata.get("molecule_ids_by_smiles", {})

        results: List[BoltzMoleculeResult] = []
        for result in provider_results:
            structure_path = await self._materialize_structure(result)
            results.append(BoltzMoleculeResult(
                molecule_id=result.external_id
                or molecule_ids_by_smiles.get(result.smiles, result.id),
                protein_scope_id=submission.protein_scope_id,
                provider_result_id=result.id,
                status=BoltzJobStatus.SUCCEEDED,
                metrics=BoltzMetricSet(
                    binding_confidence=result.metrics.binding_confidence,
                    optimization_score=result.metrics.optimization_score,
                    structure_confidence=result.metrics.structure_confidence,
                    ptm=result.metrics.ptm,
                    iptm=result.metrics.iptm,
                    complex_plddt=result.metrics.complex_plddt,
                    complex_iplddt=result.metrics.complex_iplddt,
                ),
                structure_path=structure_path,
                warnings=[warning.message for warning in (result.warnings or [])],
            ))
        return results

    async def cancel(self, submission: BoltzSubmission) -> None:
        self._validate_submission(submission)
        await self._call("stop", submission.provider_job_id)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        def invoke() -> Any:
            try:
                with self._client_factory(api_key=self._api_key) as client:
                    resource = client.small_molecule.library_screen
                    return getattr(resource, method)(*args, **kwargs)
            except Exception as exc:
                raise BoltzPlatformError(
                    f"Boltz Platform {method} request failed"
                ) from exc

        return await asyncio.to_thread(invoke)

    async def _list_results(self, provider_job_id: str) -> List[Any]:
        def invoke() -> List[Any]:
            try:
                with self._client_factory(api_key=self._api_key) as client:
                    page = client.small_molecule.library_screen.list_results(
                        provider_job_id,
                        limit=100,
                    )
                    return list(self._all_page_items(page))
            except Exception as exc:
                raise BoltzPlatformError(
                    "Boltz Platform list_results request failed"
                ) from exc

        return await asyncio.to_thread(invoke)

    async def _materialize_structure(self, result: Any) -> str | None:
        if self._artifact_root is None:
            return None
        structure = getattr(getattr(result, "artifacts", None), "structure", None)
        url = getattr(structure, "url", None)
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise BoltzPlatformError("Boltz Platform returned an invalid artifact URL")

        safe_result_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(result.id))
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".cif", ".pdb"}:
            suffix = ".cif"
        destination = self._artifact_root / f"{safe_result_id}{suffix}"
        try:
            await asyncio.to_thread(self._artifact_downloader, url, destination)
        except Exception as exc:
            raise BoltzPlatformError("Boltz Platform artifact download failed") from exc
        return str(destination)

    @staticmethod
    def _download_artifact(url: str, destination: Path) -> None:
        if destination.is_file():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(f"{destination.suffix}.part")
        try:
            with urlopen(url, timeout=120) as response, temporary_path.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _protein_entity(protein: Dict[str, Any]) -> Dict[str, Any]:
        sequence = "".join(str(protein.get("sequence") or "").split()).upper()
        if not sequence:
            raise ValueError("Boltz Platform library screens require protein sequences")
        chain_ids = protein.get("chain_ids", protein.get("chain_id", "A"))
        if isinstance(chain_ids, str):
            chain_ids = [chain_ids]
        entity: Dict[str, Any] = {
            "type": "protein",
            "chain_ids": list(chain_ids),
            "value": sequence,
        }
        for key in ("cyclic", "modifications"):
            if key in protein:
                entity[key] = protein[key]
        return entity

    @staticmethod
    def _status(status: str) -> BoltzJobStatus:
        try:
            return BoltzJobStatus(status)
        except ValueError as exc:
            raise BoltzPlatformError("Boltz Platform returned an unknown job status") from exc

    @staticmethod
    def _all_page_items(page: Any) -> Iterable[Any]:
        while True:
            yield from page.data
            if not page.has_next_page():
                break
            page = page.get_next_page()

    @staticmethod
    def _validate_submission(submission: BoltzSubmission) -> None:
        if (
            submission.provider != BoltzProvider.PLATFORM
            or submission.execution_kind != BoltzExecutionKind.LIBRARY_SCREEN
        ):
            raise ValueError("Submission does not belong to a Platform library screen")