from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, List, Dict, Any, Union, Optional, Tuple
from rdkit import Chem

from enumeration.enumerator import MoleculeEnumerator

class EnumeratorInput(BaseModel):
    """Input schema for the EnumeratorTool."""
    molecule: str = Field(..., description="SMILES string of the molecule to enumerate.")
    # Make fields with defaults optional in the schema
    n_compositions: Optional[int] = Field(default=10, description="Number of compositions to enumerate (default: 10).")
    sim_threshold: Optional[float] = Field(default=None, description="Similarity threshold for filtering results (default: 0.5).")
    reaction_tags: Optional[List[str]] = Field(default=None, description="List of reaction tags for enumeration (uses a default set if not provided).")
    building_blocks: Optional[str] = Field(default=None, description="Building blocks source (default: 'EU_stock').")
    custom_comp_sites: Optional[List[Tuple]] = Field(default=None, description="Custom composition sites for splitting the molecule (default: empty list).")


class EnumeratorTool(BaseTool):
    """
    Enumerates a library of new molecules based on a starting molecule,
    using specified reactions and building blocks. Returns a dictionary
    mapping molecule IDs to their SMILES strings.
    """
    name: str = "Enumerator"
    description: str = Field(default="Enumerates new molecules from a starting compound using chemical reactions.")
    args_schema: Type[BaseModel] = EnumeratorInput

    def _run(
        self,
        molecule: str,
        n_compositions: Optional[int] = None,
        sim_threshold: Optional[float] = None,
        reaction_tags: Optional[List[str]] = None,
        building_blocks: Optional[str] = None,
        custom_comp_sites: Optional[List[Tuple]] = None,
        memory: Optional[Dict[str, Any]] = None,
    ) -> Union[Dict[str, str], str]:
        """Use the tool."""
        if memory is None:
            # This can be a fallback or an error, depending on desired behavior.
            # For now, let's initialize it to avoid breaking the logic.
            memory = {}

        try:
            # Handle defaults internally
            n_comps = n_compositions if n_compositions is not None else 10
            sim_thresh = sim_threshold if sim_threshold is not None else 0.3
            rxn_tags = reaction_tags if reaction_tags is not None else ['amide coupling', 'amide', 'C-N bond formation', 'C-N', 'alkylation', 'N-arylation', 'azole', 'amination']
            bb_source = building_blocks if building_blocks is not None else "EU_stock"
            custom_sites = custom_comp_sites if custom_comp_sites is not None else []

            # Initialize the enumerator
            enumerator = MoleculeEnumerator(
                n_compositions=n_comps,
                molecule=molecule,
                building_blocks=bb_source,
                reaction_tags=rxn_tags,
                custom_comp_sites=custom_sites,
                sim_threshold=sim_thresh,
            )

            enumerator.enumerate()
            results_df = enumerator.get_results()

            if results_df.empty:
                return "No molecules were generated that met the criteria."

            # Limit the number of molecules to n_compositions
            limited_df = results_df.head(int(n_comps))
            enumerated_molecules_list = limited_df['Product'].to_list()

            if not enumerated_molecules_list:
                return "No molecules were generated that met the criteria."

            # Format the output as a dictionary of {id: smiles}
            enumerated_molecules_dict = {f"mol_{i}": smi for i, smi in enumerate(enumerated_molecules_list)}

            # Store the results in memory instead of returning them
            memory['enumerated_molecules'] = enumerated_molecules_dict
            
            summary_message = f"Successfully enumerated {len(enumerated_molecules_dict)} molecules and stored them in memory under the key 'enumerated_molecules'."
            
            return summary_message

        except Exception as e:
            return f"Error in EnumeratorTool: {e}"

    async def _arun(self, **kwargs):
        raise NotImplementedError("EnumeratorTool does not support async")