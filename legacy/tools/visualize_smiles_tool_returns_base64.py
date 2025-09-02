from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Optional, List, Union, Type
import rdkit
from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem import AllChem
import io
import base64
from PIL import Image


class MoleculeVisualizerInput(BaseModel):
    """Inputs for molecule visualization"""
    smiles: Union[str, List[str]] = Field(
        description="SMILES string(s) of the molecule(s) to visualize"
    )
    mols_per_row: Optional[int] = Field(
        default=3,
        description="Number of molecules per row in the grid"
    )
    sub_img_size: Optional[tuple] = Field(
        default=(300, 300),
        description="Size of each individual molecule image"
    )


class MoleculeVisualizerTool(BaseTool):
    name: str = "molecule_visualizer"
    description: str = """
    Visualizes molecular structures from SMILES strings.
    Input can be a single SMILES string or a list of SMILES strings.
    Optionally accepts layout parameters.
    Returns a base64 encoded image string.
    """
    args_schema: Type[BaseModel] = MoleculeVisualizerInput

    def _process_image(self, img):
        """Convert PIL Image to base64 string"""
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return img_str

    def visualize_smiles(self, smiles_list, mols_per_row=3, sub_img_size=(300, 300)):
        """Core visualization function"""
        if isinstance(smiles_list, str):
            smiles_list = [smiles_list]

        mols = []
        valid_smiles = []

        for smiles in smiles_list:
            try:
                mol = Chem.MolFromSmiles(smiles)
                if mol is not None:
                    AllChem.Compute2DCoords(mol)
                    mols.append(mol)
                    valid_smiles.append(smiles) #Always use SMILES as legends
            except Exception as e:
                return f"Error processing SMILES {smiles}: {str(e)}"

        if not mols:
            return "No valid molecules to display"

        if len(mols) == 1:
            img = Draw.MolToImage(mols[0], size=sub_img_size)
        else:
            img = Draw.MolsToGridImage(
                mols,
                legends=valid_smiles,  # Use SMILES as legends
                molsPerRow=mols_per_row,
                subImgSize=sub_img_size,
                useSVG=False
            )

        return img

    def _run(self, smiles: Union[str, List[str]],
             mols_per_row: int = 3, sub_img_size: tuple = (300, 300)) -> str:
        """Run the tool"""
        try:
            img = self.visualize_smiles(smiles, mols_per_row, sub_img_size)
            if isinstance(img, str):
                return img
            return self._process_image(img)
        except Exception as e:
            return f"Error: {str(e)}"


# Example usage
if __name__ == "__main__":
    from langchain_openai import OpenAI
    from IPython.display import Image, display  # For Jupyter display

    # Initialize the tool
    molecule_tool = MoleculeVisualizerTool()

    # Test with single molecule
    result1 = molecule_tool.run({"smiles": "CC(=O)OC1=CC=CC=C1C(=O)O"})
    print("Single molecule visualization complete")
    display(Image(data=base64.b64decode(result1)))

    # Test with multiple molecules
    result2 = molecule_tool.run({
        "smiles": [
            "CC(=O)OC1=CC=CC=C1C(=O)O",  # Aspirin
            "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",  # Caffeine
            "CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C"  # Testosterone
        ]
    })
    print("Multiple molecule visualization complete")
    display(Image(data=base64.b64decode(result2)))
