from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from optimizer.configs import BayesianOptimizationInput, TargetInput
from langchain.tools import BaseTool
import pandas as pd
import json


try:
    from baybe import Campaign
    from baybe.objectives import SingleTargetObjective, DesirabilityObjective
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
    def get_canonical_smiles(s): return s # Dummy function

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
        targets: str,
        batch_size: float,
        encoding: str,
        search_space_id_column: Optional[str] = "Molecule_ID",
        measurement_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        memory: Optional[Dict[str, Any]] = None,
    ) -> Union[List[str], str]:
        """Use the tool."""
        if memory is None:
            memory = {}

        if not BAYBE_AVAILABLE:
            return "Error: BayBE library is not installed. Cannot perform Bayesian Optimization."
        
        if 'search_space' in memory:
            search_space = memory['search_space']
            print(f"Using search_space from memory with {len(search_space)} entries.")
        else:
            return "Error: No search space found in memory. Run Enumerator first (populates 'search_space')."

        try:
            # The 'targets' are now a JSON string, so we need to parse it.
            validated_targets = [TargetInput(**t) for t in json.loads(targets)]

            # Handle measurement_data - it can be a list of dicts or a memory key
            processed_measurement_data = None
            if measurement_data:
                if isinstance(measurement_data, str):
                    # It's a memory key, retrieve the data
                    if measurement_data in memory:
                        processed_measurement_data = memory[measurement_data]
                        print(f"Using measurement data from memory key '{measurement_data}' with {len(processed_measurement_data)} entries.")
                    else:
                        return f"Error: Measurement data key '{measurement_data}' not found in memory."
                else:
                    # It's already a list of dicts
                    processed_measurement_data = measurement_data

            # Create BayBE parameters from the search space
            canonical_search_space = {
                id_: get_canonical_smiles(smi) for id_, smi in search_space.items()
            }
            substance_param = SubstanceParameter(
                name=search_space_id_column, 
                data=canonical_search_space, 
                encoding=encoding
            )
            searchspace = SearchSpace.from_product(parameters=[substance_param])

            # Create BayBE targets
            baybe_targets = [
                NumericalTarget(name=t.name, mode=t.mode, bounds=t.bounds, transformation=t.transformation)
                for t in validated_targets
            ]

            # Create objective
            if len(baybe_targets) == 1:
                objective = SingleTargetObjective(target=baybe_targets[0])
            else:
                weights = [t.weight for t in validated_targets]
                objective = DesirabilityObjective(targets=baybe_targets, weights=weights)

            # Create Campaign
            campaign = Campaign(searchspace=searchspace, objective=objective)

            # Add measurements if provided
            if processed_measurement_data:
                df_measurements = pd.DataFrame(processed_measurement_data)
                campaign.add_measurements(df_measurements)

            # Get recommendations
            recommendations_df = campaign.recommend(batch_size=int(batch_size))
            
            recommended_ids = recommendations_df[search_space_id_column].tolist()

            if 'bo_rounds' not in memory:
                memory['bo_rounds'] = []
            
            round_number = len(memory['bo_rounds']) + 1
            round_entry = {
                "round": round_number,
                "recommendations": recommended_ids,
                "measurements": processed_measurement_data if processed_measurement_data else [],
                "characterization": {}
            }

            memory['bo_rounds'].append(round_entry)

            summary_message = (
                f"BO Round {round_number}: recommended {len(recommended_ids)} molecules. "
                f"Stored in memory under 'bo_rounds[{round_number - 1}]'."
            )
            return summary_message

        except Exception as e:
            return f"Error during Bayesian Optimization process: {e}"

    async def _arun(self, **kwargs):
        # This tool does not support async execution
        raise NotImplementedError("BayesianOptimizationTool does not support async")

# Example of how to potentially use it (outside the class definition)
if __name__ == '__main__':
    if not BAYBE_AVAILABLE:
        print("BayBE library not found. Skipping example usage.")
    else:
        # 1. Define Targets
        targets_config = [
            {"name": "Yield", "mode": "MAX", "weight": 0.2, "bounds": [0, 1]}, 
            {"name": "Purity", "mode": "MAX", "weight": 0.8,"bounds": [0, 1]}
        ]
        # targets_config = [{"name": "ALogP", "mode": "MAX"}] # Single objective example

        # 2. Define Search Space (Molecule IDs mapped to SMILES)
        search_space = {
            "mol_1": "CCO", # Ethanol
            "mol_2": "CCC", # Propane
            "mol_3": "CCCO", # Propanol
            "mol_4": "CCCC", # Butane
            "mol_5": "CCCCO", # Butanol
            "mol_6": "c1ccccc1", # Benzene
            "mol_7": "Cc1ccccc1", # Toluene
            "mol_8": "CC(=O)O", # Acetic Acid
        }

        # Provide Measurement Data
        # Ensure 'Molecule_ID' matches search_space_id_column and target names match target configs
        previous_measurements = [
            {"Molecule_ID": "mol_1", "Yield": 0.75, "Purity": 0.98},
            {"Molecule_ID": "mol_3", "Yield": 0.805, "Purity": 0.975},
            {"Molecule_ID": "mol_6", "Yield": 0.1, "Purity": 0.999}, # Benzene example
            # {"Molecule_ID": "mol_99", "Yield": 50.0, "Purity": 90.0}, # Example of ID not in search space (will be warned/ignored)
        ]
        # previous_measurements = [{"Molecule_ID": "mol_1", "ALogP": 0.1}] # Single objective example

        # 4. Instantiate and Run the Tool
        bo_tool = BayesianOptimizationTool()
        
        recommendations = bo_tool.run(
            {
             "targets": targets_config,
             "search_space_smiles": search_space,
             "batch_size": 3,
             "measurement_data":previous_measurements,
             "search_space_id_column":"Molecule_ID", # Explicitly matching the data key
             "encoding":"MORDRED" #  Or RDKIT, MorganFP etc.
             } 
        )

        # 5. Print Results
        print("\n--- Bayesian Optimization Tool Example ---")
        if isinstance(recommendations, str) and recommendations.startswith("Error"):
            print(f"Tool Execution Failed: {recommendations}")
        else:
            print(f"Recommended Molecule IDs for next batch: {recommendations}")
        print("----------------------------------------")
