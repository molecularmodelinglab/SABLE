from typing import Optional, Type, Dict, List, Any
import time
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from rdkit import Chem
from rdkit.Chem import Descriptors

class MoleculeCharacterizationInput(BaseModel):
    """Input for characterizing a list of molecules."""
    search_space: Dict[str, str] = Field(..., description="Mapping of molecule IDs to their SMILES strings.")
    ids_to_process: List[str] = Field(..., description="List of molecule IDs to process.")

class MoleculeCharacterizationTool(BaseTool):
    name: str = Field(default="MoleculeCharacterizer")
    description: str = Field(default="""
    Analyzes properties for a list of molecules identified by their IDs.
    It retrieves SMILES from memory, calculates properties, and stores the results back in memory.
    """)
    args_schema: Type[BaseModel] = MoleculeCharacterizationInput

    def _run(self,
             search_space: Dict[str, str],
             ids_to_process: List[str],
        ) -> str:
        """Run molecule characterization for a list of molecules."""
        started_at = time.perf_counter()
        
        errors: List[str] = []
        results: Dict[str, Dict[str, float]] = {}
        
        print("Characterizing molecules with rdkit")
        for mol_id in ids_to_process:
            smi = search_space.get(mol_id)
            if not smi:
                errors.append(f"SMILES not found for {mol_id}")
                continue
            mol = Chem.MolFromSmiles(smi)
            if mol is None:
                errors.append(f"Invalid SMILES for {mol_id}")
                continue
            try:
                props = {
                    "Molecular_Formula": Chem.rdMolDescriptors.CalcMolFormula(mol),
                    "Exact_Mass": round(Descriptors.ExactMolWt(mol), 4),
                    "Molecular_Weight": round(Descriptors.MolWt(mol), 2),
                    "Heavy_Atom_Count": Descriptors.HeavyAtomCount(mol),
                    "Ring_Count": Descriptors.RingCount(mol),
                    "Aromatic_Ring_Count": Descriptors.NumAromaticRings(mol),
                    "H_Bond_Donors": Descriptors.NumHDonors(mol),
                    "H_Bond_Acceptors": Descriptors.NumHAcceptors(mol),
                    "Rotatable_Bonds": Descriptors.NumRotatableBonds(mol),
                    "LogP": round(Descriptors.MolLogP(mol), 2),
                    "TPSA": round(Descriptors.TPSA(mol), 2),
                    "QED": round(Descriptors.qed(mol), 3),
                }

                results[mol_id] = props

            except Exception as e:
                errors.append(f"{mol_id}: {e}")

        elapsed = time.perf_counter() - started_at
        print(
            f"MoleculeCharacterizationTool processed {len(ids_to_process)} molecules "
            f"in {elapsed:.2f}s (success={len(results)}, errors={len(errors)})."
        )

        return results

    async def _arun(self, molecule_ids: Optional[List[str]] = None, memory: Optional[Dict[str, Any]] = None) -> str:
        """Async implementation of molecule characterization"""
        # This is a simplified async call, for a true async version, the RDKit calls might need to be run in a thread pool.
        return self._run(molecule_ids=molecule_ids, memory=memory)


