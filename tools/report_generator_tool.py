from typing import Optional, Type, Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import json

class ReportGeneratorInput(BaseModel):
    """Input schema for the ReportGenerator tool."""
    final_recommendations_key: str = Field(
        "bo_recommendations",
        description="The key in memory where the final list of recommended molecule IDs is stored."
    )
    all_molecules_key: str = Field(
        "enumerated_molecules",
        description="The key in memory for the dictionary mapping molecule IDs to SMILES."
    )
    characterization_key: str = Field(
        "characterization_results",
        description="The key in memory for the characterization results."
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
        final_recommendations_key: str = "bo_recommendations",
        all_molecules_key: str = "enumerated_molecules",
        characterization_key: str = "characterization_results",
        highlight_properties: Optional[List[str]] = None,
        memory: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generates a summary report."""
        if memory is None:
            memory = {}

        final_recommendations = memory.get(final_recommendations_key)
        all_molecules = memory.get(all_molecules_key)
        characterization_results = memory.get(characterization_key)

        if not final_recommendations:
            return "Error: Final recommendations not found in memory."
        if not all_molecules:
            return "Error: All molecules not found in memory."
        
        report = "--- Molecule Discovery Final Report ---\n\n"
        report += f"Found {len(final_recommendations)} final recommended molecules:\n\n"


        all_props = set()
        if characterization_results:
            for v in characterization_results.values():
                all_props.update(v.keys())
    
        ordered_props = []
        if highlight_properties:
            ordered_props.extend([p for p in highlight_properties if p in all_props])
        ordered_props.extend([p for p in sorted(all_props) if p not in ordered_props])


        for mol_id in final_recommendations:
            smiles = all_molecules.get(mol_id, "N/A")
            report += f"- Molecule ID: {mol_id}\n"
            report += f"  SMILES: {smiles}\n"
            
            if characterization_results and mol_id in characterization_results:
                props = characterization_results[mol_id]
                report += "  Properties:\n"
                for prop in ordered_props:
                    if prop in props:
                        report += f"    - {prop}: {props[prop]}\n"
            report += "\n"

        report += "--- End of Report ---"
        
        return report

    async def _arun(self, **kwargs):
        raise NotImplementedError("ReportGenerator does not support async")
