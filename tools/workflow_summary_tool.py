from typing import Optional, Type, Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class WorkflowSummaryInput(BaseModel):
    """Input schema for the WorkflowSummary tool."""
    include_all_molecules: bool = Field(
        default=False,
        description="Whether to include all enumerated molecules in the summary."
    )
    primary_metric: Optional[str] = Field(
        default=None,
        description="Primary metric to evaluate improvement and 'best' molecule. If not provided, chosen automatically."
    )
    highlight_properties: Optional[List[str]] = Field(
        default=None,
        description="Subset of properties to display first in listings."
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
        primary_metric: Optional[str] = None,
        highlight_properties: Optional[List[str]] = None,
        memory: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generates a comprehensive workflow summary."""
        if memory is None:
            memory = {}

        def numeric(v):
            return isinstance(v, (int, float))

        summary = "=" * 60 + "\n"
        summary += "    MOLECULE DISCOVERY WORKFLOW SUMMARY\n"
        summary += "=" * 60 + "\n\n"

        # Enumeration
        all_molecules = memory.get('enumerated_molecules', {})
        summary += f"1. ENUMERATION PHASE\n"
        summary += f"   Total molecules enumerated: {len(all_molecules)}\n"
        if include_all_molecules and all_molecules:
            summary += "\n   All Enumerated Molecules:\n"
            for mol_id, smiles in all_molecules.items():
                summary += f"   - {mol_id}: {smiles}\n"
        summary += "\n"

        first_recs = memory.get('first_bo_recommendations', [])
        first_char = memory.get('first_characterization_results', {})
        final_recs = memory.get('bo_recommendations', [])
        final_char = memory.get('characterization_results', {})

        first_props = set()
        for v in first_char.values():
            first_props.update(v.keys())
        final_props = set()
        for v in final_char.values():
            final_props.update(v.keys())
        all_props = sorted(first_props.union(final_props))

        ordered_props = []
        if highlight_properties:
            ordered_props.extend([p for p in highlight_properties if p in all_props])
        ordered_props.extend([p for p in all_props if p not in ordered_props])
        
        summary += f"2. FIRST BAYESIAN OPTIMIZATION (Initial)\n"
        summary += f"   Molecules recommended: {len(first_recs) if first_recs else 'N/A'}\n"
        if first_recs and first_char:
            summary += "\n   First Round Recommendations:\n"
            for mol_id in first_recs:
                smiles = all_molecules.get(mol_id, "N/A")
                props = first_char.get(mol_id, {})
                line_props = ", ".join(f"{p}={props[p]}" for p in ordered_props if p in props)
                summary += f"   - {mol_id}: {smiles} ({line_props})\n"
        summary += "\n"

        # Second round Results
        final_recs = memory.get('bo_recommendations', [])
        final_char = memory.get('characterization_results', {})
        
        summary += f"3. SECOND BAYESIAN OPTIMIZATION (Informed)\n"
        summary += f"   Final molecules recommended: {len(final_recs) if final_recs else 'N/A'}\n"
        
        if final_recs and final_char:
            summary += "\n   Final Recommendations:\n"
            for mol_id in final_recs:
                smiles = all_molecules.get(mol_id, "N/A")
                props = final_char.get(mol_id, {})
                line_props = ", ".join(f"{p}={props[p]}" for p in ordered_props if p in props)
                summary += f"   - {mol_id}: {smiles} ({line_props})\n"
        summary += "\n"

        # 4. Improvement Analysis
        summary += "4. OPTIMIZATION IMPROVEMENT ANALYSIS\n"
        improvements = {}
        if first_char and final_char:
            for prop in all_props:
                first_vals = [v[prop] for v in first_char.values() if prop in v and numeric(v[prop])]
                final_vals = [v[prop] for v in final_char.values() if prop in v and numeric(v[prop])]
                if first_vals and final_vals:
                    avg_first = sum(first_vals) / len(first_vals)
                    avg_final = sum(final_vals) / len(final_vals)
                    diff = avg_final - avg_first
                    improvements[prop] = (avg_first, avg_final, diff)
                
            if improvements:
                summary += "   Average property changes:\n"
                for prop, (a1, a2, d) in sorted(improvements.items(), key=lambda x: x[0]):
                    summary += f"   - {prop}: first={a1:.3f}, final={a2:.3f}, Δ={d:+.3f}\n"
            else:
                summary += "   No numeric overlapping properties to compare.\n"
        summary += "\n"

        if not primary_metric:
            if improvements:
                # Pick property with largest absolute improvement
                primary_metric = max(improvements.items(), key=lambda x: abs(x[1][2]))[0]
            else:
                primary_metric = None


        # 5. Best Molecule
        all_characterized = {}
        if first_char:
            all_characterized.update(first_char)
        if final_char:
            all_characterized.update(final_char)
            
        if all_characterized and primary_metric:
            best_candidates = [(mid, vals.get(primary_metric)) for mid, vals in all_characterized.items()
                               if primary_metric in vals and numeric(vals[primary_metric])]
            if best_candidates:
                best_id, _ = max(best_candidates, key=lambda x: x[1])
                best_props = all_characterized[best_id]
                best_smiles = all_molecules.get(best_id, "N/A")
                summary += "5. BEST MOLECULE (Primary Metric)\n"
                summary += f"   Primary Metric: {primary_metric}\n"
                summary += f"   ID: {best_id}\n"
                summary += f"   SMILES: {best_smiles}\n"
                summary += f"   {primary_metric}: {best_props.get(primary_metric)}\n"
                summary += f"   Found in: {'Final Round' if best_id in final_char else 'First Round'}\n"
        elif all_characterized:
            summary += "5. BEST MOLECULE (Primary Metric)\n"
            summary += "   No primary metric provided or no numeric properties found.\n"

        summary += "\n" + "=" * 60

        memory['workflow_summary'] = summary

        return summary

    async def _arun(self, **kwargs):
        raise NotImplementedError("WorkflowSummary does not support async")
