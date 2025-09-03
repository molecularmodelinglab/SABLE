from typing import Optional, Type, Union, Dict, List, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import json

class MeasurementExtractorInput(BaseModel):
    """Input schema for the MeasurementExtractor tool."""
    properties_to_extract: str = Field(
        ..., 
        description="A JSON string of a list of property names to extract from the characterization results. Example: '[\"QED\"]'"
    )
    id_column_name: str = Field(
        "Molecule_ID",
        description="The name to use for the molecule ID column in the output."
    )
    include_stoplight: bool = Field(
        False,
        description="If True, will also look in 'stoplight_results' for missing properties."
    )
    round_scope: str = Field(
        "all",
        description="Which BO round(s) to pull from: 'all', 'latest', or a specific round number as string (e.g. '3')."
    )
    recommendations_only: bool = Field(
        False,
        description="If True, only include molecules that were recommendations in the selected round scope."
    )

class MeasurementExtractor(BaseTool):
    name: str = "MeasurementExtractor"
    description: str = """
    Extracts specified measurement data from 'characterization_results' in memory
    and formats it for use in the BayesianOptimizer tool. The formatted data is
    stored back in memory under the key 'measurement_data'.
    """
    args_schema: Type[BaseModel] = MeasurementExtractorInput

    def _run(
        self, 
        properties_to_extract: Union[List[str], str],
        id_column_name: str = "Molecule_ID",
        memory: Optional[Dict[str, Any]] = None,
        include_stoplight: bool = False,
        round_scope: str = "all",
        recommendations_only: bool = False,
    ) -> str:
        """Extracts and formats measurement data."""
        if memory is None:
            memory = {}

        bo_rounds: List[Dict[str, Any]] = memory.get('bo_rounds', [])
        stoplight = memory.get('stoplight_results', {}) if include_stoplight else {}

        if not bo_rounds:
            return "Error: No characterization data found. Run a characterization tool first."
        
        if isinstance(properties_to_extract, str):
            try:
                parsed = json.loads(properties_to_extract)
                if isinstance(parsed, list):
                    properties = parsed
                else:
                    return "Error: properties_to_extract string did not decode to a list."
            except (json.JSONDecodeError, ValueError) as e:
                return f"Error: Invalid format for properties_to_extract. {e}"
        else:
            properties = properties_to_extract

        if not properties:
            return "Error: properties_to_extract list is empty."
        if not all(isinstance(p, str) for p in properties):
            return "Error: All entries in properties_to_extract must be strings."

        selected_rounds: List[Dict[str, Any]] = []
        if round_scope == "all":
            selected_rounds = bo_rounds
        elif round_scope == "latest":
            if bo_rounds:
                selected_rounds = [bo_rounds[-1]]
        else:
            # numeric?
            try:
                rnum = int(round_scope)
                selected_rounds = [r for r in bo_rounds if r.get("round") == rnum]
            except ValueError:
                return f"Error: round_scope '{round_scope}' is not 'all', 'latest', or an integer."

        # Aggregate characterization dict
        aggregated: Dict[str, Dict[str, Any]] = {}

        if selected_rounds:
            for r in selected_rounds:
                for mid, props in r.get("characterization", {}).items():
                    if recommendations_only and mid not in r.get("recommendations", []):
                        continue
                    if mid not in aggregated:
                        aggregated[mid] = {}
                    aggregated[mid].update(props)

        if not aggregated:
            return "Error: No characterized molecules matched the selection criteria."

        measurement_data: List[Dict[str, Any]] = []

        for mol_id, data in aggregated.items():
            row = {id_column_name: mol_id}
            for prop in properties:
                if prop in data:
                    row[prop] = data[prop]
                elif include_stoplight and mol_id in stoplight and prop in stoplight[mol_id]:
                    row[prop] = stoplight[mol_id][prop]
                else:
                    return f"Error: Property '{prop}' not found for molecule ID '{mol_id}'."
            measurement_data.append(row)

        if not measurement_data:
            return "Error: No measurements extracted."

        memory['measurement_data'] = measurement_data
        # History log
        hist = memory.setdefault('measurement_data_history', [])
        hist.append({
            "round_scope": round_scope,
            "recommendations_only": recommendations_only,
            "properties": properties,
            "count": len(measurement_data)
        })

        scope_desc = f"round_scope={round_scope}"
        if recommendations_only:
            scope_desc += ", recommendations_only=True"

        return (f"Extracted properties {properties} for {len(measurement_data)} molecules "
                f"({scope_desc}); stored as 'measurement_data'.")

    async def _arun(self, **kwargs):
        raise NotImplementedError("MeasurementExtractor does not support async")
