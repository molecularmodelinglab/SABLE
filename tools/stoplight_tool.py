from typing import Optional, Type, Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import requests
import json


class StoplightToolInput(BaseModel):
    """Input for Stoplight API tool.

    This tool ONLY analyzes existing molecules. It CANNOT create or modify molecules
    to achieve specific properties. It simply reports the calculated properties of
    the provided SMILES string.
    """
    smiles: str = Field(...,
                        description="SMILES string representation of the molecule to analyze (this tool cannot modify molecules)")
    precision: str = Field(default="2", description="Number of decimal places for numerical results")


class StoplightTool(BaseTool):
    name: str = Field(default="Stoplight")
    description: str = Field(default="""
    Calculates and reports molecular properties using an external API given a SMILES representation.

    IMPORTANT: This tool ONLY analyzes existing molecules and CANNOT create or modify molecules
    to achieve specific properties (like increasing ALogP or BBB permeability). It only reports
    the calculated properties of the provided molecule.

    Input should be a valid SMILES string.
    Returns various molecular properties including solubility, LogP, molecular weight, structural features, 
    and confidence values for predictions where available.

    Example usage:
    - To analyze methanol: Use SMILES string "CO"
    - To analyze aspirin: Use SMILES string "CC(=O)OC1=CC=CC=C1C(=O)O"
    """)
    args_schema: Type[BaseModel] = StoplightToolInput
    api_url: str = "https://stoplight.mml.unc.edu/smiles"

    def _run(self, tool_input: str = None, **kwargs) -> Dict[str, Any]:
        """Run molecule property calculation via API

        This function accepts a SMILES string and returns calculated properties.
        It CANNOT modify molecules or generate molecules with specific properties.
        It only reports properties of the given molecule.
        """
        # Handle both single argument and named arguments cases
        smiles = tool_input if tool_input is not None else kwargs.get('smiles')
        precision = kwargs.get('precision', "2")

        if smiles is None:
            raise ValueError("SMILES string must be provided either as a single argument or as 'smiles' parameter")

        payload = {
            "smiles": smiles,
            "options": {
                "ALogP": True,
                "AmpC β-lactamase aggregation": True,
                "BBB Permeability": True,
                "CACO2": True,
                "CNS Activity": True,
                "Cysteine protease cruzain aggregation": True,
                "FSP3": True,
                "Firefly Luciferase interference": True,
                "HBA": True,
                "HBD": True,
                "Hepatic Stability": True,
                "Microsomal Half-life Sub-cellular": True,
                "Microsomal Half-life Tissue": True,
                "Microsomal Intrinsic Clearance": True,
                "Molecular Weight": True,
                "Nano Luciferase interference": True,
                "Num Heavy Atoms": True,
                "Num Saturated Quaternary Carbons": True,
                "Number of Rings": True,
                "Number of Rotatable Bonds": True,
                "Oral Bioavailability": True,
                "Plasma Half-life": True,
                "Plasma Protein Binding": True,
                "Polar Surface Area": True,
                "Redox interference": True,
                "Renal Clearance": True,
                "Solubility in Water (mg/L)": True,
                "Thiol interference": True,
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

            # Transform molProperties format to include confidence values
            if 'molProperties' in data:
                properties_dict = {}
                for prop in data['molProperties']:
                    # Extract the property name, value, and confidence
                    prop_name = prop[0]
                    prop_value = prop[2]

                    # Create an object with value and confidence
                    prop_data = {
                        "value": prop_value,
                        "confidence": prop[3] if len(prop) > 3 and prop[3] else None
                    }

                    properties_dict[prop_name] = prop_data

                data['molProperties'] = properties_dict

            return data

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API request failed: {str(e)}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse API response: {str(e)}")

    async def _arun(self, tool_input: str = None, **kwargs) -> Dict[str, Any]:
        """Async implementation of molecule property calculation

        This function accepts a SMILES string and returns calculated properties.
        It CANNOT modify molecules or generate molecules with specific properties.
        """
        return self._run(tool_input=tool_input, **kwargs)


# Example usage
if __name__ == "__main__":
    # Create the tool
    molecule_tool = StoplightTool()

    # Test SMILES - Example of methanol
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

        # Example 3: How to access property values and confidence
        print("\nExample 3: Accessing specific properties and confidence values")
        if 'molProperties' in results2:
            props = results2['molProperties']
            if 'BBB Permeability' in props:
                bbb_value = props['BBB Permeability']['value']
                bbb_confidence = props['BBB Permeability']['confidence']
                print(f"BBB Permeability: {bbb_value} (Confidence: {bbb_confidence})")

            if 'ALogP' in props:
                alogp_value = props['ALogP']['value']
                print(f"ALogP: {alogp_value}")

    except Exception as e:
        print(f"Error: {str(e)}")
