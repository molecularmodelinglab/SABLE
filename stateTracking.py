"""
StateTracker.py
==============

State management for molecular optimization workflow.
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ============================================================================
# ENUMS AND DATACLASSES
# ============================================================================

class OptimizationObjective(Enum):
    QED = "qed"
    SOLUBILITY = "solubility"
    PERMEABILITY = "permeability"
    TOXICITY = "toxicity"
    BINDING_AFFINITY = "binding_affinity"
    SELECTIVITY = "selectivity"
    MOLECULAR_WEIGHT = "molecular_weight"

class MoleculeSource(Enum):
    GENERATED = "generated"
    GIVEN = "given"
    SCREENING_LIBRARY = "screening_library"
    ENUMERATED = "enumerated"

class ExperimentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class BOParameters:
    #TODO: Expand with actual BO parameters
    acquisition_function: str = "expected_improvement"
    kernel: str = "rbf"
    n_initial_points: int = 10
    max_iterations: int = 50
    batch_size: int = 5
    exploration_weight: float = 0.1
    convergence_threshold: float = 1e-6
    random_seed: Optional[int] = 42

@dataclass
class EnumerationParameters: 
    # TODO: Expand with actual enumeration parameters
    max_molecules: int = 1000
    diversity_threshold: float = 0.8
    property_filters: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    scaffold_constraints: Optional[str] = None

@dataclass
class MoleculeEntry:
    smiles: str
    id: str = field(default_factory=lambda: f"mol_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
    source: MoleculeSource = MoleculeSource.GENERATED
    properties: Dict[str, float] = field(default_factory=dict)
    experimental_results: Dict[str, Any] = field(default_factory=dict)
    bo_score: Optional[float] = None
    generation_round: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class ExperimentRun:
    experiment_id: str
    molecules: List[str]  # SMILES strings
    molecule_ids: List[str]  # Internal molecule IDs
    experiment_type: str
    status: ExperimentStatus
    results: Dict[str, Any] = field(default_factory=dict)
    llm_prompt: Optional[str] = None
    llm_response: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

# ============================================================================
# STATE TRACKER
# ============================================================================

class StateTracker:
    def __init__(self, research_id: str, prompt: str):
        self.research_id = research_id
        self.original_prompt = prompt
        self.created_at = datetime.now()

        # Core research components
        self.objectives: List[OptimizationObjective] = []
        self.target_properties: Dict[str, Dict[str, float]] = {}  # property: {min: x, max: y, weight: z}
        self.budget: Dict[str, int] = {"experiments": 100, "iterations": 10}

        # Molecule management
        self.molecules: Dict[str, MoleculeEntry] = {}
        self.starting_molecules: List[str] = []  # SMILES strings
        self.current_generation: int = 0

        # Library and enumeration
        self.molecule_source: Optional[MoleculeSource] = None
        self.enumeration_params: Optional[EnumerationParameters] = None
        self.external_library_config: Dict[str, Any] = {}

        # Bayesian Optimization
        self.bo_params: Optional[BOParameters] = None
        self.bo_history: List[Dict[str, Any]] = []
        self.current_candidates: List[str] = []

        # Experimental tracking
        self.experiments: Dict[str, ExperimentRun] = {}
        self.experiment_queue: List[str] = []
        self.completed_experiments: List[str] = []

        # Results and analysis
        self.iteration_results: List[Dict[str, Any]] = []
        self.best_molecules: List[Tuple[str, float]] = []  # (smiles, score)
        self.convergence_history: List[float] = []

        # State management
        self.current_iteration: int = 0
        self.is_complete: bool = False
        self.exit_condition_met: Optional[str] = None

        # Recording and metadata
        self.logs: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}

    def log_action(self, action: str, data: Any) -> None:
        """Log an action with timestamp"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "iteration": self.current_iteration,
            "action": action,
            "data": data
        }
        self.logs.append(log_entry)