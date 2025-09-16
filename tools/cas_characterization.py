import re
import requests
from typing import Optional, Type, Dict, List, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class CASCharacterizationInput(BaseModel):
    """Input for characterizing a list of molecules."""
    search_space: Dict[str, str] = Field(..., description="Mapping of molecule IDs to their CAS numbers.")
    ids_to_process: List[str] = Field(..., description="List of molecule IDs to process.")

class CASCharacterizationTool(BaseTool):
    name: str = Field(default="MoleculeCharacterizer")
    description: str = Field(default="""
    Analyzes properties for a list of molecules identified by their IDs.
    It retrieves SMILES from memory, calculates properties, and stores the results back in memory.
    """)

    args_schema: Type[BaseModel] = CASCharacterizationInput

    def _run(self, input: CASCharacterizationInput) -> Dict[str, Any]:
        results = {}
        for mol_id in input.ids_to_process:
            cas = input.search_space.get(mol_id)
            if cas:
                data = self.get_data(cas)
                if data:
                    results[mol_id] = data
        return results

    def get_data(cas_number: str) -> Optional[Dict[str, Any]]:
        url = f"https://commonchemistry.cas.org/api/detail?cas_rn={cas_number}"
        headers = {'Accept': 'application/json'}

        response = requests.get(url, headers=headers)
        json_data = response.json()

        cas_data = {}
        if 'error' in json_data:
            return None
        if 'experimentalProperties' not in json_data:
            return None
        for i in json_data['experimentalProperties']:
            if i['name'] == 'Boiling Point':
                cas_data['boiling_point'] = float(re.findall(r'[-+]?\d*\.\d+|\d+', i['property'])[0])
            if i['name'] == 'Melting Point':
                cas_data['melting_point'] = float(re.findall(r'[-+]?\d*\.\d+|\d+', i['property'])[0])
        return cas_data