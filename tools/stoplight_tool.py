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
    precision: int = Field(default=2, description="Number of decimal places for numerical results.")
    search_space: Optional[Dict[str, str]] = Field(default=None, description="Mapping of molecule IDs to their SMILES strings.")


class StoplightTool(BaseTool):
    name: str = Field(default="Stoplight")
    description: str = Field(default="""
    Calculates and reports molecular properties for a list of molecules using an external API.
    It retrieves molecule IDs and their SMILES from memory, queries the API, and stores the results back in memory.
    """)
    args_schema: Type[BaseModel] = StoplightToolInput
    api_url: str = "https://stoplight.mml.unc.edu/smiles-csv"

    def _run(self, 
             precision: int = 2, 
             search_space: Optional[Dict[str, str]] = None,
             ids_to_process: Optional[List[str]] = None,
        ) -> str:
        """Run molecule property calculation for a list of molecules via API."""

        smiles_list = []
        id_map = []
        for mol_id in ids_to_process:
            smiles = search_space.get(mol_id)
            if smiles:
                smiles_list.append(smiles)
                id_map.append(mol_id)
            else:
                print(f"Warning: SMILES not found for molecule ID: {mol_id}")

        if not smiles_list:
            return "Error: No valid SMILES found for the provided molecule IDs."
        
        payload = self._build_payload(smiles_list, precision)

        try:
            print("Sending batch request to Stoplight API...")
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            response = requests.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            output = pd.read_csv(io.StringIO(response.content.decode('utf-8')))
        except requests.exceptions.RequestException as e:
            return f"API error during batch request: {e}"
        except json.JSONDecodeError:
            return "Failed to decode API batch response."
        
        results = {}
        for i, row in output.iterrows():
            if i >= len(id_map):
                break
            mol_id = id_map[i]
            
            # Process and filter data for the current molecule
            stoplight_data = {k: float(v.item()) if isinstance(v, np.generic) else v for k, v in row.drop('SMILES').items()}
            stoplight_data = {k: v for k, v in stoplight_data.items() if isinstance(v, (int, float))}
            
            if "HBD" in stoplight_data:
                results[mol_id] = stoplight_data
                
        return results

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


