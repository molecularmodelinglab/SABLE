import os
import time
from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
import pandas as pd

from .optimizer.configs import BayBEOptimizerInput
from schemas.errors import ToolError
from schemas.tool_schemas import BORecommendationRequest, BORecommendationResult

try:
    from baybe import Campaign
    from baybe.objectives import SingleTargetObjective, DesirabilityObjective, ParetoObjective
    from baybe.parameters import SubstanceParameter
    from baybe.searchspace import SearchSpace
    from baybe.targets import NumericalTarget
    from baybe.utils.chemistry import get_canonical_smiles

    BAYBE_AVAILABLE = True
except ImportError:
    Campaign = None
    SingleTargetObjective = DesirabilityObjective = ParetoObjective = None
    SubstanceParameter = SearchSpace = NumericalTarget = None
    BAYBE_AVAILABLE = False


class BayBEOptimizerTool(BaseTool):
    """
    A tool to perform Bayesian Optimization for molecule selection using the BayBE library.

    This tool sets up and runs a BayBE campaign based on specified targets,
    a search space of molecules (SMILES), and optional existing measurement data.
    It then recommends the next batch of molecules to evaluate according to the BO strategy.

    Requires the 'baybe' library to be installed.
    """
    name: str = "BayBEOptimizer"
    description: str = Field(default="Runs a Bayesian Optimization campaign using BayBE to recommend the next batch of molecules based on optimization targets, search space, and optional prior measurements. Returns a list of recommended molecule IDs.")
    args_schema: Type[BaseModel] = BayBEOptimizerInput

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
            self._check_baybe_installed()
            started_at = time.perf_counter()
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
            
            if len(targets) == 1 and not isinstance(targets[0], dict):
                targets[0].transformation = None
            # baybe_targets = [
            #     NumericalTarget(name=t.name, mode=t.mode, bounds=t.bounds, transformation=t.transformation)
            #     for t in targets
            # ]

            baybe_targets = []
            for t in targets:
                target_name = _target_value(t, "name")
                target_mode = _normalize_mode(_target_value(t, "mode"))
                target_bounds = _target_value(t, "bounds")
                if target_mode == "MAX":
                    baybe_targets.append(NumericalTarget(name=target_name, minimize=False))
                elif target_mode == "MIN":
                    baybe_targets.append(NumericalTarget(name=target_name, minimize=True))
                elif target_mode == "MATCH":
                    baybe_targets.append(NumericalTarget.match_triangular(name=target_name, match_value=(target_bounds[0]+target_bounds[1])/2, cutoffs=(target_bounds[0], target_bounds[1])))
          

            if len(baybe_targets) == 1:
                objective = SingleTargetObjective(target=baybe_targets[0])
            else:
                weights = [_target_value(t, "weight", 1.0) for t in targets]
                if os.getenv("MULTI_OPT_TYPE", "pareto").lower() == "pareto":
                    objective = ParetoObjective(targets=baybe_targets)
                else:
                    objective = DesirabilityObjective(targets=baybe_targets, weights=weights, require_normalization=False)
                print(f"Using multi-objective optimization with type: {os.getenv('MULTI_OPT_TYPE', 'pareto').lower()}")
            campaign = Campaign(searchspace=searchspace, objective=objective)#, acquisition_function=qUpperConfidenceBound(beta=0.1))

            if measurement_data:
                df_measurements = pd.DataFrame(measurement_data)
                campaign.add_measurements(df_measurements)

            recommendations_df = campaign.recommend(batch_size=int(batch_size))
            
            results = recommendations_df[search_space_id_column].tolist()
            elapsed = time.perf_counter() - started_at
            print(
                f"BayBEOptimizerTool recommended {len(results)} molecules "
                f"in {elapsed:.2f}s."
            )

            return results

        except Exception as e:
            return f"Error during Bayesian Optimization process: {e}"

    def recommend(self, request: BORecommendationRequest) -> BORecommendationResult:
        """Run BayBE through the shared optimizer request/result contract."""
        result = self._run(
            targets=request.targets,
            batch_size=request.batch_size,
            encoding=request.encoding,
            search_space_id_column=request.search_space_id_column,
            measurement_data=request.measurements,
            search_space=request.search_space,
        )
        if isinstance(result, str):
            raise ToolError(result, tool="baybe", code="OPTIMIZER_FAILED")

        return BORecommendationResult(
            recommended_ids=[str(molecule_id) for molecule_id in result],
            model_metrics={
                "measurement_count": len(request.measurements or []),
                "search_space_size": len(request.search_space),
            },
            metadata={
                "tool_id": "baybe",
                "encoding": request.encoding,
                "optimizer_strategy": request.optimizer_strategy,
            },
        )

    async def _arun(self, **kwargs):
        # This tool does not support async execution
        raise NotImplementedError("BayBEOptimizerTool does not support async")


def _target_value(target: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(target, dict):
        value = target.get(field_name, default)
    else:
        value = getattr(target, field_name, default)
    if hasattr(value, "value"):
        return value.value
    return value


def _normalize_mode(mode: Any) -> str:
    mode_value = mode.value if hasattr(mode, "value") else str(mode)
    if "." in mode_value:
        mode_value = mode_value.rsplit(".", 1)[-1]
    mode_value = mode_value.upper()
    return {
        "MAXIMIZE": "MAX",
        "MINIMIZE": "MIN",
    }.get(mode_value, mode_value)
