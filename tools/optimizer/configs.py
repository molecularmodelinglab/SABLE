import pandas as pd
from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field, field_validator
import json

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
        if v not in ["MAX", "MIN", "MATCH"]:
            raise ValueError("Mode must be either 'MAX', 'MIN', or 'MATCH'.")
        return v

class BayesianOptimizationInput(BaseModel):
    """Input schema for the Bayesian Optimization Tool."""
    targets: str = Field(..., description="A JSON string representing a list of optimization targets. Each target should be a dictionary with 'name' and 'mode' keys. Example: '[{\"name\": \"QED\", \"mode\": \"MAX\"}]'")
    batch_size: int = Field(default=5, description="Number of molecules to recommend in the next batch.")
    encoding: str = Field(default="MORDRED", description="Molecular encoding strategy for SubstanceParameter (e.g., 'MORDRED', 'RDKIT', 'MorganFP').")
    measurement_data: Optional[Union[List[Dict[str, Any]], str]] = Field(default=None, description="Optional list of dictionaries representing previous measurements OR a string key to retrieve measurement data from memory (e.g., 'measurement_data'). Each dict needs the search space ID key (e.g., 'Molecule_ID') and target keys.")
    search_space_id_column: Optional[str] = Field(default="Molecule_ID", description="The key/column name used for molecule IDs in the measurement_data.")

    @field_validator('batch_size')
    def coerce_batch_size_to_int(cls, v):
        return int(v)

    @field_validator('targets')
    def check_targets_non_empty(cls, v):
        if not v:
            raise ValueError("At least one target must be specified.")
        try:
            targets_list = json.loads(v)
            if not isinstance(targets_list, list) or not targets_list:
                raise ValueError("Targets must be a non-empty list.")
            for target in targets_list:
                if 'name' not in target or 'mode' not in target:
                    raise ValueError("Each target must have 'name' and 'mode' keys.")
        except json.JSONDecodeError:
            raise ValueError("Targets must be a valid JSON string.")
        return v
