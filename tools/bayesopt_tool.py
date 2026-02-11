import os
from re import match
from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
import pandas as pd

from .optimizer.configs import BayesianOptimizationInput

from baybe import Campaign
from baybe.objectives import SingleTargetObjective, DesirabilityObjective, ParetoObjective
from baybe.parameters import SubstanceParameter
from baybe.searchspace import SearchSpace
from baybe.targets import NumericalTarget
from baybe.acquisition import qUpperConfidenceBound
from baybe.utils.chemistry import get_canonical_smiles
BAYBE_AVAILABLE = True


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
                # kwargs_fingerprint={"radius": 3, "fp_size": 2048, "count": True} if encoding == "ECFP" else {}
                kwargs_fingerprint={"radius": 3, "fp_size": 2048} if encoding == "ECFP" else {}
            )
            searchspace = SearchSpace.from_product(parameters=[substance_param])
            
            if len(targets) == 1:
                targets[0].transformation = None
            # baybe_targets = [
            #     NumericalTarget(name=t.name, mode=t.mode, bounds=t.bounds, transformation=t.transformation)
            #     for t in targets
            # ]

            baybe_targets = []
            for t in targets:
                if t.mode == "MAX":
                    baybe_targets.append(NumericalTarget(name=t.name, minimize=False))
                elif t.mode == "MIN":
                    baybe_targets.append(NumericalTarget(name=t.name, minimize=True))
                elif t.mode == "MATCH":
                    baybe_targets.append(NumericalTarget.match_triangular(name=t.name, match_value=(t.bounds[0]+t.bounds[1])/2, cutoffs=(t.bounds[0], t.bounds[1]))) 
          

            if len(baybe_targets) == 1:
                objective = SingleTargetObjective(target=baybe_targets[0])
            else:
                weights = [t.weight for t in targets]
                if os.getenv("MULTI_OPT_TYPE", "desirability").lower() == "pareto":
                    objective = ParetoObjective(targets=baybe_targets)
                else:
                    objective = DesirabilityObjective(targets=baybe_targets, weights=weights, require_normalization=False)
                print(f"Using multi-objective optimization with type: {os.getenv('MULTI_OPT_TYPE', 'desirability').lower()}")
            campaign = Campaign(searchspace=searchspace, objective=objective)#, acquisition_function=qUpperConfidenceBound(beta=0.1))

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
