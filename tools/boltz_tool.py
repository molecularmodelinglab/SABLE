import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Any, List, Union

import requests
import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator
from langchain.tools import BaseTool


class ToolException(Exception):
    """Custom exception for tool errors."""
    pass


class Polymer(BaseModel):
    """Helper model for polymer definitions in Boltz input."""
    chain_id: Union[str, List[str]] = Field(..., description="Chain ID, or list of chain IDs")
    sequence: Optional[str] = Field(None, description="Amino acid sequence (no whitespace)")
    uniprot_id: Optional[str] = Field(None, description="If provided, sequence will be fetched")
    msa: Optional[str] = Field(None, description="MSA file path (protein only)")
    cyclic: Optional[bool] = Field(None, description="Whether the chain is cyclic")
    modifications: Optional[List[Dict[str, Any]]] = Field(
        None, description="[{position: int, ccd: str}, ...]"
    )

    @model_validator(mode="after")
    def _require_seq_or_uniprot(self) -> "Polymer":
        if not (self.sequence and self.sequence.strip()) and not (
            self.uniprot_id and self.uniprot_id.strip()
        ):
            raise ValueError("Each polymer must have either 'sequence' or 'uniprot_id'.")
        return self


class BoltzInput(BaseModel):
    """Input for Boltz affinity submission tool."""
    ligands: Dict[str, str] = Field(..., description="Mapping of ligand_id -> SMILES")
    polymers: List[Polymer] = Field(..., description="List of polymer chains")

    constraints: Optional[List[Dict[str, Any]]] = Field(
        None, description="Boltz constraints (bond/pocket/contact)"
    )
    templates: Optional[List[Dict[str, Any]]] = Field(
        None, description="Boltz templates (cif/pdb options)"
    )

    @field_validator("ligands")
    @classmethod
    def _ligands_non_empty(cls, v: Dict[str, str]) -> Dict[str, str]:
        if not v:
            raise ValueError("`ligands` cannot be empty. Provide at least one {id: SMILES} pair.")
        for k, smiles in v.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError("Every ligand key must be a non-empty string ID.")
            if not isinstance(smiles, str) or not smiles.strip():
                raise ValueError(f"Ligand '{k}' has empty SMILES.")
        return v

    @field_validator("polymers")
    @classmethod
    def _polymers_non_empty(cls, v: List[Polymer]) -> List[Polymer]:
        if not v:
            raise ValueError("`polymers` cannot be empty. Provide at least one Polymer.")
        return v


