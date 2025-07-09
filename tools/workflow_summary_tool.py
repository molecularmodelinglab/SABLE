from typing import Optional, Type, Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class WorkflowSummaryInput(BaseModel):
    """Input schema for the WorkflowSummary tool."""
    include_all_molecules: bool = Field(
        default=False,
        description="Whether to include all enumerated molecules in the summary."
    )

class WorkflowSummary(BaseTool):
    name: str = "WorkflowSummary"
    description: str = """
    Generates a comprehensive summary of the entire molecule discovery workflow,
    including all evaluated molecules, optimization steps, and results comparison.
    """
    args_schema: Type[BaseModel] = WorkflowSummaryInput

    def _run(
        self,
        include_all_molecules: bool = False,
        memory: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generates a comprehensive workflow summary."""
        if memory is None:
            memory = {}

        summary = "=" * 60 + "\n"
        summary += "    MOLECULE DISCOVERY WORKFLOW SUMMARY\n"
        summary += "=" * 60 + "\n\n"

        # 1. Enumeration Summary
        all_molecules = memory.get('enumerated_molecules', {})
        summary += f"1. ENUMERATION PHASE\n"
        summary += f"   Total molecules enumerated: {len(all_molecules)}\n"
        
        if include_all_molecules and all_molecules:
            summary += "\n   All Enumerated Molecules:\n"
            for mol_id, smiles in all_molecules.items():
                summary += f"   - {mol_id}: {smiles}\n"
        summary += "\n"

        # 2. First Optimization Results
        first_recs = memory.get('first_bo_recommendations', [])
        first_char = memory.get('first_characterization_results', {})
        
        summary += f"2. FIRST BAYESIAN OPTIMIZATION (Initial)\n"
        summary += f"   Molecules recommended: {len(first_recs) if first_recs else 'N/A'}\n"
        
        if first_recs and first_char:
            summary += "\n   First Round Recommendations:\n"
            for mol_id in first_recs:
                smiles = all_molecules.get(mol_id, "N/A")
                props = first_char.get(mol_id, {})
                qed = props.get('QED', 'N/A')
                summary += f"   - {mol_id}: {smiles} (QED: {qed})\n"
        summary += "\n"

        # 3. Second Optimization Results
        final_recs = memory.get('bo_recommendations', [])
        final_char = memory.get('characterization_results', {})
        
        summary += f"3. SECOND BAYESIAN OPTIMIZATION (Informed)\n"
        summary += f"   Final molecules recommended: {len(final_recs) if final_recs else 'N/A'}\n"
        
        if final_recs and final_char:
            summary += "\n   Final Recommendations:\n"
            for mol_id in final_recs:
                smiles = all_molecules.get(mol_id, "N/A")
                props = final_char.get(mol_id, {})
                qed = props.get('QED', 'N/A')
                summary += f"   - {mol_id}: {smiles} (QED: {qed})\n"
        summary += "\n"

        # 4. Improvement Analysis
        summary += "4. OPTIMIZATION IMPROVEMENT ANALYSIS\n"
        
        if first_char and final_char:
            # Calculate average QED for both rounds
            first_qeds = [props.get('QED', 0) for props in first_char.values() if 'QED' in props]
            final_qeds = [props.get('QED', 0) for props in final_char.values() if 'QED' in props]
            
            if first_qeds and final_qeds:
                avg_first = sum(first_qeds) / len(first_qeds)
                avg_final = sum(final_qeds) / len(final_qeds)
                improvement = avg_final - avg_first
                
                summary += f"   Average QED (First Round): {avg_first:.3f}\n"
                summary += f"   Average QED (Final Round): {avg_final:.3f}\n"
                summary += f"   Improvement: {improvement:+.3f}\n"
                
                if improvement > 0:
                    summary += "   ✓ Bayesian optimization successfully improved QED scores!\n"
                else:
                    summary += "   ⚠ No improvement in average QED scores.\n"
        summary += "\n"

        # 5. Best Molecule Overall
        all_characterized = {}
        if first_char:
            all_characterized.update(first_char)
        if final_char:
            all_characterized.update(final_char)
            
        if all_characterized:
            best_mol = max(all_characterized.items(), key=lambda x: x[1].get('QED', 0))
            best_id, best_props = best_mol
            best_smiles = all_molecules.get(best_id, "N/A")
            best_qed = best_props.get('QED', 'N/A')
            
            summary += "5. BEST MOLECULE DISCOVERED\n"
            summary += f"   ID: {best_id}\n"
            summary += f"   SMILES: {best_smiles}\n"
            summary += f"   QED Score: {best_qed}\n"
            summary += f"   Found in: {'Final Round' if best_id in final_char else 'First Round'}\n"

        summary += "\n" + "=" * 60

        return summary

    async def _arun(self, **kwargs):
        raise NotImplementedError("WorkflowSummary does not support async")
