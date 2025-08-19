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
        include_stoplight: bool = False
    ) -> str:
        """Extracts and formats measurement data."""
        if memory is None:
            memory = {}

        # Check for characterization results in both possible locations
        characterization_results = None
        if 'characterization_results' in memory:
            characterization_results = memory['characterization_results']
        elif 'first_characterization_results' in memory:
            characterization_results = memory['first_characterization_results']
        else:
            return "Error: No characterization results found in memory. The MoleculeCharacterizer must be run first."
        
        stoplight = memory.get('stoplight_results', {}) if include_stoplight else {}
        
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

        measurement_data: List[Dict[str, Any]] = []

        for mol_id, data in characterization_results.items():
            row = {id_column_name: mol_id}
            for prop in properties:
                if prop in data:
                    row[prop] = data[prop]
                elif include_stoplight and mol_id in stoplight and prop in stoplight[mol_id]:
                    row[prop] = stoplight[mol_id][prop]
                else:
                    return f"Error: Property '{prop}' not found for molecule ID '{mol_id}'."
            measurement_data.append(row)

        memory['measurement_data'] = measurement_data
        
        return f"Successfully extracted {properties} for {len(measurement_data)} molecules and stored in memory as 'measurement_data'."

    async def _arun(self, **kwargs):
        raise NotImplementedError("MeasurementExtractor does not support async")
