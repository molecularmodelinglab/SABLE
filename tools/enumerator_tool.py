from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, List, Dict, Any, Union, Optional, Tuple
from rdkit import Chem

from enumeration.enumerator import MoleculeEnumerator
from schemas.errors import ToolError

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
    ) -> Union[Dict[str, str], str]:
        try:
            n_comps = n_compositions if n_compositions is not None else 10
            sim_thresh = sim_threshold if sim_threshold is not None else 0.1
            rxn_tags = reaction_tags if reaction_tags is not None else ['amide coupling', 'amide', 'C-N bond formation', 'C-N', 'alkylation', 'N-arylation', 'azole', 'amination']
            bb_source = building_blocks if building_blocks is not None else "test"
            custom_sites = custom_comp_sites if custom_comp_sites is not None else []
            
            print("Molecule to enumerate:", molecule)
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

            # Basic sanity: ensure Product column exists and has strings
            if 'Product' not in results_df.columns:
                raise ToolError("Enumerator did not return a 'Product' column.", tool="Enumerator", code="BAD_OUTPUT")

            results_df = results_df.dropna(subset=['Product']).copy()

            # Validate SMILES strictly; keep boolean only
            results_df['Valid'] = results_df['Product'].map(self.validate_smiles)
            results_df = results_df[results_df['Valid']]

            if results_df.empty:
                raise ToolError("No molecules were generated that met the criteria.", tool="Enumerator", code="NO_RESULTS")

            # Limit the number of molecules to n_compositions
            if len(results_df) > n_comps:
                limited_df = results_df.head(int(n_comps))
            else:
                limited_df = results_df
            enumerated_molecules_list = limited_df['Product'].tolist()

            if not enumerated_molecules_list:
                raise ToolError("No molecules were generated that met the criteria.", tool="Enumerator", code="NO_RESULTS")

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
            # Optional: reject multi-fragment molecules
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