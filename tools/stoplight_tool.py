import io
import requests
import json
from typing import Optional, Type, Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import pandas as pd
import numpy as np



class StoplightToolInput(BaseModel):
    """Input for the Stoplight API tool."""
    molecule_ids: Optional[List[str]] = Field(default=None, description="A list of molecule IDs to analyze. If not provided, the tool will use 'bo_recommendations' from memory.")
    precision: int = Field(default=2, description="Number of decimal places for numerical results.")


class StoplightTool(BaseTool):
    name: str = Field(default="Stoplight")
    description: str = Field(default="""
    Calculates and reports molecular properties for a list of molecules using an external API.
    It retrieves molecule IDs and their SMILES from memory, queries the API, and stores the results back in memory.
    """)
    args_schema: Type[BaseModel] = StoplightToolInput
    api_url: str = "https://stoplight.mml.unc.edu/smiles-csv"

    def _run(self, molecule_ids: Optional[List[str]] = None, precision: int = 2, memory: Optional[Dict[str, Any]] = None) -> str:
        """Run molecule property calculation for a list of molecules via API."""
        if memory is None:
            memory = {}

        ids_to_process = molecule_ids
        if not ids_to_process:
            if 'bo_recommendations' in memory:
                ids_to_process = memory['bo_recommendations']
                print(f"Using 'bo_recommendations' from memory: {ids_to_process}")
            else:
                return "Error: No molecule IDs provided. Please provide 'molecule_ids' or ensure 'bo_recommendations' is in memory."
        
        if not isinstance(ids_to_process, list):
            return f"Error: molecule_ids must be a list, but got {type(ids_to_process).__name__}."
        
        # Retrieve the full molecule dictionary from memory
        all_molecules = memory.get('enumerated_molecules', {})
        if not all_molecules:
            return "Error: 'enumerated_molecules' not found in memory. Cannot retrieve SMILES strings."

        smiles_list = []
        id_map = []
        for mol_id in ids_to_process:
            smiles = all_molecules.get(mol_id)
            if smiles:
                smiles_list.append(smiles)
                id_map.append(mol_id)
            else:
                print(f"Warning: SMILES not found for molecule ID: {mol_id}")

        if not smiles_list:
            return "Error: No valid SMILES found for the provided molecule IDs."
        
        payload = self._build_payload(smiles, precision)

        try:
            print("Sending batch request to Stoplight API...")
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            response = requests.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            results = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
        except requests.exceptions.RequestException as e:
            return f"API error during batch request: {e}"
        except json.JSONDecodeError:
            return "Failed to decode API batch response."
        
        if 'characterization_results' not in memory:
            memory['characterization_results'] = {}
        
        processed_mols = 0
        for i, row in results.iterrows():
            if i >= len(id_map):
                break
            mol_id = id_map[i]
            
            # Process and filter data for the current molecule
            stoplight_data = {k: float(v.item()) if isinstance(v, np.generic) else v for k, v in row.drop('SMILES').items()}
            stoplight_data = {k: v for k, v in stoplight_data.items() if isinstance(v, (int, float))}
            
            if "HBD" in stoplight_data:
                # Update general characterization results
                if mol_id not in memory['characterization_results']:
                    memory['characterization_results'][mol_id] = {}
                memory['characterization_results'][mol_id].update(stoplight_data)
                processed_mols += 1

                # If these are the first recommendations, store them separately
                if 'first_bo_recommendations' in memory and ids_to_process == memory.get('first_bo_recommendations'):
                    if 'first_characterization_results' not in memory:
                        memory['first_characterization_results'] = {}
                    if mol_id not in memory['first_characterization_results']:
                        memory['first_characterization_results'][mol_id] = {}
                    memory['first_characterization_results'][mol_id].update(stoplight_data)
        
        summary = f"Successfully retrieved and merged Stoplight data for {processed_mols} molecules."
        return summary


    def _build_payload(self, smiles: str, precision: int) -> Dict[str, Any]:
        """Helper to construct the API payload."""
        return {
            "smiles": smiles,
            "options": {
                "ALogP": True, "AmpC β-lactamase aggregation": True,
                "CNS Activity": True, "Cysteine protease cruzain aggregation": True,
                "FSP3": True, "Firefly Luciferase interference": True, "HBA": True, "HBD": True,
                "Molecular Weight": True, "Nano Luciferase interference": True, "Num Heavy Atoms": True,
                "Num Saturated Quaternary Carbons": True, "Number of Rings": True,
                "Number of Rotatable Bonds": True,"Polar Surface Area": True,"Redox interference": True,
                "Solubility in Water (mg/L)": True,"Thiol interference": True,
                "precision": str(precision)
            }
        }

    async def _arun(self, **kwargs) -> str:
        """Async implementation is not supported for this tool."""
        # For a true async version, HTTP requests should be made with an async library like aiohttp.
        raise NotImplementedError("StoplightTool does not support async execution.")


# Example usage (adapted for the new structure)
if __name__ == "__main__":
    # Create the tool
    stoplight_tool = StoplightTool()

    # Setup mock memory
    mock_memory = {
        'enumerated_molecules': {
            "mol_1": "CC(=O)OC1=CC=CC=C1C(=O)O", # Aspirin
            "mol_2": "CCO"  # Ethanol
        },
        'bo_recommendations': ["mol_1", "mol_2", "mol_not_found"]
    }

    try:
        # Use the tool, which will get IDs from memory
        summary_message = stoplight_tool.run(memory=mock_memory)

        # Print summary and results from memory
        print(summary_message)
        print("\nResults stored in memory:")
        print("-" * 30)
        print(json.dumps(mock_memory.get('stoplight_results', {}), indent=2))

    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
