import pandas as pd
from typing import List, Dict, Any, Optional

# Import necessary tools and components
from tools.enumerator_tool import EnumeratorTool, EnumeratorInput
from tools.molecule_characterization_tool import MoleculeCharacterizationTool, MoleculeInput
from tools.bayesopt_tool import BayesianOptimizationTool
from optimizer.configs import TargetInput

# Helper function to safely get property value
def get_property_value(properties_result: Dict[str, Any], property_name: str) -> Optional[float]:
    """Safely extracts a numerical property value from the characterization tool's output."""
    if not isinstance(properties_result, dict) or 'molProperties' not in properties_result:
        print(f"Warning: Unexpected characterization result format: {properties_result}")
        return None
    
    prop_data = properties_result['molProperties'].get(property_name)
    
    if prop_data is None:
        print(f"Warning: Property '{property_name}' not found in results.")
        return None
        
    if isinstance(prop_data, dict) and 'value' in prop_data:
        value = prop_data['value']
        if isinstance(value, (int, float)):
            return float(value)
        else:
            print(f"Warning: Property '{property_name}' value is not numeric: {value}")
            return None
    elif isinstance(prop_data, (int, float)):
         return float(prop_data)
    else:
        print(f"Warning: Unexpected format for property '{property_name}': {prop_data}")
        return None


def run_bo_enumeration_workflow(
    starting_molecule: str,
    target_property: List[str],
    target_mode: List[str],
    target_weight: List[float],
    target_bounds: Optional[List[float]] = None,
    target_transformations: Optional[str] = None,
    n_iterations: int = 5,
    batch_size: int = 3,
    n_enumerations: int = 50,
    enumeration_sim_threshold: float = 0.4,
    bo_encoding: str = "MORDRED",
    characterization_method: str = "rdkit"
) -> pd.DataFrame:
    """
    Runs a workflow combining molecule enumeration and Bayesian Optimization.

    Args:
        starting_molecule: SMILES string of the initial molecule.
        target_property: List of names of the properties to optimize.
        target_mode: List of optimization modes ('MAX' or 'MIN') for each target.
        target_weight: List of weights for each target property.
        target_bounds: Optional list of bounds [min, max] for each target property.
                       Use None for targets without bounds, e.g., [[1, 10], None, [0, 1]].
        n_iterations: Number of Bayesian Optimization iterations.
        batch_size: Number of molecules to evaluate in each BO iteration.
        n_enumerations: Max number of molecules to generate via enumeration.
        enumeration_sim_threshold: Similarity threshold for enumeration filtering.
        bo_encoding: Encoding method for BayBE ('MORDRED', 'RDKIT', 'MorganFP', etc.).
        characterization_method: Method used by MoleculeCharacterizationTool ('rdkit' or 'stoplight').

    Returns:
        A pandas DataFrame containing the results of the optimization campaign
        (iteration, molecule_id, smiles, target_property_values...).
        Returns an empty DataFrame if enumeration fails.
    """
    if not (len(target_property) == len(target_mode) == len(target_weight)):
        raise ValueError("Lengths of target_property, target_mode, and target_weight must match.")
    if target_bounds is not None and len(target_property) != len(target_bounds):
         raise ValueError("Length of target_bounds must match the number of targets if provided.")

    enumerator_tool = EnumeratorTool()
    bo_tool = BayesianOptimizationTool()
    char_tool = MoleculeCharacterizationTool()

    all_measurements = []
    bo_search_space_id_col = "Molecule_ID"

    print(f"Starting enumeration for: {starting_molecule}")
    enumeration_args = {
        'molecule': starting_molecule,
        'n_compositions': n_enumerations,
        'sim_threshold': enumeration_sim_threshold,
    }
    enumerated_smiles = enumerator_tool._run(**enumeration_args)

    if not enumerated_smiles or (isinstance(enumerated_smiles, list) and enumerated_smiles[0].startswith("Error:")):
        print(f"Enumeration failed: {enumerated_smiles}")
        return pd.DataFrame()

    enumerated_smiles = [smi for smi in enumerated_smiles if isinstance(smi, str) and not smi.startswith("Error:")]
    
    if not enumerated_smiles:
        print("Enumeration resulted in no valid SMILES strings.")
        return pd.DataFrame()

    print(f"Enumerated {len(enumerated_smiles)} candidate molecules.")

    search_space_smiles_dict = {str(i): smi for i, smi in enumerate(enumerated_smiles)}

    targets_config = []
    for i, prop_name in enumerate(target_property):
        bounds = target_bounds[i] if target_bounds else None
        targets_config.append(
            TargetInput(name=prop_name, mode=target_mode[i], weight=target_weight[i], bounds=bounds, transformation=target_transformations)
        )

    evaluated_ids = set()

    for i in range(n_iterations):
        print(f"--- BO Iteration {i + 1}/{n_iterations} ---")

        measurements_df = pd.DataFrame(all_measurements) if all_measurements else None
        
        if measurements_df is not None and not measurements_df.empty:
             if 'molecule_id' in measurements_df.columns and bo_search_space_id_col != 'molecule_id':
                 measurements_df = measurements_df.rename(columns={'molecule_id': bo_search_space_id_col})
             measurements_df[bo_search_space_id_col] = measurements_df[bo_search_space_id_col].astype(str)

        bo_args = {
            "targets": targets_config,
            "search_space_smiles": search_space_smiles_dict,
            "batch_size": batch_size,
            "encoding": bo_encoding,
            "measurement_data": measurements_df.to_dict('records') if measurements_df is not None and not measurements_df.empty else None,
            "search_space_id_column": bo_search_space_id_col,
        }

        print("Requesting recommendations from Bayesian Optimizer...")
        recommended_ids_str = bo_tool._run(**bo_args)

        if isinstance(recommended_ids_str, str) and recommended_ids_str.startswith("Error"):
            print(f"BO recommendation failed: {recommended_ids_str}")
            break

        if not recommended_ids_str:
            print("BO returned no recommendations.")
            break

        print(f"BO recommended IDs: {recommended_ids_str}")

        newly_evaluated_count = 0
        for mol_id_str in recommended_ids_str:
            if mol_id_str in evaluated_ids:
                print(f"Skipping already evaluated molecule ID: {mol_id_str}")
                continue

            smiles_to_evaluate = search_space_smiles_dict.get(mol_id_str)
            if smiles_to_evaluate is None:
                print(f"Warning: Recommended ID {mol_id_str} not found in search space dict. Skipping.")
                continue

            print(f"Characterizing molecule ID {mol_id_str}: {smiles_to_evaluate}")
            try:
                properties = char_tool._run(smiles=smiles_to_evaluate)

                target_values = {}
                all_targets_found = True
                for prop_name in target_property:
                    value = get_property_value(properties, prop_name)
                    if value is None:
                        print(f"  -> Failed to get target property '{prop_name}' for {smiles_to_evaluate}")
                        all_targets_found = False
                        target_values[prop_name] = None
                    else:
                        target_values[prop_name] = value
                        print(f"  -> Measured {prop_name}: {value}")

                if properties:
                    measurement = {
                        "iteration": i + 1,
                        bo_search_space_id_col: mol_id_str,
                        "smiles": smiles_to_evaluate,
                        **target_values
                    }
                    all_measurements.append(measurement)
                    evaluated_ids.add(mol_id_str)
                    newly_evaluated_count += 1

            except Exception as e:
                print(f"Error characterizing molecule {smiles_to_evaluate} (ID: {mol_id_str}): {e}")

        if newly_evaluated_count == 0 and i < n_iterations -1 :
             print("No new molecules were evaluated in this iteration. Stopping early.")
             break

    results_df = pd.DataFrame(all_measurements)
    if bo_search_space_id_col not in results_df.columns and not results_df.empty:
         print(f"Warning: ID column '{bo_search_space_id_col}' missing in final results.")
    elif bo_search_space_id_col in results_df.columns:
         pass
   
    return results_df


