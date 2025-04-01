from typing import Optional, Type, Dict
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from rdkit import Chem
from rdkit.Chem import Descriptors, AllChem


class MoleculeInput(BaseModel):
    """Input for molecule characterization."""
    smiles: str = Field(..., description="SMILES string representation of the molecule")


class MoleculeCharacterizationTool(BaseTool):
    name: str = Field(default="molecule_characterization")
    description: str = Field(default="""
    Analyzes a molecule's properties given its SMILES representation.
    Input should be a valid SMILES string.
    Returns various molecular properties including molecular weight, LogP, and structural features.
    """)
    args_schema: Type[BaseModel] = MoleculeInput

    def _run(self, smiles: str) -> Dict[str, float | str | int]:
        """Run molecule characterization"""
        # Create RDKit molecule object
        mol = Chem.MolFromSmiles(smiles)

        if mol is None:
            raise ValueError("Invalid SMILES string provided")

        # Calculate 3D coordinates for the molecule
        mol = Chem.AddHs(mol)  # Add hydrogen atoms
        AllChem.EmbedMolecule(mol, randomSeed=42)
        AllChem.MMFFOptimizeMolecule(mol)  # Energy minimize the structure

        # Calculate basic properties
        properties = {
            "Molecular Formula": Chem.rdMolDescriptors.CalcMolFormula(mol),
            "Exact Mass": round(Descriptors.ExactMolWt(mol), 2),
            "Molecular Weight": round(Descriptors.MolWt(mol), 2),
            "Heavy Atom Count": Descriptors.HeavyAtomCount(mol),
            "Ring Count": Descriptors.RingCount(mol),
            "Aromatic Ring Count": Descriptors.NumAromaticRings(mol),
            "H-Bond Donors": Descriptors.NumHDonors(mol),
            "H-Bond Acceptors": Descriptors.NumHAcceptors(mol),
            "Rotatable Bonds": Descriptors.NumRotatableBonds(mol),
            "LogP": round(Descriptors.MolLogP(mol), 2),
            "TPSA": round(Descriptors.TPSA(mol), 2),
            "QED" : round(Descriptors.qed(mol), 2),
        }

        properties_dict = {}
        data = {}
        for property_name, value in properties.items():
            if value is not None:
                prop_data = {
                    "value": value,
                    "confidence": 1.0
                }
            properties_dict[property_name] = prop_data

        data['molProperties'] = properties_dict

        return data

    async def _arun(self, smiles: str) -> Dict[str, float | str | int]:
        """Async implementation of molecule characterization"""
        return self._run(smiles)


# Example usage
if __name__ == "__main__":
    # Create the tool
    molecule_tool = MoleculeCharacterizationTool()

    # Example with aspirin
    test_smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"

    try:
        # Use the tool
        results = molecule_tool.run(test_smiles)

        # Print results
        print("Molecular Characterization Results:")
        print("-" * 30)
        for property_name, value in results.items():
            print(f"{property_name}: {value}")

    except Exception as e:
        print(f"Error: {str(e)}")