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

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_token}",
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
        except Exception as exc:
            raise ToolException(f"GET {url} failed: {exc}")

        if resp.status_code // 100 != 2:
            try:
                payload = resp.json()
            except Exception:
                payload = {"text": resp.text}
            raise ToolException(
                f"GET {url} returned HTTP {resp.status_code}: {payload}"
            )

        try:
            return resp.json()
        except Exception as exc:
            raise ToolException(f"Failed to parse JSON from {url}: {exc}")

    def _maybe_wait_for_result(self, initial_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Poll for job completion. If affinity already present, return immediately.
        Otherwise poll indefinitely until job completes or fails.
        """
        if initial_payload.get("affinity") is not None:
            print("[BoltzTool] Received affinity in initial response, skipping poll.")
            return initial_payload

        job_id = initial_payload.get("job_id")
        if not job_id:
            print("[BoltzTool] No job_id returned; cannot poll.")
            return initial_payload

        last_payload = dict(initial_payload)

        if self.latency_seconds > 0:
            print(f"[BoltzTool] Waiting {self.latency_seconds}s before polling job {job_id}.")
            time.sleep(self.latency_seconds)

        # Poll indefinitely until completion or failure
        attempt = 0
        while True:
            attempt += 1
            print(f"[BoltzTool] Poll attempt {attempt} for job {job_id}.")
            
            try:
                refreshed = self._get_json(f"/jobs/{job_id}")
            except ToolException as e:
                print(f"[BoltzTool] Poll attempt {attempt} failed to fetch job {job_id} status: {e}")
                time.sleep(self.poll_interval)
                continue

            if isinstance(refreshed, dict):
                last_payload.update({k: v for k, v in refreshed.items() if v is not None})
                
                status = last_payload.get("status")
                
                # Job completed successfully
                if last_payload.get("affinity") is not None or status == "completed":
                    print(f"[BoltzTool] Job {job_id} completed on attempt {attempt}.")
                    break
                
                # Job failed
                if status == "failed":
                    print(f"[BoltzTool] Job {job_id} failed: {last_payload.get('error')}")
                    break
                
                # Still running, keep polling
                print(f"[BoltzTool] Job {job_id} status: {status}, continuing to poll...")

            time.sleep(self.poll_interval)

        return last_payload

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

    # HTTP helpers
    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                payload_str = json.dumps(body)
                print(
                    f"[BoltzTool] POST {url} (attempt {attempt + 1}/{self.max_retries + 1}) "
                    f"payload_len={len(payload_str)}"
                )
                resp = self.session.post(url, headers=headers, data=payload_str, timeout=self.timeout)
                if resp.status_code // 100 == 2:
                    print(f"[BoltzTool] POST {url} succeeded with status {resp.status_code}")
                    return resp.json()
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"text": resp.text}
                raise ToolException(
                    f"Server error {resp.status_code} at {url}. "
                    f"stderr: {payload.get('stderr','')} stdout: {payload.get('stdout','')}"
                )
            except Exception as e:
                last_exc = e
                print(f"[BoltzTool] POST {url} failed on attempt {attempt + 1}: {e}")
                if attempt == self.max_retries:
                    break
                time.sleep(0.6 * (attempt + 1))
                attempt += 1
        raise ToolException(f"Request failed after retries: {last_exc}")
    
    def _download_cif_if_wanted(self, cif_url: Optional[str], job_id: str) -> Optional[str]:
        if not (self.fetch_cif and cif_url and self.cif_save_dir):
            return None
        url = self._build_url(str(cif_url))
        headers = {"Authorization": f"Bearer {self.api_token}"}
        r = self.session.get(url, headers=headers, timeout=self.timeout, stream=True)
        if r.status_code != 200:
            return None
        os.makedirs(self.cif_save_dir, exist_ok=True)
        dst = os.path.join(self.cif_save_dir, f"{job_id}_model_0.cif")
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
        return dst

    def _run(self, **kwargs) -> str:
        """Run Boltz affinity submission for multiple ligands."""
        data = BoltzInput.model_validate(kwargs)  # pydantic v2

        protein_entries = self._protein_entries_from_polymers(data.polymers)
        ligand_chain_id = self._pick_ligand_id(self._collect_used_chain_ids(data.polymers))

        per_ligand: Dict[str, Any] = {}
        
        try:
            from server.tasks.slurm import submit_boltz_job, poll_boltz_job
            use_celery = True
        except ImportError:
            print("[BoltzTool] Celery tasks not available, falling back to HTTP.")
            use_celery = False

        if use_celery:
            # - submit all jobs in parallel
            pending_submissions = {}  # ligand_id -> AsyncResult
            
            for ligand_id, smiles in data.ligands.items():
                print(f"[BoltzTool] Preparing submission for ligand '{ligand_id}'.")
                yaml_text = self._affinity_yaml(
                    protein_sequences=protein_entries,
                    ligand_smiles=smiles,
                    ligand_chain_id=ligand_chain_id,
                    constraints=data.constraints,
                    templates=data.templates,
                )
                print(f"[BoltzTool] Submitting ligand '{ligand_id}' via Celery task.")
                pending_submissions[ligand_id] = submit_boltz_job.delay(yaml_text)

            # - wait for submission confirmation (get job_ids)
            submitted_jobs = {}  # ligand_id -> job_id
            for ligand_id, task in pending_submissions.items():
                try:
                    submission_result = task.get(timeout=60)
                    job_id = submission_result.get("job_id")
                    submitted_jobs[ligand_id] = job_id
                    print(f"[BoltzTool] Job submitted for '{ligand_id}' with ID: {job_id}")
                except Exception as e:
                    print(f"[BoltzTool] Failed to submit job for '{ligand_id}': {e}")
                    per_ligand[ligand_id] = {"error": f"Submission failed: {e}"}

            # 3. Poll all jobs until completion
            completed_results = {}  # ligand_id -> result dict
            start_time = time.time()
            
            while len(completed_results) < len(submitted_jobs):
                if time.time() - start_time > (self.poll_attempts * self.poll_interval):
                    print("[BoltzTool] Global timeout reached while polling jobs.")
                    break

                active_jobs = [
                    (lid, jid) for lid, jid in submitted_jobs.items() 
                    if lid not in completed_results
                ]
                
                if not active_jobs:
                    break

                for ligand_id, job_id in active_jobs:
                    try:
                        poll_task = poll_boltz_job.delay(job_id)
                        status_result = poll_task.get(timeout=30)
                        status = status_result.get("status")
                        
                        if status in ["completed", "failed"]:
                            completed_results[ligand_id] = status_result
                    except Exception as e:
                        print(f"[BoltzTool] Error polling job {job_id}: {e}")
                
                if len(completed_results) < len(submitted_jobs):
                    time.sleep(self.poll_interval)

            # 4. Process results
            for ligand_id, job_id in submitted_jobs.items():
                final_result = completed_results.get(ligand_id)
                if not final_result:
                    final_result = {"status": "timeout", "job_id": job_id}

                outputs_dir = final_result.get("outputs_dir")
                affinity = None
                confidence = None
                cif_url = None
                saved_cif = None
                
                if outputs_dir:
                    try:
                        out_path = Path(outputs_dir)
                        # Look for affinity_*.json
                        aff_files = list(out_path.glob(f"**/affinity_{job_id}.json"))
                        if aff_files:
                            with open(aff_files[0]) as f:
                                affinity = json.load(f)
                        
                        # Look for confidence_*.json
                        conf_files = list(out_path.glob(f"**/confidence_{job_id}_model_0.json"))
                        if conf_files:
                            with open(conf_files[0]) as f:
                                confidence = json.load(f)
                                
                        # Look for CIF
                        cif_files = list(out_path.glob(f"**/{job_id}_model_0.cif"))
                        if cif_files:
                            cif_src = cif_files[0]
                            
                            # Copy CIF to artifacts directory if configured
                            if self.fetch_cif and self.cif_save_dir:
                                import shutil
                                os.makedirs(self.cif_save_dir, exist_ok=True)
                                dst = os.path.join(self.cif_save_dir, f"{job_id}_model_0.cif")
                                shutil.copy2(cif_src, dst)
                                saved_cif = dst
                                cif_url = f"file://{dst}"
                            else:
                                saved_cif = str(cif_src)
                                cif_url = f"file://{saved_cif}"
                            
                    except Exception as e:
                        print(f"[BoltzTool] Error reading results from {outputs_dir}: {e}")

                entry = {
                    "job_id": job_id,
                    "status": final_result.get("status"),
                    "outputs_dir": outputs_dir,
                    "affinity": affinity,
                    "confidence": confidence,
                    "cif_url": cif_url,
                    "cif_file": saved_cif,
                }
                
                if affinity is None:
                    entry["error"] = f"No affinity score returned (status={final_result.get('status')})"

                per_ligand[ligand_id] = entry
                
                print(
                    f"[BoltzTool] Completed ligand '{ligand_id}' job_id={job_id} "
                    f"affinity={affinity} confidence={confidence}"
                )

        else:
            for ligand_id, smiles in data.ligands.items():
                print(f"[BoltzTool] Preparing submission for ligand '{ligand_id}'.")
                yaml_text = self._affinity_yaml(
                    protein_sequences=protein_entries,
                    ligand_smiles=smiles,
                    ligand_chain_id=ligand_chain_id,
                    constraints=data.constraints,
                    templates=data.templates,
                )
                
                print(
                    f"[BoltzTool] Submitting ligand '{ligand_id}' to /submit_yaml_text with chain '{ligand_chain_id}'."
                )
                resp = self._post_json("/submit_yaml_text", {"yaml_text": yaml_text})
                if isinstance(resp, dict):
                    resp = self._maybe_wait_for_result(resp)
                saved_cif = self._download_cif_if_wanted(resp.get("cif_url"), job_id=resp.get("job_id", ""))

            job_id = resp.get("job_id")
            affinity = resp.get("affinity")
            confidence = resp.get("confidence")

            print(
                f"[BoltzTool] Completed ligand '{ligand_id}' job_id={job_id} "
                f"affinity={affinity} confidence={confidence}"
            )

            entry: Dict[str, Any] = {
                "job_id": job_id,
                "outputs_dir": resp.get("outputs_dir"),
                "affinity": affinity,
                "confidence": confidence,
                "cif_url": resp.get("cif_url"),
                "cif_file": saved_cif,
            }

            # If the service did not return an affinity score
            if affinity is None:
                # Try to gather a helpful server-side message from known keys
                server_msg = None
                for k in ("error", "message", "stderr", "stdout", "status"):
                    v = resp.get(k) if isinstance(resp, dict) else None
                    if v:
                        server_msg = v
                        break

                payload_summary = {}
                if isinstance(resp, dict):
                    for k in ("job_id", "status", "outputs_dir", "cif_url"):
                        if resp.get(k) is not None:
                            payload_summary[k] = resp.get(k)

                entry["error"] = (
                    f"No affinity score returned for ligand '{ligand_id}'"
                    f" (job_id={job_id}). Server message: {server_msg!r}."
                    f" Payload summary: {json.dumps(payload_summary, ensure_ascii=False)}"
                )

            per_ligand[ligand_id] = entry

        return json.dumps(
            {"count": len(per_ligand), "base_url": self.base_url, "ligand_chain_id": ligand_chain_id, "per_ligand": per_ligand},
            ensure_ascii=False,
        )

    async def _arun(self, **kwargs) -> str:
        """Async run not implemented for BoltzTool."""
        raise NotImplementedError("Use the synchronous .run() for now.")