class BoltzTool(BaseTool):
    name: str = "boltz_submit"
    description: str = (
        "Submit per-ligand Boltz affinity jobs (single ligand per job, multi-chain protein allowed). "
        "Inputs: ligands={id: SMILES}, polymers=[...], optional constraints/templates."
    )
    args_schema: type[BaseModel] = BoltzInput

    _base_url: str = PrivateAttr("")
    _api_token: str = PrivateAttr("")
    _timeout: float = PrivateAttr(900.0)  # Default 15min for synchronous server
    _max_retries: int = PrivateAttr(1)
    _session: Optional[requests.Session] = PrivateAttr(default=None)
    _fetch_cif: bool = PrivateAttr(True)
    _cif_save_dir: str = PrivateAttr("./boltz_cifs")
    _latency_seconds: float = PrivateAttr(0.0)
    _poll_interval: float = PrivateAttr(5.0)
    _poll_attempts: int = PrivateAttr(0)
    _uniprot_cache: Dict[str, str] = PrivateAttr(default_factory=dict)

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 900.0,  # Increased: server runs Boltz synchronously (up to 600s)
        max_retries: int = 1,
        session: Optional[requests.Session] = None,
        fetch_cif: bool = True,
        cif_save_dir: Optional[str] = None,
        latency_seconds: Optional[float] = None,
        poll_interval: Optional[float] = None,
        poll_attempts: Optional[int] = None,
    ):
        super().__init__()
        self.base_url = (base_url or os.environ.get("BOLTZ_BASE_URL", "")).rstrip("/")
        self.api_token = api_token or os.environ.get("BOLTZ_API_TOKEN", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.fetch_cif = fetch_cif
        self.cif_save_dir = cif_save_dir or "./boltz_cifs"

        # Latency configuration with environment overrides
        env_latency = os.environ.get("BOLTZ_LATENCY_SECONDS")
        if latency_seconds is None and env_latency is not None:
            try:
                latency_seconds = float(env_latency)
            except ValueError:
                latency_seconds = None

        env_poll_interval = os.environ.get("BOLTZ_POLL_INTERVAL")
        if poll_interval is None and env_poll_interval is not None:
            try:
                poll_interval = float(env_poll_interval)
            except ValueError:
                poll_interval = None

        env_poll_attempts = os.environ.get("BOLTZ_POLL_ATTEMPTS")
        if poll_attempts is None and env_poll_attempts is not None:
            try:
                poll_attempts = int(env_poll_attempts)
            except ValueError:
                poll_attempts = None

        # Enable polling by default now that server is async.
        # Jobs return immediately with status="queued", then we poll for completion.
        # Boltz calculations typically take 2-10 minutes per ligand depending on complexity.
        self.latency_seconds = latency_seconds if latency_seconds is not None else 5.0
        self.poll_interval = poll_interval if poll_interval is not None else 5.0
        self.poll_attempts = poll_attempts if poll_attempts is not None else 120  # 10min total

        if not self.base_url or not self.api_token:
            raise ToolException("Missing BOLTZ_BASE_URL and/or BOLTZ_API_TOKEN configuration.")

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: Optional[str]) -> None:
        self._base_url = (value or "").rstrip("/")

    @property
    def api_token(self) -> str:
        return self._api_token

    @api_token.setter
    def api_token(self, value: Optional[str]) -> None:
        self._api_token = (value or "").strip()

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._timeout = float(value)

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @max_retries.setter
    def max_retries(self, value: int) -> None:
        self._max_retries = max(0, int(value))

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    @session.setter
    def session(self, value: Optional[requests.Session]) -> None:
        self._session = value or requests.Session()

    @property
    def fetch_cif(self) -> bool:
        return self._fetch_cif

    @fetch_cif.setter
    def fetch_cif(self, value: bool) -> None:
        self._fetch_cif = bool(value)

    @property
    def cif_save_dir(self) -> str:
        return self._cif_save_dir

    @cif_save_dir.setter
    def cif_save_dir(self, value: Optional[str]) -> None:
        path = (value or "./boltz_cifs").strip()
        self._cif_save_dir = path or "./boltz_cifs"

    @property
    def latency_seconds(self) -> float:
        return self._latency_seconds

    @latency_seconds.setter
    def latency_seconds(self, value: Optional[float]) -> None:
        self._latency_seconds = max(0.0, float(value or 0.0))

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @poll_interval.setter
    def poll_interval(self, value: Optional[float]) -> None:
        interval = float(value or 0.0)
        self._poll_interval = max(0.1, interval) if interval > 0 else 5.0

    @property
    def poll_attempts(self) -> int:
        return self._poll_attempts

    @poll_attempts.setter
    def poll_attempts(self, value: Optional[int]) -> None:
        attempts = int(value or 0)
        self._poll_attempts = max(0, attempts)

    # UniProt helpers
    def _fetch_uniprot_seq(self, uniprot_id: str, timeout: float = 10.0) -> str:
        uid = uniprot_id.strip()
        if not uid:
            raise ToolException("uniprot_id is empty.")
        if uid in self._uniprot_cache:
            return self._uniprot_cache[uid]
        url = f"https://rest.uniprot.org/uniprotkb/{uid}.fasta"
        try:
            print("Fetching UniProt sequence for ID:", uid)
            resp = self.session.get(url, timeout=timeout)
            print("UniProt response status:", resp.status_code)
        except Exception as e:
            raise ToolException(f"UniProt request failed for '{uid}': {e}")
        if resp.status_code != 200 or not resp.text:
            raise ToolException(f"Failed to fetch UniProt FASTA for '{uid}' (HTTP {resp.status_code}).")
        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        if not lines or not lines[0].startswith(">"):
            raise ToolException(f"Unexpected FASTA format for UniProt ID '{uid}'.")
        seq = "".join(ln for ln in lines[1:] if not ln.startswith(">")).replace(" ", "")
        if not seq:
            raise ToolException(f"Parsed empty sequence for UniProt ID '{uid}'.")
        self._uniprot_cache[uid] = seq
        return seq

    # Boltz YAML helpers
    def _collect_used_chain_ids(self, polymers: list) -> set[str]:
        """Gather all polymer chain IDs (str or list[str]) into a flat set."""
        used: set[str] = set()
        for p in polymers:
            v = p.chain_id  # Polymer.chain_id is Union[str, List[str]]
            if isinstance(v, str):
                used.add(v)
            else:
                used.update(v)
        return used

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _wait_for_celery_jobs(
        self,
        pending_jobs: Dict[str, str],
        per_ligand: Dict[str, Dict[str, Any]],
        poll_proxy,
    ) -> None:
        if not poll_proxy or not pending_jobs:
            return

        attempts = 0
        while pending_jobs and attempts < self.poll_attempts:
            for ligand_id in list(pending_jobs.keys()):
                job_id = pending_jobs[ligand_id]
                try:
                    result = poll_proxy(job_id, emit_audit=False)
                except Exception as exc:
                    print(f"[BoltzTool] Polling job {job_id} failed: {exc}")
                    continue

                entry = per_ligand.get(ligand_id)
                if not entry:
                    pending_jobs.pop(ligand_id, None)
                    continue

                status = (result.get("status") or "").lower()
                entry["status"] = status or entry.get("status", "submitted")

                if status == "completed":
                    entry.update(
                        {
                            "affinity": result.get("affinity"),
                            "confidence": result.get("confidence"),
                            "outputs_dir": result.get("outputs_dir"),
                            "cif_path": result.get("cif_path"),
                        }
                    )
                    print(f"[BoltzTool] Boltz job {job_id} completed for ligand '{ligand_id}'.")
                    pending_jobs.pop(ligand_id, None)
                elif status == "failed":
                    entry.update(
                        {
                            "error": result.get("error", "Boltz job failed on HPC"),
                            "outputs_dir": result.get("outputs_dir"),
                        }
                    )
                    print(f"[BoltzTool] Boltz job {job_id} failed for ligand '{ligand_id}'.")
                    pending_jobs.pop(ligand_id, None)
                else:
                    entry["message"] = f"Boltz job {job_id} status: {status or 'pending'}"

            if pending_jobs:
                time.sleep(self.poll_interval)
                attempts += 1

        if pending_jobs:
            print(
                f"[BoltzTool] Timeout reached waiting for {len(pending_jobs)} Celery Boltz job(s) to finish."
            )

    def _pick_ligand_id(self, used: set[str]) -> str:
        """Pick a ligand chain ID that doesn't collide with polymer IDs."""
        candidates = ["L", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "LL"]
        for c in candidates:
            if c not in used:
                return c
        i = 1
        while True:
            c = f"L{i}"
            if c not in used:
                return c
            i += 1

    def _protein_entries_from_polymers(self, polymers: List[Polymer]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for poly in polymers:
            if poly.sequence and poly.sequence.strip():
                seq = poly.sequence.replace("\n", "").replace(" ", "")
            else:
                seq = self._fetch_uniprot_seq(poly.uniprot_id or "")
            protein_entry: Dict[str, Any] = {"protein": {"id": poly.chain_id, "sequence": seq}}
            if poly.msa:
                protein_entry["protein"]["msa"] = poly.msa
            if poly.cyclic is not None:
                protein_entry["protein"]["cyclic"] = bool(poly.cyclic)
            if poly.modifications:
                protein_entry["protein"]["modifications"] = poly.modifications
            entries.append(protein_entry)
        return entries
    
    @staticmethod
    def _affinity_yaml(
        protein_sequences: List[Dict[str, Any]],
        ligand_smiles: str,
        ligand_chain_id: str,
        constraints: Optional[List[Dict[str, Any]]] = None,
        templates: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        doc: Dict[str, Any] = {
            "version": 1,
            "sequences": [
                *protein_sequences, 
                {"ligand": {"id": ligand_chain_id, "smiles": str(ligand_smiles)}}
            ],
            "properties": [{"affinity": {"binder": ligand_chain_id}}],
        }
        if constraints:
            doc["constraints"] = constraints
        if templates:
            doc["templates"] = templates
        return yaml.safe_dump(doc, sort_keys=False)

    def _run(self, **kwargs) -> str:
        """Run Boltz affinity submission for multiple ligands."""
        data = BoltzInput.model_validate(kwargs)  # pydantic v2

        protein_entries = self._protein_entries_from_polymers(data.polymers)
        ligand_chain_id = self._pick_ligand_id(self._collect_used_chain_ids(data.polymers))

        per_ligand: Dict[str, Any] = {}

        try:
            from server.tasks.slurm import submit_boltz_job, poll_boltz_job_once
        except ImportError as exc:
            raise ToolException(
                "Boltz submission Celery tasks are unavailable; install `server.tasks.slurm`."
            ) from exc

        pending_jobs: Dict[str, str] = {}
        import uuid
        for ligand_id, smiles in data.ligands.items():
            print(f"[BoltzTool] Preparing submission for ligand '{ligand_id}'.")
            yaml_text = self._affinity_yaml(
                protein_sequences=protein_entries,
                ligand_smiles=smiles,
                ligand_chain_id=ligand_chain_id,
                constraints=data.constraints,
                templates=data.templates,
            )

            job_id = str(uuid.uuid4())
            print(f"[BoltzTool] Submitting ligand '{ligand_id}' via Celery task (Async). Job ID: {job_id}")

            task = submit_boltz_job.delay(yaml_text, job_id=job_id)

            per_ligand[ligand_id] = {
                "job_id": job_id,
                "celery_task_id": task.id,
                "status": "submitted",
                "message": f"Job submitted to HPC queue. Results will be available in /data/runs/{job_id}/artifacts/boltz_cifs"
            }
            pending_jobs[ligand_id] = job_id

        if pending_jobs:
            self._wait_for_celery_jobs(pending_jobs, per_ligand, poll_boltz_job_once)

        return json.dumps(
            {"count": len(per_ligand), "base_url": self.base_url, "ligand_chain_id": ligand_chain_id, "per_ligand": per_ligand},
            ensure_ascii=False,
        )

    async def _arun(self, **kwargs) -> str:
        """Async run not implemented for BoltzTool."""
        raise NotImplementedError("Use the synchronous .run() for now.")
