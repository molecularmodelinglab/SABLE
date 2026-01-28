import healer.utils.rdkit_monkey_patch as rdkit_monkey_patch
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr
from typing import Type, List, Dict, Any, Union, Optional, Tuple
from rdkit import Chem

from healer import MoleculeHEALER, SiteHEALER, FragmentHEALER
from schemas.errors import ToolError

from healer.domain import get_repository

class EnumeratorInput(BaseModel):
    """Input schema for the EnumeratorTool."""
    molecule: str = Field(..., description="SMILES string of the molecule to enumerate.")
    healer_mode: Optional[Any] = Field(default="MoleculeHEALER", description="Enumeration mode to use (default: ' MoleculeHEALER').")
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
    description: str = Field(default="Enumerates new molecules from a starting compound using chemical reactions.")
    args_schema: Type[BaseModel] = EnumeratorInput
    healer_mode: str = Field(default="MoleculeHEALER", description="HEALER enumeration mode: MoleculeHEALER, FragmentHEALER, or SiteHEALER")
    
    # Use PrivateAttr to exclude enumerator from Pydantic field validation
    _enumerator: Any = PrivateAttr(default=None)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, healer_mode: str = "MoleculeHEALER", **kwargs):
        """
        Initialize HEALER with a specific mode: MoleculeHEALER, FragmentHEALER or SiteHEALER.
        """
        super().__init__(healer_mode=healer_mode, **kwargs)
        mode = self.healer_mode
        if mode == "MoleculeHEALER":
            self._enumerator = MoleculeHEALER(
                bb_source="US_stock",     # Building blocks source
                reaction_tags='all',        # List of reaction tags to consider, keep 'all' for all reactions
                sim_threshold=0.3,          # Similarity threshold for filtering building blocks
                verbose=2,                  # Verbosity level for the enumeration process
                max_bbs_per_frag=10,
                bb_repository=get_repository("US_stock")
            )
        elif mode == "FragmentHEALER":
            self._enumerator = FragmentHEALER(
                bb_source="US_stock",
                reaction_tags="all",
                max_bbs_per_frag=10,
                sim_threshold=0.3,
                verbose=2,
                bb_repository=get_repository("US_stock")
            )
        elif mode == "SiteHEALER":
            self._enumerator = SiteHEALER(
                bb_source="US_stock",
                reaction_tags="all",
                rules={
                    'MW': (0, 500),
                    'HBD': (0, 5),
                    'HBA': (0, 10),
                    'TPSA': (0, 200),
                    'RotB': (0, 10),
                    'Rings': (0, 10),
                    'ArRings': (0, 5),
                    'Chiral': (0, 5)
                },
                struct_rules=[],
                verbose=2,
                bb_repository=get_repository("US_stock")
            )
        else:
            raise ValueError(f"Invalid HEALER mode. Got {mode}, need one of MoleculeHEALER, FragmentHEALER or SiteHEALER")

    def _run(
        self,
        molecule: str,
        n_compositions: Optional[int] = 100,
        max_evals_per_comp: Optional[int] = 50_000,
        randomize_compositions: Optional[bool] = False,
        random_seed: Optional[int] = 42,
        custom_split_sites: Optional[List[Tuple]] = None,
        retro_tree_depth: Optional[int] = 2,
        min_frag_size: Optional[int] = 2,
        reactive_sites: Optional[List[int]] = None,
    ) -> Union[Dict[str, str], str]:
        try:
            if self.healer_mode == "MoleculeHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                    n_compositions=n_compositions,
                    randomize_compositions=randomize_compositions,
                    random_seed=random_seed,
                    custom_split_sites=custom_split_sites,
                    retro_tree_depth=retro_tree_depth,
                    min_frag_size=min_frag_size
                )
            elif self.healer_mode == "FragmentHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                )
            elif self.healer_mode == "SiteHEALER":
                self._enumerator.set_query_mol(
                    query_mol=molecule,
                    reactive_sites=reactive_sites,
                )

            self._enumerator.enumerate(
                optimizer=None,
                max_total_products=10_000,
                max_products_per_comp=1_000,
                max_evals_per_comp=max_evals_per_comp
                )
            
            results_df = self._enumerator.get_results(calc_similarity=True, calc_properties=False)
            results_df.drop_duplicates(subset='Product', inplace=True)

            results_df = results_df[results_df['Similarity_to_query'] >= 0.25]

            if results_df.empty:
                return "No molecules were generated that met the criteria."

            enumerated_molecules_list = results_df['Product'].to_list()

            if not enumerated_molecules_list:
                return "No molecules were generated that met the criteria."

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