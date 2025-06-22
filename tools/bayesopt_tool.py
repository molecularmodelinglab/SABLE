from typing import Type, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from optimizer.configs import BayesianOptimizationInput
from langchain.tools import BaseTool
import pandas as pd


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
        tool_input: str = None,
        **kwargs: Any # To catch any unexpected args
    ) -> Union[List[str], str]:
        """
        Executes the Bayesian Optimization campaign setup and recommendation.

        Args:
            targets: List of target configurations.
            search_space_smiles: Dictionary mapping IDs to SMILES.
            batch_size: Number of recommendations requested.
            encoding: Molecular encoding method.
            measurement_data: Optional list of prior measurements.
            search_space_id_column: Identifier column name.

        Returns:
            A list of recommended molecule IDs (strings) or an error message string.
        """
        self._check_baybe_installed()

        targets = kwargs.get('targets', [])
        search_space_smiles = kwargs.get('search_space_smiles', {})
        batch_size = kwargs.get('batch_size', 5)
        encoding = kwargs.get('encoding', 'MORDRED')
        measurement_data = kwargs.get('measurement_data', None)
        search_space_id_column = kwargs.get('search_space_id_column', 'Molecule_ID')

        try:
            # 1. Validate and Prepare Inputs
            if not targets:
                return "Error: No optimization targets specified."
            if not search_space_smiles:
                return "Error: Search space (smiles dictionary) cannot be empty."

            # Canonicalize SMILES in search space for consistency
            canonical_search_space = {
                id_: get_canonical_smiles(smi) for id_, smi in search_space_smiles.items()
            }

            # 2. Define Search Space Parameter
            # Ensure IDs are strings for BayBE compatibility if they aren't already
            str_canonical_search_space = {str(k): v for k, v in canonical_search_space.items()}
            
            substance_param = SubstanceParameter(
                name=search_space_id_column, # Use the specified ID column name
                data=str_canonical_search_space,
                encoding=encoding
            )
            searchspace = SearchSpace.from_product(parameters=[substance_param])

            # 3. Define Objective
            baybe_targets = []
            # for t_dict in targets:
            #     print(f"Processing target: {t_dict}")
            #     target_input = TargetInput(**t_dict) # Validate each target dict
            #     baybe_targets.append(
            #         NumericalTarget(name=target_input.name, mode=target_input.mode, bounds=None) # Bounds can be added if needed
            #     )
            for target_obj in targets: # Renamed loop variable for clarity
                print(f"Processing target object: {target_obj}")
                # No need to re-initialize, just use the existing object's attributes
                baybe_targets.append(
                    NumericalTarget(name=target_obj.name, mode=target_obj.mode, bounds=target_obj.bounds, transformation=target_obj.transformation) 
                )
            print(f"BayBE targets created: {baybe_targets}")
            if len(baybe_targets) == 1:
                objective = SingleTargetObjective(target=baybe_targets[0])
            else:
                # For multi-objective, DesirabilityObjective is a common choice
                # Weights can be customized if provided in TargetInput
                # default_weight = TargetInput.model_fields['weight'].default # Get default from the model
                weights = [target_obj.weight for target_obj in targets] # Corrected line
                objective = DesirabilityObjective(targets=baybe_targets, weights=weights, scalarizer="MEAN")
                # weights = [TargetInput(**t_dict).weight for t_dict in targets]
                # objective = DesirabilityObjective(targets=baybe_targets, weights=weights, combine_func="MEAN")
                # Alternatively: objective = MultiTargetObjective(targets=baybe_targets) if using specific multi-objective recommenders

            # 4. Create Campaign
            campaign = Campaign(
                searchspace=searchspace,
                objective=objective
                # recommender can be specified here, defaults are usually good
            )

            # 5. Add Measurement Data (if provided)
            if measurement_data:
                try:
                    # Convert list of dicts to DataFrame
                    measurements_df = pd.DataFrame(measurement_data)

                    # Ensure the ID column exists and contains string IDs matching the search space keys
                    if search_space_id_column not in measurements_df.columns:
                         return f"Error: Measurement data is missing the specified ID column '{search_space_id_column}'."
                    
                    # Convert ID column to string to match SubstanceParameter keys
                    measurements_df[search_space_id_column] = measurements_df[search_space_id_column].astype(str)

                    # Check if all required target columns exist
                    required_target_names = [t.name for t in baybe_targets]
                    missing_cols = [col for col in required_target_names if col not in measurements_df.columns]
                    if missing_cols:
                        return f"Error: Measurement data is missing required target columns: {', '.join(missing_cols)}."

                    # Ensure IDs in measurements exist in the search space
                    valid_ids = set(str_canonical_search_space.keys())
                    measured_ids = set(measurements_df[search_space_id_column])
                    invalid_measured_ids = measured_ids - valid_ids
                    if invalid_measured_ids:
                         print(f"Warning: Measurement data contains IDs not found in the search space: {invalid_measured_ids}. These rows will be ignored by BayBE.")
                         # Filter out invalid rows before adding
                         measurements_df = measurements_df[measurements_df[search_space_id_column].isin(valid_ids)]


                    if not measurements_df.empty:
                        campaign.add_measurements(measurements_df)
                    else:
                         print("Warning: No valid measurement data to add after filtering.")


                except Exception as e:
                    return f"Error processing measurement data: {e}"

            # 6. Get Recommendations
            recommendations_df = campaign.recommend(batch_size=batch_size)

            # Extract the recommended IDs (they should be strings matching the input keys)
            recommended_ids = recommendations_df[search_space_id_column].tolist()

            return recommended_ids # Return list of string IDs

        except Exception as e:
            return f"Error during Bayesian Optimization: {e}"

    
    async def _arun(self, tool_input: str = None, **kwargs) -> Union[List[str], str]:
        return self._run(tool_input=tool_input, **kwargs)

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
