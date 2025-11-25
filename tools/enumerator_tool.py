import healer.healer.utils.rdkit_monkey_patch as rdkit_monkey_patch
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, List, Dict, Any, Union, Optional, Tuple
from rdkit import Chem

from healer.application.healer import MoleculeHEALER
from schemas.errors import ToolError

class EnumeratorInput(BaseModel):
    """Input schema for the EnumeratorTool."""
    molecule: str = Field(..., description="SMILES string of the molecule to enumerate.")
    enumerator: Optional[Any] = Field(default=" MoleculeHEALER", description="Enumeration method to use (default: ' MoleculeHEALER').")
    n_compositions: Optional[int] = Field(default=10, description="Number of compositions to enumerate (default: 10).")
    sim_threshold: Optional[float] = Field(default=None, description="Similarity threshold for filtering results (default: 0.5).")
    reaction_tags: Optional[List[str]] = Field(default=None, description="List of reaction tags for enumeration (uses a default set if not provided).")
    bb_supplier: Optional[str] = Field(default="EU_stock", description="Building blocks source (default: 'EU_stock').")
    custom_comp_sites: Optional[List[Tuple]] = Field(default=None, description="Custom composition sites for splitting the molecule (default: empty list).")
    max_bbs_per_comp: Optional[int] = Field(default=5, description="Maximum number of building blocks per composition.")
    verbose: Optional[int] = Field(default=0, description="Verbosity level for the enumeration process.")


class EnumeratorTool(BaseTool):
    """
    Enumerates a library of new molecules based on a starting molecule,
    using specified reactions and building blocks. Returns a dictionary
    mapping molecule IDs to their SMILES strings.
    """
    name: str = "Enumerator"
    enumerator: Optional[Any] = Field(default=" MoleculeHEALER", description="Enumeration method to use (default: ' MoleculeHEALER').")
    description: str = Field(default="Enumerates new molecules from a starting compound using chemical reactions.")
    args_schema: Type[BaseModel] = EnumeratorInput

    def __init__(self):
        super().__init__()
        self.enumerator = MoleculeHEALER(
            bb_supplier="test",     # Building blocks source
            reaction_tags='all',        # List of reaction tags to consider, keep 'all' for all reactions
            max_evals_per_comp=700,     # Maximum evaluations per composition
            sim_threshold=0.1,          # Similarity threshold for filtering building blocks
            max_bbs_per_comp=10 ,       # Limit the maximum number of filtered BBs per composition, 10 is a good trade-off
            verbose=2                   # Verbosity level for the enumeration process
        )

    def _run(
        self,
        molecule: str,
        n_compositions: Optional[int] = 50,
        max_evals_per_comp: Optional[int] = 1,
        randomize_compositions: Optional[bool] = True,
        random_seed: Optional[int] = 42,
        custom_split_sites: Optional[List[Tuple]] = None,
        retro_tree_depth: Optional[int] = 2,
        min_frag_size: Optional[int] = 2,
    ) -> Union[Dict[str, str], str]:
        try:
            self.enumerator.set_query_mol(
                query_mol=molecule,
                n_compositions=n_compositions,
                randomize_compositions=randomize_compositions,
                random_seed=random_seed,
                custom_split_sites=custom_split_sites,
                retro_tree_depth=retro_tree_depth,
                min_frag_size=min_frag_size
            )

            self.enumerator.enumerate(
                optimizer=None,
                max_evals_per_comp= n_compositions * max_evals_per_comp
                )
            
            results_df = self.enumerator.get_results(calc_similarity=True)
            results_df.drop_duplicates(subset='Product', inplace=True)

            if results_df.empty:
                return "No molecules were generated that met the criteria."

            enumerated_molecules_list = results_df['Product'].to_list()

            if not enumerated_molecules_list:
                return "No molecules were generated that met the criteria."

            # Format the output as a dictionary of {id: smiles}
            enumerated_molecules_dict = {f"mol_{i}": smi for i, smi in enumerate(enumerated_molecules_list)}

            # Format the output as a dictionary of {id: smiles}
            enumerated_molecules_dict = {f"mol_{i}": smi for i, smi in enumerate(enumerated_molecules_list)}
            print(f"EnumeratorTool generated {len(enumerated_molecules_dict)} molecules.")
            return enumerated_molecules_dict

        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Error in EnumeratorTool: {e}", tool="Enumerator", code="EXCEPTION")
    
    @staticmethod
    def validate_smiles(smiles_string) -> bool:
        """Return True iff smiles parses and sanitizes, False otherwise (no exceptions)."""
        try:
            if not isinstance(smiles_string, str) or not smiles_string:
                return False
            # Avoid sanitization at parse time; sanitize explicitly with catchErrors
            mol = Chem.MolFromSmiles(smiles_string, sanitize=False)
            if mol is None:
                return False
            try:
                Chem.SanitizeMol(mol, catchErrors=True)
            except Exception:
                return False
            if '.' in smiles_string:
                try:
                    frags = Chem.GetMolFrags(mol, asMols=True)
                    if len(frags) > 1:
                        return False
                except Exception:
                    return False
            return True
        except Exception:
            return False


    async def _arun(self, **kwargs):
        raise NotImplementedError("EnumeratorTool does not support async")