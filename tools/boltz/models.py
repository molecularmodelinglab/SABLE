import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BoltzProvider(str, Enum):
    SELF_HOSTED = "self_hosted"
    PLATFORM = "platform"


class BoltzExecutionKind(str, Enum):
    PREDICTION = "prediction"
    LIBRARY_SCREEN = "library_screen"


class BoltzJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class BoltzMolecule(BaseModel):
    id: str
    smiles: str


class BoltzMetricSet(BaseModel):
    affinity: Optional[float] = None
    binding_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    optimization_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    structure_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ptm: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    iptm: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    complex_plddt: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    complex_iplddt: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class BoltzRequest(BaseModel):
    provider: BoltzProvider
    execution_kind: BoltzExecutionKind
    molecules: List[BoltzMolecule]
    proteins: List[Dict[str, Any]]
    protein_scope_id: str
    options: Dict[str, Any] = Field(default_factory=dict)


class BoltzSubmission(BaseModel):
    provider: BoltzProvider
    execution_kind: BoltzExecutionKind
    provider_job_id: str
    protein_scope_id: str
    status: BoltzJobStatus = BoltzJobStatus.PENDING
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BoltzMoleculeResult(BaseModel):
    molecule_id: str
    protein_scope_id: str
    provider_result_id: Optional[str] = None
    status: BoltzJobStatus
    metrics: BoltzMetricSet = Field(default_factory=BoltzMetricSet)
    structure_path: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    sanitized_error: Optional[str] = None


def protein_scope_id(proteins: List[Dict[str, Any]]) -> str:
    """Return a stable scope for scores that are comparable only per protein target."""

    canonical_proteins = []
    for protein in proteins:
        chain_ids = protein.get("chain_id", "A")
        if isinstance(chain_ids, str):
            chain_ids = [chain_ids]

        modifications = protein.get("modifications") or []
        canonical_proteins.append({
            "chain_ids": sorted(str(chain_id).strip() for chain_id in chain_ids),
            "sequence": "".join(str(protein.get("sequence") or "").split()).upper() or None,
            "uniprot_id": str(protein.get("uniprot_id") or "").strip().upper() or None,
            "cyclic": protein.get("cyclic"),
            "modifications": sorted(
                modifications,
                key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
            ),
        })

    canonical_proteins.sort(
        key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    )
    payload = json.dumps(
        {"version": 1, "proteins": canonical_proteins},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"boltz-protein-scope-v1:{hashlib.sha256(payload).hexdigest()}"