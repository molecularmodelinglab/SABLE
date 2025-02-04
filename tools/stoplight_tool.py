from typing import Optional, Type, Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import requests
import json


class StoplightToolInput(BaseModel):
    """Input for Stoplight API tool."""
    smiles: str = Field(..., description="SMILES string representation of the molecule")
    precision: str = Field(default="2", description="Number of decimal places for numerical results")


class StoplightTool(BaseTool):
    name: str = Field(default="Stoplight")
    description: str = Field(default="""
    Calculates molecular properties using an external API given a SMILES representation.
    Input should be a valid SMILES string.
    Returns various molecular properties including solubility, LogP, molecular weight, and structural features.
    """)
    args_schema: Type[BaseModel] = StoplightToolInput
    api_url: str = "https://stoplight.mml.unc.edu/smiles"

    def _run(self, tool_input: str = None, **kwargs) -> Dict[str, Any]:
        """Run molecule property calculation via API"""
        # Handle both single argument and named arguments cases
        smiles = tool_input if tool_input is not None else kwargs.get('smiles')
        precision = kwargs.get('precision', "2")

        if smiles is None:
            raise ValueError("SMILES string must be provided either as a single argument or as 'smiles' parameter")

        payload = {
            "smiles": smiles,
            "options": {
                "ALogP": True,
                "FSP3": True,
                "HBA": True,
                "HBD": True,
                "Molecular Weight": True,
                "Num Heavy Atoms": True,
                "Num Saturated Quaternary Carbons": True,
                "Number of Rings": True,
                "Number of Rotatable Bonds": True,
                "Polar Surface Area": True,
                "Solubility in Water (mg/L)": True,
                "precision": precision
            }
        }

        try:
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }

            response = requests.post(
                url=self.api_url,
                headers=headers,
                json=payload
            )

            response.raise_for_status()
            data = response.json()

            # Remove unwanted keys
            data.pop('stoplight', None)
            data.pop('svg', None)

            # Transform molProperties format
            if 'molProperties' in data:
                data['molProperties'] = {
                    prop[0]: prop[2] for prop in data['molProperties']
                }

            return data

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API request failed: {str(e)}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse API response: {str(e)}")

    async def _arun(self, tool_input: str = None, **kwargs) -> Dict[str, Any]:
        """Async implementation of molecule property calculation"""
        return self._run(tool_input=tool_input, **kwargs)


# Example usage
if __name__ == "__main__":
    # Create the tool
    molecule_tool = StoplightTool()

    # Test SMILES
    test_smiles = "CO"

    try:
        # Example 1: Using single argument (will be treated as tool_input)
        print("Example 1: Using single argument")
        results1 = molecule_tool.run(test_smiles)
        print(json.dumps(results1, indent=2))

        # Example 2: Using named arguments
        print("\nExample 2: Using named arguments")
        # The correct way to pass named arguments is to pass them as tool_input
        results2 = molecule_tool.run(tool_input=test_smiles, precision="2")
        print(json.dumps(results2, indent=2))

    except Exception as e:
        print(f"Error: {str(e)}")