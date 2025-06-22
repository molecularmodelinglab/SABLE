import pandas as pd
from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field, field_validator

from langchain.tools import BaseTool



class TargetInput(BaseModel):
    """Configuration for a single optimization target."""
    name: str = Field(..., description="Name of the property to optimize (must match measurement data column).")
    mode: str = Field(..., description="Optimization mode: 'MAX' for maximization, 'MIN' for minimization.")
    # Optional: weight and bounds for multi-objective desirability
    bounds: Optional[List[float]] = Field(default=None, description="Bounds for the target property (optional).")
    transformation: Optional[str] = Field(default=None, description="Transformation function for the target property (optional).")
    weight: float = Field(default=1.0, description="Weight for this target in multi-objective desirability (optional).")

    @field_validator('mode')
    def validate_mode(cls, v):
        if v not in ["MAX", "MIN"]:
            raise ValueError("Mode must be either 'MAX' or 'MIN'.")
        return v

class BayesianOptimizationInput(BaseModel):
    """Input schema for the Bayesian Optimization Tool."""
    targets: List[TargetInput] = Field(..., description="List of optimization targets (properties and modes).")
    search_space_smiles: Dict[str, str] = Field(..., description="Dictionary mapping unique IDs to SMILES strings for the search space.")
    batch_size: int = Field(default=5, description="Number of molecules to recommend in the next batch.")
    encoding: str = Field(default="MORDRED", description="Molecular encoding strategy for SubstanceParameter (e.g., 'MORDRED', 'RDKIT', 'MorganFP').")
    measurement_data: Optional[List[Dict[str, Any]]] = Field(default=None, description="Optional list of dictionaries representing previous measurements (like rows in a DataFrame). Each dict needs the search space ID key (e.g., 'Molecule_ID') and target keys.")
    search_space_id_column: str = Field(default="Molecule_ID", description="The key/column name used for molecule IDs in the search_space_smiles and measurement_data.")

    @field_validator('targets')
    def check_targets_non_empty(cls, v):
        if not v:
            raise ValueError("At least one target must be specified.")
        return v
