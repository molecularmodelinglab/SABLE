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

        for mol_id in final_recommendations:
            smiles = all_molecules.get(mol_id, "N/A")
            report += f"- Molecule ID: {mol_id}\n"
            report += f"  SMILES: {smiles}\n"
            
            if characterization_results and mol_id in characterization_results:
                properties = characterization_results[mol_id]
                qed_value = properties.get('QED', 'N/A')
                report += f"  QED Score: {qed_value}\n"
                report += "  Properties:\n"
                for key, value in properties.items():
                    report += f"    - {key}: {value}\n"
            report += "\n"

        report += "--- End of Report ---"
        
        return report

    async def _arun(self, **kwargs):
        raise NotImplementedError("ReportGenerator does not support async")
