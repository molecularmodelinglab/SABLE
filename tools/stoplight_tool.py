from typing import Optional, Type, Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import requests
import json


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
    api_url: str = "https://stoplight.mml.unc.edu/smiles"

    def _run(self, molecule_ids: Optional[List[str]] = None, precision: int = 2, memory: Optional[Dict[str, Any]] = None) -> str:
        """Run molecule property calculation for a list of molecules via API."""
        if memory is None:
            memory = {}

        # Determine the list of molecule IDs to process
        if molecule_ids:
            ids_to_process = molecule_ids
        elif 'bo_recommendations' in memory:
            ids_to_process = memory['bo_recommendations']
            print(f"Using 'bo_recommendations' from memory: {ids_to_process}")
        else:
            return "Error: No molecule IDs provided. Please provide 'molecule_ids' or ensure 'bo_recommendations' is in memory."

        # Retrieve the full molecule dictionary from memory
        all_molecules = memory.get('enumerated_molecules', {})
        if not all_molecules:
            return "Error: 'enumerated_molecules' not found in memory. Cannot retrieve SMILES strings."

        stoplight_results = {}
        errors = []

        for mol_id in ids_to_process:
            smiles = all_molecules.get(mol_id)
            if not smiles:
                errors.append(f"SMILES not found for molecule ID: {mol_id}")
                continue

            payload = self._build_payload(smiles, precision)
            
            try:
                headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
                response = requests.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                stoplight_results[mol_id] = response.json()
            except requests.exceptions.RequestException as e:
                errors.append(f"API error for molecule ID {mol_id}: {e}")
            except json.JSONDecodeError:
                errors.append(f"Failed to decode API response for molecule ID {mol_id}.")

        # Store results in memory
        memory['stoplight_results'] = stoplight_results
        
        summary = f"Successfully retrieved Stoplight data for {len(stoplight_results)} molecules. Results are in memory under 'stoplight_results'."
        if errors:
            summary += f" Encountered {len(errors)} errors: {'; '.join(errors)}"
            
        return summary

    def _build_payload(self, smiles: str, precision: int) -> Dict[str, Any]:
        """Helper to construct the API payload."""
        return {
            "smiles": smiles,
            "options": {
                "ALogP": True, "AmpC β-lactamase aggregation": True, "BBB Permeability": True,
                "CACO2": True, "CNS Activity": True, "Cysteine protease cruzain aggregation": True,
                "FSP3": True, "Firefly Luciferase interference": True, "HBA": True, "HBD": True,
                "Hepatic Stability": True, "Microsomal Half-life Sub-cellular": True,
                "Microsomal Half-life Tissue": True, "Microsomal Intrinsic Clearance": True,
                "Molecular Weight": True, "Nano Luciferase interference": True, "Num Heavy Atoms": True,
                "Num Saturated Quaternary Carbons": True, "Number of Rings": True,
                "Number of Rotatable Bonds": True, "Oral Bioavailability": True,
                "Plasma Half-life": True, "Plasma Protein Binding": True, "Polar Surface Area": True,
                "Redox interference": True, "Renal Clearance": True, "Solubility in Water (mg/L)": True,
                "Thiol interference": True, "precision": str(precision)
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
