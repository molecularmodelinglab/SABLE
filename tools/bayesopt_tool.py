import os
from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
import pandas as pd

from .optimizer.configs import BayesianOptimizationInput

try:
    from baybe import Campaign
    from baybe.objectives import SingleTargetObjective, DesirabilityObjective, ParetoObjective
    from baybe.parameters import SubstanceParameter
    from baybe.searchspace import SearchSpace
    from baybe.targets import NumericalTarget
    from baybe.utils.chemistry import get_canonical_smiles
    BAYBE_AVAILABLE = True
except ImportError:
    BAYBE_AVAILABLE = False
    # Define dummy classes if BayBE is not installed to avoid import errors
    class Campaign: pass
    class SingleTargetObjective: pass
    class MultiTargetObjective: pass
    class DesirabilityObjective: pass
    class SubstanceParameter: pass
    class SearchSpace: pass
    class NumericalTarget: pass
    def get_canonical_smiles(s): return s

class BayesianOptimizationTool(BaseTool):
    """
    A tool to perform Bayesian Optimization for molecule selection using the BayBE library.

    This tool sets up and runs a BayBE campaign based on specified targets,
    a search space of molecules (SMILES), and optional existing measurement data.
    It then recommends the next batch of molecules to evaluate according to the BO strategy.

    Requires the 'baybe' library to be installed.
    """
    name: str = "BayesianOptimizer"
    description: str = Field(default="Runs a Bayesian Optimization campaign using BayBE to recommend the next batch of molecules based on optimization targets, search space, and optional prior measurements. Returns a list of recommended molecule IDs.")
    args_schema: Type[BaseModel] = BayesianOptimizationInput

    def _check_baybe_installed(self):
        if not BAYBE_AVAILABLE:
            raise ImportError("The 'baybe' library is required for this tool but is not installed. Please install it using 'pip install baybe'.")

    def _run(
        self,
        targets: List[Any],
        batch_size: float,
        encoding: str,
        search_space_id_column: Optional[str] = "Molecule_ID",
        measurement_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        search_space: Optional[Dict[str, Any]] = None,
    ) -> Union[List[str], str]:
        try:
            canonical_search_space = {
                id_: get_canonical_smiles(smi) for id_, smi in search_space.items()
            }
            substance_param = SubstanceParameter(
                name=search_space_id_column, 
                data=canonical_search_space, 
                encoding=encoding,
                kwargs_fingerprint={"radius": 3, "fp_size": 2048, "count": True} if encoding == "ECFP" else {}
            )
            searchspace = SearchSpace.from_product(parameters=[substance_param])
            
            if len(targets) == 1:
                targets[0].transformation = None
            baybe_targets = [
                NumericalTarget(name=t.name, mode=t.mode, bounds=t.bounds, transformation=t.transformation)
                for t in targets
            ]

            if len(baybe_targets) == 1:
                objective = SingleTargetObjective(target=baybe_targets[0])
            else:
                weights = [t.weight for t in targets]
                if os.getenv("MULTI_OPT_TYPE", "desirability").lower() == "pareto":
                    objective = ParetoObjective(targets=baybe_targets)
                else:
                    objective = DesirabilityObjective(targets=baybe_targets, weights=weights)

            campaign = Campaign(searchspace=searchspace, objective=objective)

            if measurement_data:
                df_measurements = pd.DataFrame(measurement_data)
                campaign.add_measurements(df_measurements)

            recommendations_df = campaign.recommend(batch_size=int(batch_size))
            
            results = recommendations_df[search_space_id_column].tolist()

            return results

        except Exception as e:
            return f"Error during Bayesian Optimization process: {e}"

    async def _arun(self, **kwargs):
        # This tool does not support async execution
        raise NotImplementedError("BayesianOptimizationTool does not support async")
