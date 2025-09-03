from typing import Optional, Type, Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import json

class ReportGeneratorInput(BaseModel):
    """Input schema for the ReportGenerator tool."""
    round_scope: str = Field(
        default="latest",
        description="Which BO rounds to include: 'latest', 'all', or a specific integer round number as string (e.g. '3')."
    )
    include_all_rounds_section: bool = Field(
        default=True,
        description="If True, adds a per-round summary section listing recommendations."
    )
    highlight_properties: Optional[List[str]] = Field(
        default=None,
        description="Subset of properties to highlight first in the report."
    )

class ReportGenerator(BaseTool):
    name: str = "ReportGenerator"
    description: str = """
    Generates a final report summarizing the molecule discovery workflow.
    It combines the final recommendations with their SMILES and characterization data.
    """
    args_schema: Type[BaseModel] = ReportGeneratorInput

    def _run(
        self,
        round_scope: str = "latest",
        include_all_rounds_section: bool = True,
        highlight_properties: Optional[List[str]] = None,
        memory: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generates a summary report."""
        if memory is None:
            memory = {}

        bo_rounds: List[Dict[str, Any]] = memory.get("bo_rounds", [])
        search_space: Dict[str, str] = memory.get("search_space")

        def is_num(v):
            try:
                float(v)
                return True
            except Exception:
                return False


        if bo_rounds:
            # Determine selected rounds
            if round_scope == "all":
                selected_rounds = bo_rounds
            elif round_scope == "latest":
                selected_rounds = [bo_rounds[-1]]
            else:
                try:
                    rnum = int(round_scope)
                    selected_rounds = [r for r in bo_rounds if r.get("round") == rnum]
                    if not selected_rounds:
                        return f"Error: Requested round {rnum} not found."
                except ValueError:
                    return f"Error: round_scope '{round_scope}' invalid. Use 'latest', 'all', or an integer."
            # Aggregate selected recommendations
            selected_ids = []
            for r in selected_rounds:
                selected_ids.extend(r.get("recommendations", []))
            # Unique order preserving
            seen = set()
            selected_ids = [x for x in selected_ids if not (x in seen or seen.add(x))]

            # Collect properties from selected rounds
            prop_set = set()
            for r in selected_rounds:
                for props in r.get("characterization", {}).values():
                    prop_set.update(props.keys())
        
            ordered_props: List[str] = []
            if highlight_properties:
                ordered_props.extend([p for p in highlight_properties if p in prop_set])
            ordered_props.extend([p for p in sorted(prop_set) if p not in ordered_props])

            report = []
            report.append("--- Molecule Discovery Report ---")
            report.append("")
            report.append(f"Search Space Size: {len(search_space)}")
            report.append(f"Total BO Rounds Executed: {len(bo_rounds)}")
            report.append(f"Reported Scope: {round_scope} (Rounds: {', '.join(str(r['round']) for r in selected_rounds)})")
            report.append("")

            if include_all_rounds_section:
                report.append("Section 1: Round Summaries")
                for r in bo_rounds:
                    recs = r.get("recommendations", [])
                    report.append(f"  - Round {r['round']}: {len(recs)} recommendations")
                report.append("")

            report.append("Section 2: Detailed Recommendations (Selected Scope)")
            if not selected_ids:
                report.append("  No recommendations in selected scope.")
            else:
                for r in selected_rounds:
                    report.append(f"  Round {r['round']}:")
                    recs = r.get("recommendations", [])
                    if not recs:
                        report.append("    (No recommendations)")
                        continue
                    for mid in recs:
                        smi = search_space.get(mid, "N/A")
                        props_src = r.get("characterization", {}).get(mid, {})
                        report.append(f"    - {mid}")
                        report.append(f"      SMILES: {smi}")
                        if props_src:
                            for p in ordered_props:
                                if p in props_src:
                                    report.append(f"      {p}: {props_src[p]}")
                    report.append("")
            # Improvement overview (first vs last round for overlapping props)
            report.append("Section 3: Improvement Overview (First vs Last Round)")
            if len(bo_rounds) >= 2:
                first = bo_rounds[0].get("characterization", {})
                last = bo_rounds[-1].get("characterization", {})
                shared_props = sorted(set(k for v in first.values() for k in v) |
                                      set(k for v in last.values() for k in v))
                any_line = False
                for p in shared_props:
                    f_vals = [v[p] for v in first.values() if p in v and is_num(v[p])]
                    l_vals = [v[p] for v in last.values() if p in v and is_num(v[p])]
                    if f_vals and l_vals:
                        any_line = True
                        f_avg = sum(float(x) for x in f_vals) / len(f_vals)
                        l_avg = sum(float(x) for x in l_vals) / len(l_vals)
                        report.append(f"  {p}: first_avg={f_avg:.3f} last_avg={l_avg:.3f} delta={l_avg - f_avg:+.3f}")
                if not any_line:
                    report.append("  No numeric overlap for improvement calculation.")
            else:
                report.append("  Not enough rounds for comparison.")
            # Best by primary metric guess (largest absolute delta)
            report.append("")
            report.append("Section 4: Best Candidate Heuristic")
            # Heuristic: choose property with largest |delta| if computed above, else skip
            best_metric = None
            best_delta = 0.0
            if len(bo_rounds) >= 2:
                first = bo_rounds[0].get("characterization", {})
                last = bo_rounds[-1].get("characterization", {})
                metrics = {}
                for p in ordered_props:
                    f_vals = [v[p] for v in first.values() if p in v and is_num(v[p])]
                    l_vals = [v[p] for v in last.values() if p in v and is_num(v[p])]
                    if f_vals and l_vals:
                        f_avg = sum(float(x) for x in f_vals) / len(f_vals)
                        l_avg = sum(float(x) for x in l_vals) / len(l_vals)
                        metrics[p] = abs(l_avg - f_avg)
                if metrics:
                    best_metric = max(metrics.items(), key=lambda x: x[1])[0]
            if best_metric:
                # Find top molecule in last round for that metric
                last_round = bo_rounds[-1]
                last_char = last_round.get("characterization", {})
                candidates = [(mid, vals.get(best_metric)) for mid, vals in last_char.items()
                              if best_metric in vals and is_num(vals[best_metric])]
                if candidates:
                    top_id, _ = max(candidates, key=lambda x: float(x[1]))
                    report.append(f"  Metric: {best_metric}")
                    report.append(f"  Best Molecule: {top_id}")
                    smi = search_space.get(top_id, "N/A")
                    report.append(f"  SMILES: {smi}")
                    props = last_char.get(top_id, {})
                    for p in ordered_props:
                        if p in props:
                            report.append(f"  {p}: {props[p]}")
                else:
                    report.append("  No candidates for best metric.")
            else:
                report.append("  No metric identified for best candidate selection.")

            report.append("")
            report.append("--- End of Report ---")
            final_report = "\n".join(report)
            memory['final_report'] = final_report
            return final_report

        
        memory['final_report'] = final_report
        return final_report
        
    

    async def _arun(self, **kwargs):
        raise NotImplementedError("ReportGenerator does not support async")