if __name__ == "__main__":
    start_smiles = "Cc1ccc(cc1)c2cc(nn2c3ccc(cc3)S(=O)(=O)N)C(F)(F)F"
    targets = ["QED"]
    modes = ["MAX"]
    weights = [0.6]
    # bounds = [(0,1)]
    # transformations = "LINEAR"

    print(f"Running BO Enumeration Workflow...")
    print(f"Starting Molecule: {start_smiles}")
    print(f"Target Properties: {targets}")
    print(f"Modes: {modes}")
    print(f"Weights: {weights}")
    print(f"Iterations: 4, Batch Size: 15, Enumerations: 20")

    try:
        final_results = run_bo_enumeration_workflow(
            starting_molecule=start_smiles,
            target_property=targets,
            target_mode=modes,
            target_weight=weights,
            # target_bounds=bounds,
            # target_transformations=transformations,
            n_iterations=4,
            batch_size=15,
            n_enumerations=20,
            enumeration_sim_threshold=0.4,
            bo_encoding="MORDRED",
            characterization_method="rdkit"
        )

        print("--- Workflow Complete ---")
        if not final_results.empty:
            print("Optimization Campaign Results:")
            print(final_results.to_string())
            final_results.to_csv("scratch/bo_enumeration_results_1.csv", index=False)

            first_target = targets[0]
            first_mode = modes[0]
            if first_target in final_results.columns:
                 best_result_idx = final_results[first_target].idxmax() if first_mode == "MAX" else final_results[first_target].idxmin()
                 best_result = final_results.loc[best_result_idx]
                 print(f"\nBest Molecule Found (based on {first_target}):")
                 print(best_result)
            else:
                 print(f"First target property '{first_target}' not found in results columns.")

        else:
            print("Workflow finished, but no measurement results were generated.")

    except ImportError as e:
         print(f"Error: Missing dependency. Please ensure 'baybe', 'rdkit', 'pandas' are installed. Details: {e}")
    except Exception as e:
        print(f"An error occurred during the workflow: {e}")
        import traceback
        traceback.print_exc()

