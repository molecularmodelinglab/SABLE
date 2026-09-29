"""Client for the hosted OpenFE campaign API; no local OpenFE installation required."""

from __future__ import annotations

import json
import math
import os
import re
import time
from copy import deepcopy
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from schemas.tool_schemas import CharacterizationRequest, CharacterizationResult


class OpenFEError(RuntimeError):
    """An OpenFE request or campaign failed."""


class OpenFETimeout(OpenFEError):
    """The wait expired; the remote campaign is not cancelled."""


class OpenFETool:
    """Submit staged ABFE campaigns and retrieve their binding free energies."""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: float = 30,
        poll_interval: float | None = None,
        wait_timeout: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("OPENFE_BASE_URL", "")).rstrip("/")
        self.api_token = api_token if api_token is not None else os.getenv("OPENFE_API_TOKEN", "")
        self.timeout = float(timeout)
        self.poll_interval = float(
            poll_interval if poll_interval is not None else os.getenv("OPENFE_POLL_INTERVAL", "10")
        )
        self.wait_timeout = float(
            wait_timeout if wait_timeout is not None else os.getenv("OPENFE_WAIT_TIMEOUT", "3600")
        )
        if not self.base_url.startswith(("http://", "https://")):
            raise OpenFEError("Set OPENFE_BASE_URL to the hosted OpenFE API URL.")
        for value in (self.timeout, self.poll_interval, self.wait_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("OpenFE timeouts and poll interval must be finite and positive.")
        self.session = session or requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        try:
            response = self.session.request(
                method, f"{self.base_url}{path}", headers=headers,
                timeout=self.timeout, allow_redirects=False, **kwargs,
            )
        except requests.RequestException as error:
            raise OpenFEError(
                f"OpenFE {method} {path} could not complete; check campaign status before retrying."
            ) from error
        if not 200 <= response.status_code < 300:
            raise OpenFEError(f"OpenFE {method} {path} returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError as error:
            raise OpenFEError(f"OpenFE {method} {path} returned invalid JSON.") from error

    @staticmethod
    def _campaign_path(name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("Invalid OpenFE campaign name.")
        return f"/campaigns/{name}"

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def list_campaigns(self) -> list[str]:
        return self._request("GET", "/campaigns")

    def ensure_upload_support(self) -> None:
        schema = self._request("GET", "/openapi.json")
        if "post" not in schema.get("paths", {}).get("/campaigns/upload", {}):
            raise OpenFEError("The OpenFE service must be redeployed with /campaigns/upload before generating Boltz poses.")

    @staticmethod
    def complexes_from_boltz(
        request: CharacterizationRequest, result: CharacterizationResult,
    ) -> dict[str, Any]:
        """Select a single ligand and protein chains from each downloaded Boltz CIF."""
        import gemmi

        complexes = {}
        metadata = result.metadata.get("per_molecule_metadata", {})
        for molecule_id in request.molecule_ids:
            if molecule_id in result.failed_molecules or molecule_id not in result.results:
                raise OpenFEError(f"Boltz pose generation failed for '{molecule_id}'.")
            details = metadata.get(molecule_id, {})
            path = details.get("cif_file") or details.get("cif_path") or details.get("structure_path")
            if not path or not Path(path).is_file():
                raise OpenFEError(f"Boltz did not download a structure for '{molecule_id}'.")
            try:
                structure = gemmi.read_structure(str(path))
                if Path(path).suffix.lower() == ".pdb":
                    structure.setup_entities()
            except Exception as error:
                raise OpenFEError(f"Cannot parse Boltz structure for '{molecule_id}'.") from error
            if len(structure) != 1:
                raise OpenFEError("Automatic OpenFE inputs require exactly one structure model.")
            protein_chains = set()
            ligands = []
            for chain in structure[0]:
                for residue in chain:
                    if residue.entity_type == gemmi.EntityType.Polymer:
                        if not gemmi.find_tabulated_residue(residue.name).is_amino_acid():
                            raise OpenFEError("Automatic OpenFE inputs support protein polymers only.")
                        protein_chains.add(chain.name)
                    elif residue.entity_type == gemmi.EntityType.NonPolymer:
                        ligands.append((chain.name, residue.name))
                    elif residue.entity_type != gemmi.EntityType.Water:
                        raise OpenFEError("Boltz structure has unclassified residues; supply explicit complexes.")
            if not protein_chains or len(ligands) != 1:
                raise OpenFEError(
                    "Automatic OpenFE inputs require protein chains and exactly one ligand residue; "
                    "supply explicit complexes for cofactors or multiple ligands."
                )
            ligand_chain, ligand_resname = ligands[0]
            if ligand_chain in protein_chains:
                raise OpenFEError("Protein and ligand share a chain; supply explicit OpenFE complexes.")
            complexes[molecule_id] = {
                "name": molecule_id,
                "structure": str(path),
                "protein": {"chains": sorted(protein_chains)},
                "ligand": {
                    "smiles": request.search_space[molecule_id],
                    "selector": {"chain": ligand_chain, "resname": ligand_resname},
                },
                "extra_ligand_copies": "drop",
            }
        return complexes

    def characterize(self, request: CharacterizationRequest) -> CharacterizationResult:
        options = request.tool_options
        configured = options.get("complexes", {})
        molecule_ids = request.molecule_ids or list(request.search_space)
        if not molecule_ids:
            raise OpenFEError("OpenFE requires molecule IDs and structure specifications.")
        missing = [molecule_id for molecule_id in molecule_ids if molecule_id not in configured]
        if missing:
            raise OpenFEError(
                f"Missing OpenFE structures for: {', '.join(missing)}. "
                "Provide complexes keyed by SABLE molecule ID with paths accessible to the API host."
            )
        complexes = []
        molecule_by_ligand = {}
        complex_names = set()
        for molecule_id in molecule_ids:
            complex_spec = deepcopy(configured[molecule_id])
            complex_spec.setdefault("name", molecule_id)
            ligand = complex_spec.get("ligand", {})
            smiles = request.search_space.get(molecule_id)
            if not smiles or ligand.get("smiles") != smiles:
                raise OpenFEError(f"OpenFE ligand SMILES must match molecule '{molecule_id}'.")
            ligand_name = ligand.get("name") or complex_spec["name"]
            if ligand_name in molecule_by_ligand or complex_spec["name"] in complex_names:
                raise OpenFEError("OpenFE complex names and ligand names must be unique.")
            molecule_by_ligand[ligand_name] = molecule_id
            complex_names.add(complex_spec["name"])
            complexes.append(complex_spec)
        execution = options.get("execution", {})
        repeats = execution.get("repeats", 3)
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
            raise OpenFEError("OpenFE repeats must be a positive integer.")
        name = options.get("campaign_name") or f"sable_{uuid4().hex}"
        if options.get("resume"):
            if not options.get("campaign_name"):
                raise OpenFEError("Resuming OpenFE requires campaign_name.")
        else:
            campaign = {
                "protocol": "abfe",
                "name": name,
                "complexes": complexes,
                "settings": options.get("settings", {}),
                "execution": execution,
                "neutralize_ligands": False,
            }
            if options.get("upload_inputs", False):
                self.submit_campaign(campaign, upload_inputs=True)
            else:
                self.submit_campaign(campaign)
        predictions = self.wait_for_results(name, {ligand: repeats for ligand in molecule_by_ligand})
        results = {}
        details = {}
        for prediction in predictions:
            molecule_id = molecule_by_ligand[prediction["ligand"]]
            if prediction.get("quality") == "fail" and not options.get("allow_failed_quality", False):
                raise OpenFEError(f"OpenFE quality checks failed for '{molecule_id}' in '{name}'.")
            energy = float(prediction["dg"])
            uncertainty = float(prediction["uncertainty"])
            if not math.isfinite(energy) or not math.isfinite(uncertainty) or uncertainty < 0:
                raise OpenFEError(f"OpenFE returned invalid energy or uncertainty for '{molecule_id}'.")
            results[molecule_id] = {"openfe_binding_free_energy": energy}
            details[molecule_id] = {**prediction, "units": "kcal/mol", "campaign_name": name}
        return CharacterizationResult(
            results=results,
            metadata={"tool_id": "openfe", "campaign_name": name, "openfe": details},
        )

    def create_campaign(self, campaign: dict[str, Any]) -> dict[str, Any]:
        self._campaign_path(campaign.get("name", ""))
        return self._request("POST", "/campaigns", json=campaign)

    def upload_campaign(self, campaign: dict[str, Any]) -> dict[str, Any]:
        """Create a campaign by uploading all referenced local coordinate files."""
        self._campaign_path(campaign.get("name", ""))
        payload = deepcopy(campaign)
        sources: dict[Path, str] = {}
        for spec in payload.get("complexes", []):
            references = [(spec, "structure")]
            references.extend((spec.get(key, {}), "path") for key in ("protein", "ligand"))
            references.extend((cofactor, "path") for cofactor in spec.get("cofactors", []))
            for component, key in references:
                if component.get(key) is None:
                    continue
                source = Path(component[key]).resolve()
                if not source.is_file() or source.stat().st_size == 0:
                    raise OpenFEError(f"OpenFE upload requires a nonempty local file: {source}")
                suffix = source.suffix.lower()
                if suffix not in {".cif", ".mmcif", ".pdb", ".sdf", ".mol"}:
                    raise OpenFEError(f"Unsupported OpenFE coordinate extension: {suffix}")
                if source not in sources:
                    sources[source] = f"input_{len(sources) + 1}{suffix}"
                component[key] = sources[source]
        if not sources or len(sources) > 100:
            raise OpenFEError("OpenFE uploads require between 1 and 100 coordinate files.")
        with ExitStack() as stack:
            files = [
                ("files", (filename, stack.enter_context(source.open("rb")), "application/octet-stream"))
                for source, filename in sources.items()
            ]
            return self._request(
                "POST", "/campaigns/upload", data={"campaign": json.dumps(payload)}, files=files,
            )

    def status(self, name: str) -> dict[str, Any]:
        return self._request("GET", self._campaign_path(name))

    def start_operation(self, name: str, operation: str) -> dict[str, Any]:
        if operation not in {"prep", "plan", "submit"}:
            raise ValueError("OpenFE operation must be prep, plan, or submit.")
        return self._request("POST", f"{self._campaign_path(name)}/{operation}")

    def results(self, name: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"{self._campaign_path(name)}/results")
        if not isinstance(payload, list):
            raise OpenFEError("OpenFE results must be a list.")
        return payload

    @staticmethod
    def _check_failure(summary: dict[str, Any]) -> None:
        operation = summary.get("last_operation") or {}
        if operation.get("status") == "failed":
            raise OpenFEError(
                f"OpenFE {operation.get('operation')} failed: {operation.get('message')}"
            )
        failed = [run["name"] for run in summary.get("runs", []) if run.get("state") == "failed"]
        if failed:
            raise OpenFEError(f"OpenFE runs failed: {', '.join(failed)}")

    def _pause(self, name: str, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OpenFETimeout(
                f"Timed out waiting for OpenFE campaign '{name}'. Remote work was not cancelled; "
                "use status() and wait_for_results() to resume without resubmitting."
            )
        time.sleep(min(self.poll_interval, remaining))

    def wait_for_operation(self, name: str, operation: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_timeout
        while True:
            summary = self.status(name)
            self._check_failure(summary)
            record = summary.get("last_operation") or {}
            if record.get("operation") == operation and record.get("status") == "done":
                return summary
            self._pause(name, deadline)

    def submit_campaign(self, campaign: dict[str, Any], *, upload_inputs: bool = False) -> dict[str, Any]:
        name = campaign["name"]
        if upload_inputs:
            self.upload_campaign(campaign)
        else:
            self.create_campaign(campaign)
        for operation in ("prep", "plan"):
            self.start_operation(name, operation)
            self.wait_for_operation(name, operation)
        self.start_operation(name, "submit")
        return {"name": name, "status": "submitted"}

    def wait_for_results(
        self, name: str, expected_repeats: dict[str, int],
    ) -> list[dict[str, Any]]:
        if not expected_repeats or any(repeats < 1 for repeats in expected_repeats.values()):
            raise ValueError("Expected ligand names and positive repeat counts are required.")
        deadline = time.monotonic() + self.wait_timeout
        while True:
            summary = self.status(name)
            self._check_failure(summary)
            operation = summary.get("last_operation") or {}
            if operation.get("operation") == "submit" and operation.get("status") == "done":
                results = self.results(name)
                by_name = {result["ligand"]: result for result in results}
                if all(
                    ligand in by_name and by_name[ligand].get("repeats", 0) >= repeats
                    for ligand, repeats in expected_repeats.items()
                ):
                    return [by_name[ligand] for ligand in expected_repeats]
            self._pause(name, deadline)