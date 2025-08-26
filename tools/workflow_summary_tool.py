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
        all_molecules = memory.get('search_space') or memory.get('enumerated_molecules', {})
        summary += f"1. ENUMERATION PHASE\n"
        summary += f"   Total molecules enumerated: {len(all_molecules)}\n"
        if include_all_molecules and all_molecules:
            summary += "\n   All Enumerated Molecules:\n"
            for mol_id, smiles in all_molecules.items():
                summary += f"   - {mol_id}: {smiles}\n"
        summary += "\n"

        bo_rounds = memory.get('bo_rounds', [])
        
        summary += f"2. FIRST BAYESIAN OPTIMIZATION (Initial)\n"
        summary += f"   Total BO rounds executed: {len(bo_rounds)}\n\n"

        all_props = set()
        for r in bo_rounds:
            for props in r.get('characterization', {}).values():
                all_props.update(props.keys())
        all_props = sorted(all_props)
        ordered_props = []
        if highlight_properties:
            ordered_props.extend([p for p in highlight_properties if p in all_props])
        ordered_props.extend([p for p in all_props if p not in ordered_props])

        for r in bo_rounds:
            summary += f"   ROUND {r['round']}:\n"
            recs = r.get('recommendations', [])
            summary += f"     Recommendations: {len(recs)}\n"
            if recs:
                for mol_id in recs:
                    smiles = all_molecules.get(mol_id, "N/A")
                    props = r.get('characterization', {}).get(mol_id, {})
                    if props:
                        line_props = ", ".join(f"{p}={props[p]}" for p in ordered_props if p in props)
                        summary += f"       - {mol_id}: {smiles} ({line_props})\n"
                    else:
                        summary += f"       - {mol_id}: {smiles}\n"
            summary += "\n"

        
        summary += f"3. IMPROVEMENT ANALYSIS\n"
        improvements = {}
        
        if len(bo_rounds) >= 2:
            first_char = bo_rounds[0].get('characterization', {})
            last_char = bo_rounds[-1].get('characterization', {})
            for prop in all_props:
                first_vals = [v[prop] for v in first_char.values() if prop in v and numeric(v[prop])]
                last_vals = [v[prop] for v in last_char.values() if prop in v and numeric(v[prop])]
                if first_vals and last_vals:
                    avg_first = sum(first_vals) / len(first_vals)
                    avg_last = sum(last_vals) / len(last_vals)
                    improvements[prop] = (avg_first, avg_last, avg_last - avg_first)
            if improvements:
                for prop, (a1, a2, d) in sorted(improvements.items()):
                    summary += f"   - {prop}: round1={a1:.3f}, round{len(bo_rounds)}={a2:.3f}, Δ={d:+.3f}\n"
            else:
                summary += "   No overlapping numeric properties.\n"
        else:
            summary += "   Not enough rounds for comparison.\n"

        if not primary_metric:
            if improvements:
                primary_metric = max(improvements.items(), key=lambda x: abs(x[1][2]))[0]

        # 4. Improvement Analysis
        summary += "\n4. BEST MOLECULE\n"
        if primary_metric and bo_rounds:
            all_char = {}
            for r in bo_rounds:
                all_char.update(r.get('characterization', {}))
            candidates = [(mid, vals.get(primary_metric)) for mid, vals in all_char.items()
                          if primary_metric in vals and numeric(vals[primary_metric])]
            if candidates:
                best_id, _ = max(candidates, key=lambda x: x[1])
                best_round = None
                for r in bo_rounds:
                    if best_id in r.get('characterization', {}):
                        best_round = r['round']; break
                best_props = all_char[best_id]
                summary += f"   Primary Metric: {primary_metric}\n"
                summary += f"   ID: {best_id}\n"
                summary += f"   Round Discovered: {best_round}\n"
                summary += f"   {primary_metric}: {best_props.get(primary_metric)}\n"
            else:
                summary += "   No candidates with the primary metric.\n"
        else:
            summary += "   Primary metric not defined or no rounds characterized.\n"

        if not primary_metric:
            if improvements:
                # Pick property with largest absolute improvement
                primary_metric = max(improvements.items(), key=lambda x: abs(x[1][2]))[0]
            else:
                primary_metric = None

        summary += "\n" + "=" * 60

        memory['workflow_summary'] = summary

        return summary

    async def _arun(self, **kwargs):
        raise NotImplementedError("WorkflowSummary does not support async")
