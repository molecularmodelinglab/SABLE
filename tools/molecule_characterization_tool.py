from typing import Optional, Type, Dict, List, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from rdkit import Chem
from rdkit.Chem import Descriptors
import json
import ast


class MoleculeCharacterizationInput(BaseModel):
    """Input for characterizing a list of molecules."""
    pass

class MoleculeCharacterizationTool(BaseTool):
    name: str = Field(default="MoleculeCharacterizer")
    description: str = Field(default="""
    Analyzes properties for a list of molecules identified by their IDs.
    It retrieves SMILES from memory, calculates properties, and stores the results back in memory.
    """)
    args_schema: Type[BaseModel] = MoleculeCharacterizationInput

    def _run(self, memory: Optional[Dict[str, Any]] = None) -> str:
        """Run molecule characterization for a list of molecules."""
        if memory is None:
            memory = {}

        ids_to_process = None
        if not ids_to_process:
            bo_rounds = memory.get('bo_rounds', [])
            if bo_rounds:
                ids_to_process = bo_rounds[-1].get('recommendations', [])
                if ids_to_process:
                    print(f"Using latest BO round ({bo_rounds[-1]['round']}) recommendations.")

        # if not ids_to_process:
        #     return "Error: No molecule IDs resolved. Provide 'molecule_ids' or run BayesianOptimizer first."
        if not isinstance(ids_to_process, list):
            return f"Error: molecule_ids must be a list, got {type(ids_to_process).__name__}."


        all_molecules = memory.get('search_space') or memory.get('enumerated_molecules', {})
        if not all_molecules:
            return "Error: 'search_space' not in memory. Run Enumerator first."

        memory.setdefault('characterization_results', {})

        bo_rounds = memory.get('bo_rounds', [])
        latest_round_for: Dict[str, Any] = {}
        if bo_rounds:
            for r in bo_rounds:
                for mid in r.get('recommendations', []):
                    latest_round_for[mid] = r

        success = 0
        errors: List[str] = []
        rounds_touched = set()

        for mol_id in ids_to_process:
            smi = all_molecules.get(mol_id)
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

                memory['characterization_results'].setdefault(mol_id, {}).update(props)
                # Round-specific
                r = latest_round_for.get(mol_id)
                if r:
                    r['characterization'].setdefault(mol_id, {}).update(props)
                    rounds_touched.add(r['round'])
                success += 1
            except Exception as e:
                errors.append(f"{mol_id}: {e}")

        if rounds_touched:
            summary = f"Characterized {success} molecules across BO rounds {sorted(rounds_touched)}."
        else:
            summary = f"Characterized {success} molecules (no BO round matched)."
        if errors:
            summary += f" {len(errors)} errors: " + "; ".join(errors[:5]) + ("..." if len(errors) > 5 else "")
        return summary

    async def _arun(self, molecule_ids: Optional[List[str]] = None, memory: Optional[Dict[str, Any]] = None) -> str:
        """Async implementation of molecule characterization"""
        # This is a simplified async call, for a true async version, the RDKit calls might need to be run in a thread pool.
        return self._run(molecule_ids=molecule_ids, memory=memory)


