import json
import os
import time
from typing import Dict, Optional, Any, List, Union

import requests
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
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
    args_schema = BoltzInput

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 300.0,
        max_retries: int = 1,
        session: Optional[requests.Session] = None,
        fetch_cif: bool = True,
        cif_save_dir: Optional[str] = None,
    ):
        super().__init__()
        self.base_url = (base_url or os.environ.get("BOLTZ_BASE_URL", "")).rstrip("/")
        self.api_token = api_token or os.environ.get("BOLTZ_API_TOKEN", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.fetch_cif = fetch_cif
        self.cif_save_dir = cif_save_dir or "./boltz_cifs"
        self._uniprot_cache: Dict[str, str] = {}

        if not self.base_url or not self.api_token:
            raise ToolException("Missing BOLTZ_BASE_URL and/or BOLTZ_API_TOKEN configuration.")

    # UniProt helpers
    def _fetch_uniprot_seq(self, uniprot_id: str, timeout: float = 10.0) -> str:
        uid = uniprot_id.strip()
        if not uid:
            raise ToolException("uniprot_id is empty.")
        if uid in self._uniprot_cache:
            return self._uniprot_cache[uid]
        url = f"https://rest.uniprot.org/uniprotkb/{uid}.fasta"
        try:
            resp = self.session.get(url, timeout=timeout)
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
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                resp = self.session.post(url, headers=headers, data=json.dumps(body), timeout=self.timeout)
                if resp.status_code // 100 == 2:
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
                if attempt == self.max_retries:
                    break
                time.sleep(0.6 * (attempt + 1))
                attempt += 1
        raise ToolException(f"Request failed after retries: {last_exc}")
    
    def _download_cif_if_wanted(self, cif_url: Optional[str], job_id: str) -> Optional[str]:
        if not (self.fetch_cif and cif_url and self.cif_save_dir):
            return None
        url = f"{self.base_url}{cif_url}" if not str(cif_url).startswith("http") else str(cif_url)
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
        for ligand_id, smiles in data.ligands.items():
            yaml_text = self._affinity_yaml(
                protein_sequences=protein_entries,
                ligand_smiles=smiles,
                ligand_chain_id=ligand_chain_id,
                constraints=data.constraints,
                templates=data.templates,
            )
            resp = self._post_json("/submit_yaml_text", {"yaml_text": yaml_text})

            saved_cif = self._download_cif_if_wanted(resp.get("cif_url"), job_id=resp.get("job_id", ""))

            per_ligand[ligand_id] = {
                "job_id": resp.get("job_id"),
                "outputs_dir": resp.get("outputs_dir"),
                "affinity": resp.get("affinity"),
                "confidence": resp.get("confidence"),
                "cif_url": resp.get("cif_url"),
                "cif_file": saved_cif,
            }

        return json.dumps(
            {"count": len(per_ligand), "base_url": self.base_url, "ligand_chain_id": ligand_chain_id, "per_ligand": per_ligand},
            ensure_ascii=False,
        )

    async def _arun(self, **kwargs) -> str:
        """Async run not implemented for BoltzTool."""
        raise NotImplementedError("Use the synchronous .run() for now.")
