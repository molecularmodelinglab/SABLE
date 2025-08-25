from typing import Optional, Type, Dict, List, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from rdkit import Chem
from rdkit.Chem import Descriptors
import json
import ast


class MoleculeCharacterizationInput(BaseModel):
    """Input for characterizing a list of molecules."""
    molecule_ids: Optional[List[str]] = Field(
        default=None, 
        description="A list of molecule IDs (e.g., [\"mol_1\", \"mol_2\"]) or a memory key (e.g., 'bo_recommendations'). If None, defaults to 'bo_recommendations' from memory."
    )

class MoleculeCharacterizationTool(BaseTool):
    name: str = Field(default="MoleculeCharacterizer")
    description: str = Field(default="""
    Analyzes properties for a list of molecules identified by their IDs.
    It retrieves SMILES from memory, calculates properties, and stores the results back in memory.
    """)
    args_schema: Type[BaseModel] = MoleculeCharacterizationInput

    def _run(self, molecule_ids: Optional[List[str]] = None, memory: Optional[Dict[str, Any]] = None) -> str:
        """Run molecule characterization for a list of molecules."""
        if memory is None:
            memory = {}

        ids_to_process = molecule_ids
        if not ids_to_process:
            if 'bo_recommendations' in memory:
                ids_to_process = memory['bo_recommendations']
                print(f"Using 'bo_recommendations' from memory")
            else:
                return "Error: No molecule IDs provided. Ensure 'bo_recommendations' is in memory."
            
        if not isinstance(ids_to_process, list):
            return f"Error: molecule_ids must be a list, but got {type(ids_to_process).__name__}."

        # Retrieve the full molecule dictionary from memory
        all_molecules = memory.get('enumerated_molecules', {})
        if not all_molecules:
            return "Error: 'enumerated_molecules' not found in memory. Cannot retrieve SMILES strings."
        
        if 'characterization_results' not in memory:
            memory['characterization_results'] = {}

        characterization_results = {}
        errors = []
        successful_count = 0
        for mol_id in ids_to_process:
            smiles = all_molecules.get(mol_id)
            if not smiles:
                errors.append(f"SMILES not found for molecule ID: {mol_id}")
                continue

            try:
                mol = Chem.MolFromSmiles(smiles)
                if mol is None:
                    errors.append(f"Invalid SMILES '{smiles}' for molecule ID: {mol_id}")
                    continue

                # Calculate properties
                properties = {
                    "Molecular_Formula": Chem.rdMolDescriptors.CalcMolFormula(mol),
                    "Exact_Mass": round(Descriptors.ExactMolWt(mol), 2),
                    "Molecular_Weight": round(Descriptors.MolWt(mol), 2),
                    "Heavy_Atom_Count": Descriptors.HeavyAtomCount(mol),
                    "Ring_Count": Descriptors.RingCount(mol),
                    "Aromatic_Ring_Count": Descriptors.NumAromaticRings(mol),
                    "H_Bond_Donors": Descriptors.NumHDonors(mol),
                    "H_Bond_Acceptors": Descriptors.NumHAcceptors(mol),
                    "Rotatable_Bonds": Descriptors.NumRotatableBonds(mol),
                    "LogP": round(Descriptors.MolLogP(mol), 2),
                    "TPSA": round(Descriptors.TPSA(mol), 2),
                    "QED": round(Descriptors.qed(mol), 2),
                }
                if mol_id not in memory['characterization_results']:
                    memory['characterization_results'][mol_id] = {}
                memory['characterization_results'][mol_id].update(properties)
                successful_count += 1
            except Exception as e:
                errors.append(f"Error characterizing molecule ID {mol_id}: {e}")

        # Store results in memorys
        if 'first_bo_recommendations' in memory and set(ids_to_process) == set(memory.get('first_bo_recommendations')):
            if 'first_characterization_results' not in memory:
                memory['first_characterization_results'] = {}
            # This part needs to merge too
            for mol_id, props in memory['characterization_results'].items():
                 if mol_id in ids_to_process:
                    if mol_id not in memory['first_characterization_results']:
                        memory['first_characterization_results'][mol_id] = {}
                    memory['first_characterization_results'][mol_id].update(props)
            summary = f"Successfully characterized and merged data for {successful_count} molecules (First Round)."
        else:
            summary = f"Successfully characterized and merged data for {successful_count} molecules."
        
        if errors:
            summary += f" Encountered {len(errors)} errors: {'; '.join(errors)}"
            
        return summary

    async def _arun(self, molecule_ids: Optional[List[str]] = None, memory: Optional[Dict[str, Any]] = None) -> str:
        """Async implementation of molecule characterization"""
        # This is a simplified async call, for a true async version, the RDKit calls might need to be run in a thread pool.
        return self._run(molecule_ids=molecule_ids, memory=memory)


# Example usage (adapted for the new structure)
if __name__ == "__main__":
    # Create the tool
    molecule_tool = MoleculeCharacterizationTool()

    # Setup mock memory
    mock_memory = {
        'enumerated_molecules': {
            "mol_1": "CC(=O)OC1=CC=CC=C1C(=O)O", # Aspirin
            "mol_2": "CCO", # Ethanol
            "mol_invalid": "this is not smiles"
        },
        'bo_recommendations': ["mol_1", "mol_2", "mol_invalid", "mol_not_found"]
    }

    try:
        # Use the tool, which will get IDs from memory
        summary_message = molecule_tool.run(memory=mock_memory)

        # Print summary and results from memory
        print(summary_message)
        print("\nResults stored in memory:")
        print("-" * 30)
        import json
        print(json.dumps(mock_memory.get('characterization_results', {}), indent=2))

    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")