import rdkit
from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem import AllChem
import matplotlib.pyplot as plt
import io


def visualize_smiles(smiles_list, grid_size=None, mols_per_row=3, sub_img_size=(300, 300)):
    """
    Visualize one or more SMILES strings as molecular structures.

    Parameters:
    -----------
    smiles_list : str or list
        Single SMILES string or list of SMILES strings
    grid_size : tuple, optional
        Size of the overall image (width, height)
    mols_per_row : int
        Number of molecules per row in the grid
    sub_img_size : tuple
        Size of each individual molecule image

    Returns:
    --------
    matplotlib figure
    """
    # Convert single SMILES to list
    if isinstance(smiles_list, str):
        smiles_list = [smiles_list]

    # Convert SMILES to molecules
    mols = []
    valid_smiles = []

    for idx, smiles in enumerate(smiles_list):
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is not None:
                # Generate 2D coordinates for the molecule
                AllChem.Compute2DCoords(mol)
                mols.append(mol)
                valid_smiles.append(smiles)
        except Exception as e:
            print(f"Error processing SMILES {smiles}: {str(e)}")

    if not mols:
        raise ValueError("No valid molecules to display")

    # Create the image
    if len(mols) == 1:
        img = Draw.MolToImage(mols[0], size=sub_img_size)
    else:
        img = Draw.MolsToGridImage(mols,
                                   legends=valid_smiles,
                                   molsPerRow=mols_per_row,
                                   subImgSize=sub_img_size,
                                   returnPNG=False)

    # Convert PIL image to matplotlib figure
    plt.figure(figsize=(10, 10) if grid_size is None else grid_size)
    plt.imshow(img)
    plt.axis('off')
    return plt.gcf()


# Example usage:
if __name__ == "__main__":
    # Single molecule example
    print("Displaying Aspirin...")
    aspirin = "CC(=O)OC1=CC=CC=C1C(=O)O"
    fig = visualize_smiles(aspirin)
    plt.show()  # Added this line

    # Multiple molecules example
    print("\nDisplaying multiple molecules...")
    molecules = [
        "CC(=O)OC1=CC=CC=C1C(=O)O",  # Aspirin
        "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",  # Caffeine
        "CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C"  # Testosterone
    ]

    fig = visualize_smiles(molecules)
    plt.show()  # Added this line

    # Close all figures at the end
    plt.close('all')