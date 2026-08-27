"""Core state schema for the molecular optimization workflow.

This module defines the Pydantic models that represent the complete state
of the molecular optimization workflow as it flows through a LangGraph graph.
"""
import os
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple, Callable, Union, ClassVar
from pydantic import BaseModel, Field, PrivateAttr, model_validator, ConfigDict

from schemas.tool_registry import ToolRunRecord, ToolSelection


class OptimizationMode(str, Enum):
    """Defines the optimization mode for a target property."""
    MAXIMIZE = "MAX"
    MINIMIZE = "MIN"
    MATCH = "MATCH"


class MoleculeSource(str, Enum):
    """Specifies the origin of molecules for the optimization process."""
    GENERATED = "generated"
    PROVIDED = "provided"
    ENUMERATED = "enumerated"
    EXTERNAL_LIBRARY = "external_library"


class WorkflowStatus(str, Enum):
    """Represents the overall status of the optimization workflow."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"



class TargetProperty(BaseModel):
    """Defines a target property to be optimized."""
    name: str
    mode: OptimizationMode = OptimizationMode.MAXIMIZE
    weight: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Weight of the property in the composite score.",
    )
    bounds: Optional[Tuple[float, float]] = Field(
        default=None, description="Acceptable bounds for the property value (min, max)."
    )
    transformation: Optional[str] = Field(
        default=None, description="Transformation to apply to the property value."
    )


class BOConfiguration(BaseModel):
    """Configuration settings for Bayesian Optimization."""
    acquisition_function: str = "expected_improvement"
    batch_size: int = Field(default=5, ge=1)
    n_initial_points: int = Field(default=10, ge=1)
    max_iterations: int = Field(default=4, ge=1)
    encoding: str = Field(
        default=os.getenv("MOLECULAR_FP", "MORDRED"), description="Molecular encoding/fingerprint method."
    )
    convergence_threshold: Optional[float] = 1e-6
    exploration_weight: float = Field(default=0.1, ge=0.0)


class ExperimentResult(BaseModel):
    """Stores the result from a single experimental or computational evaluation."""
    molecule_id: str
    smiles: str
    iteration: int
    properties: Dict[str, float]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)
    source: str = Field(
        default="computational",
        description="Source of the result (e.g., 'experimental', 'computational').",
    )


class ProteinTarget(BaseModel):
    """Describes a protein chain target for affinity predictions."""

    chain_id: Union[str, List[str]] = Field(
        default="A",
        description="Chain identifier(s) associated with this protein target.",
    )
    sequence: Optional[str] = Field(
        default=None,
        description="Amino acid sequence using single-letter codes.",
    )
    uniprot_id: Optional[str] = Field(
        default=None,
        description="UniProt accession identifier for fetching the protein sequence.",
    )
    msa: Optional[str] = Field(
        default=None,
        description="Optional path to a multiple sequence alignment file.",
    )
    cyclic: Optional[bool] = Field(
        default=None,
        description="Whether the protein chain should be treated as cyclic.",
    )
    modifications: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Post-translational or residue-level modifications.",
    )

    @model_validator(mode="after")
    def _validate_and_normalize(self) -> "ProteinTarget":
        """Ensure either a sequence or UniProt ID is present and normalize values."""

        if isinstance(self.chain_id, str):
            self.chain_id = self.chain_id.strip() or "A"
        else:
            cleaned_ids = [cid.strip() for cid in self.chain_id if cid and cid.strip()]
            self.chain_id = cleaned_ids or "A"

        if self.sequence:
            normalized_seq = "".join(self.sequence.split()).upper()
            if normalized_seq:
                allowed = set("ACDEFGHIKLMNPQRSTVWYBXZJUO")
                if not set(normalized_seq) <= allowed:
                    raise ValueError(
                        "Protein sequences must contain valid amino-acid one-letter codes."
                    )
                self.sequence = normalized_seq
            else:
                self.sequence = None

        if self.uniprot_id:
            self.uniprot_id = self.uniprot_id.strip().upper()

        if not (self.sequence or self.uniprot_id):
            raise ValueError("ProteinTarget requires a sequence and/or UniProt ID.")

        return self


class WorkflowState(BaseModel):
    """
    The complete, self-contained state for the molecular optimization workflow.
    This Pydantic model is the object that flows through the LangGraph, being
    updated by each node in the process.
    """

    # === Core Identifiers & Configuration ===
    workflow_id: str = Field(
        default_factory=lambda: f"workflow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    user_prompt: str
    parsed_arguments: Dict[str, Any] = Field(default_factory=dict)
    targets: List[TargetProperty] = Field(default_factory=list)

    starting_molecules: List[str] = Field(
        default_factory=list, description="Initial list of SMILES strings."
    )
    molecule_source: Optional[MoleculeSource] = None
    search_space: Dict[str, str] = Field(
        default_factory=dict, description="Master dictionary of all molecules (ID -> SMILES)."
    )
    library_config: Dict[str, Any] = Field(
        default_factory=dict, description="Configuration for external libraries."
    )
    characterization_config: Dict[str, Any] = Field(
        default_factory=dict, description="Configuration for characterization tools."
    )
    stage_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Generic per-stage configuration for registry-managed workflow stages.",
    )
    selected_tools: Dict[str, List[ToolSelection]] = Field(
        default_factory=dict,
        description="Tool selections made for each workflow stage.",
    )
    tool_runs: List[ToolRunRecord] = Field(
        default_factory=list,
        description="Structured records of registry-managed tool executions.",
    )
    extensions: Dict[str, Any] = Field(
        default_factory=dict,
        description="Namespaced extension data for future tools and plugins.",
    )
    run_paths: Dict[str, str] = Field(
        default_factory=dict,
        description="Filesystem paths provisioned for this run (inputs, outputs, artifacts, etc.).",
    )
    profiling: Dict[str, Any] = Field(
        default_factory=dict,
        description="Timing and profiling metadata collected during workflow execution.",
    )

    llm_client: Optional[Any] = Field(
        default=None, exclude=True
    )

    # === Bayesian Optimization State ===
    bo_config: Optional[BOConfiguration] = None
    bo_rounds: List[Dict[str, Any]] = Field(
        default_factory=list, description="History of BO iterations."
    )
    current_bo_recommendations: List[str] = Field(
        default_factory=list, description="Molecule IDs recommended by BO for this iteration."
    )

    experimental_results: List[ExperimentResult] = Field(default_factory=list)
    best_molecules: List[Tuple[str, float]] = Field(
        default_factory=list, description="Top molecules found so far (SMILES, score)."
    )

    protein_targets: List[ProteinTarget] = Field(
        default_factory=list,
        description="Protein chains provided for affinity predictions.",
    )

    # === Workflow Control & Status ===
    current_iteration: int = 0
    max_iterations: int = 10
    status: WorkflowStatus = WorkflowStatus.INITIALIZING
    exit_reason: Optional[str] = None

    llm_history: List[Dict[str, Any]] = Field(default_factory=list)
    current_thought: Optional[str] = None
    current_action: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None

    # Private callback used for UI live events; not part of the public schema
    _event_callback: Optional[Callable[[Dict[str, Any]], None]] = PrivateAttr(default=None)
    _callback_registry: ClassVar[Dict[str, Callable[[Dict[str, Any]], None]]] = {}
    _registry_lock: ClassVar[RLock] = RLock()

    def log(self, action: str, data: Any = None) -> None:
        """Adds a structured log entry to the state."""
        event = {
            "timestamp": datetime.now().isoformat(),
            "iteration": self.current_iteration,
            "action": action,
            "data": data,
        }
        self.logs.append(event)
        self.updated_at = datetime.now()

        # Fan out to UI if an event callback is present
        cb = getattr(self, "_event_callback", None)
        if cb is None:
            with self._registry_lock:
                cb = self._callback_registry.get(self.workflow_id)
        if cb is not None:
            try:
                payload = {
                    **event,
                    "workflow_id": self.workflow_id,
                }
                cb(payload)
            except Exception:
                # Never let UI callback failures break the workflow
                pass

    def add_experimental_result(self, result: ExperimentResult) -> None:
        """Add an observation and refresh the legacy best-molecule projection."""
        self.experimental_results.append(result)

        if not self.targets:
            return

        from utils.objective_ranking import (
            build_optimization_report,
            compatibility_best_molecules,
        )

        report = build_optimization_report(self)
        self.best_molecules = compatibility_best_molecules(report)

    def record_tool_selection(self, selection: ToolSelection) -> None:
        """Record a tool selected for a workflow stage."""
        stage = selection.stage.value if hasattr(selection.stage, "value") else str(selection.stage)
        self.selected_tools.setdefault(stage, []).append(selection)
        self.log("tool_selected", selection.model_dump(mode="json"))

    def record_tool_run(self, record: ToolRunRecord) -> None:
        """Record a tool execution result for auditability."""
        self.tool_runs.append(record)
        self.log("tool_run_recorded", record.model_dump(mode="json"))

    def should_continue(self) -> bool:
        """Determines if the workflow should continue to the next iteration."""
        is_terminated = self.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.STOPPED,
        }
        is_max_iterations_reached = self.current_iteration >= self.max_iterations

        return not (is_terminated or is_max_iterations_reached)

    def model_copy(self, *args, **kwargs):  # type: ignore[override]
        """Ensure private event callbacks survive state cloning."""
        copied = super().model_copy(*args, **kwargs)
        if isinstance(copied, WorkflowState):
            setattr(copied, "_event_callback", getattr(self, "_event_callback", None))
        return copied

    @classmethod
    def register_event_callback(cls, workflow_id: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        with cls._registry_lock:
            cls._callback_registry[workflow_id] = callback

    @classmethod
    def unregister_event_callback(cls, workflow_id: str) -> None:
        with cls._registry_lock:
            cls._callback_registry.pop(workflow_id, None)

    model_config = ConfigDict(
        # """Pydantic model configuration."""
        validate_assignment = True,
        use_enum_values = True,
        arbitrary_types_allowed = True
    )
