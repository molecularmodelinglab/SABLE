"""
Schemas for tool inputs and outputs in the workflow.
"""
import os
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from enum import Enum


class EnumerationStrategy(str, Enum):
    """Strategy for molecular enumeration."""
    REACTION_BASED = "reaction_based"
    SCAFFOLD_HOPPING = "scaffold_hopping"
    FRAGMENT_GROWTH = "fragment_growth"
    SIMILARITY_BASED = "similarity_based"


class HealerMode(str, Enum):
    """High-level HEALER enumeration modes used by the EnumeratorTool."""
    MOLECULE_HEALER = "MoleculeHEALER"
    SITE_HEALER = "SiteHEALER"
    FRAGMENT_HEALER = "FragmentHEALER"


class EnumerationRequest(BaseModel):
    """Request for molecule enumeration."""
    starting_smiles: str
    strategy: EnumerationStrategy = EnumerationStrategy.REACTION_BASED
    healer_mode: Optional[HealerMode] = Field(default=HealerMode.MOLECULE_HEALER, description="Which HEALER mode to use: MoleculeHEALER, SiteHEALER or FragmentHEALER.")
    max_molecules: int = Field(default=100, ge=1, le=10000)
    diversity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    property_filters: Optional[Dict[str, tuple[float, float]]] = None
    reaction_types: Optional[List[str]] = None


class EnumerationResult(BaseModel):
    """Result from molecule enumeration."""
    molecules: Dict[str, str]  # ID -> SMILES mapping
    count: int
    strategy_used: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CharacterizationRequest(BaseModel):
    """Request for molecule characterization."""
    smiles: Union[str, List[str]]
    properties: List[str]  # e.g., ["QED", "TPSA", "LogP"]
    include_descriptors: bool = False


class CharacterizationResult(BaseModel):
    """Result from molecule characterization."""
    results: Dict[str, Dict[str, float]]  # SMILES -> {property: value}
    failed_molecules: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BORecommendationRequest(BaseModel):
    """Request for Bayesian Optimization recommendations."""
    search_space: Dict[str, str]  # ID -> SMILES
    targets: List[Dict[str, Any]]  # Target configurations
    measurements: Optional[List[Dict[str, Any]]] = None  # Previous measurements
    batch_size: int = Field(default=5, ge=1)
    encoding: str = os.getenv("MOLECULAR_FP", "MORDRED")


class BORecommendationResult(BaseModel):
    """Result from Bayesian Optimization."""
    recommended_ids: List[str]
    acquisition_scores: Dict[str, float]
    model_metrics: Dict[str, Any] = Field(default_factory=dict)
    iteration: int


class LLMExperimentRequest(BaseModel):
    """Request for LLM-based experiment simulation."""
    molecules: List[str]  # SMILES
    properties: List[str]  # Properties to evaluate
    context: Optional[str] = None  # Additional context for the LLM
    use_react: bool = True  # Whether to use ReACT pattern


class LLMExperimentResult(BaseModel):
    """Result from LLM experiment."""
    results: Dict[str, Dict[str, float]]  # SMILES -> {property: value}
    reasoning: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)