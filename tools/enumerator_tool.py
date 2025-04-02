from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, List, Dict, Any, Union
from rdkit import Chem

from  enumeration.enumerator import MoleculeEnumerator

class EnumeratorInput(BaseModel):
    molecule: str = Field(..., description="SMILES string or RDKit Mol object of the molecule to enumerate.")
    building_blocks: str = Field(default="EU_stock", description="Building blocks source.")
    reaction_tags: list[str] = Field(default_factory=lambda: ['amide coupling', 'amide', 'C-N bond formation', 'C-N',
                                                              'alkylation', 'N-arylation', 'azole', 'amination'],
                                       description="List of reaction tags to consider for enumeration.")
    custom_comp_sites: list[tuple] = Field(default_factory=list, description="Custom composition sites for splitting the molecule.")
    n_compositions: int = Field(default=10, description="Number of compositions to enumerate.")
    sim_threshold: float = Field(default=0.5, description="Similarity threshold for filtering results.")


class EnumeratorTool(BaseTool):
    name: str = "Enumerator"
    description: str = "Enumerates a given molecule with building blocks based on specified reaction sites and rules."
    args_schema: Type[BaseModel] = EnumeratorInput

    def _run(self, tool_input: str = None, **kwargs) -> List[str]:
        """Enumerate molecules based on the provided SMILES and rules."""

        # Validate the SMILES string
        molecule = kwargs.get('molecule')
        if molecule is None and tool_input is not None:
            # If molecule not in kwargs but tool_input is provided, use tool_input as molecule
            molecule = tool_input
            
        # Validate the molecule is provided
        if molecule is None:
            return ["Error: No molecule provided. Please provide a SMILES string."]
        
        is_valid, error_message = self.validate_smiles(molecule)
        if not is_valid:
            return [f"Invalid SMILES: {error_message}"]

        # Process the enumeration
        results = self.enumerate_molecules(**kwargs)
        return results

    def enumerate_molecules(self, **kwargs) -> List[str]:
        """Perform enumeration based on the SMILES and specified rules."""

        molecule = kwargs.get('molecule')
        building_blocks = kwargs.get('building_blocks', "EU_stock")
        reaction_tags = kwargs.get('reaction_tags', ['amide coupling', 'amide', 'C-N bond formation', 'C-N',
                                                    'alkylation', 'N-arylation', 'azole', 'amination'])
        custom_comp_sites = kwargs.get('custom_comp_sites', [])
        n_compositions = kwargs.get('n_compositions', 10)
        sim_threshold = kwargs.get('sim_threshold', 0.3)

        enumerator = MoleculeEnumerator(
            molecule=molecule,
            building_blocks=building_blocks,
            reaction_tags=reaction_tags,
            custom_comp_sites=custom_comp_sites,
            n_compositions=n_compositions,
            sim_threshold=sim_threshold
        )
     
        enumerator.enumerate()
        enumerated_molecules = enumerator.get_results()
        return enumerated_molecules['Product'].to_list()
    
    def validate_smiles(self, smiles: str) -> Union[bool, str]:
        """Validate the SMILES string using RDKit."""
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return False, "Could not parse the SMILES string."
        return True, ""
    
    async def _arun(self, tool_input: str = None, **kwargs) -> List[str]:
        return self._run(tool_input=tool_input, **kwargs)