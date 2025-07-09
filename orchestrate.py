import os
import json
import google.generativeai as genai
from typing import List, Dict, Any

# Import your custom tools
from tools.enumerator_tool import EnumeratorTool
from tools.bayesopt_tool import BayesianOptimizationTool, BAYBE_AVAILABLE
from tools.stoplight_tool import StoplightTool
from tools.molecule_characterization_tool import MoleculeCharacterizationTool
from tools.measurement_extractor_tool import MeasurementExtractor
from tools.report_generator_tool import ReportGenerator
from tools.workflow_summary_tool import WorkflowSummary


try:
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
except KeyError:
    print("ERROR: GOOGLE_API_KEY environment variable not set.")
    print("Please set your API key to run this script.")
    exit()


def clean_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively processes a Pydantic JSON schema to make it Gemini-compatible.
    - Inlines '$ref' definitions.
    - Removes unsupported keys ('title', 'default', '$defs', 'additionalProperties').
    - Simplifies 'anyOf' for optional types (Union[T, None]).
    """
    # Store definitions from '$defs' if they exist to inline them later
    defs = schema.pop('$defs', {})

    def _process_node(node):
        if not isinstance(node, (dict, list)):
            return node

        if isinstance(node, list):
            return [_process_node(item) for item in node]

        # If a reference is found, replace it with its definition
        if '$ref' in node:
            ref_path = node['$ref'].split('/')
            def_name = ref_path[-1]
            if def_name in defs:
                return _process_node(defs[def_name].copy())
            else:
                return node

        
        if 'anyOf' in node:
            if len(node['anyOf']) == 2:
                # Try to find a non-null type
                non_null_type = next((item for item in node['anyOf'] if item.get('type') != 'null'), None)
                if non_null_type:
                    return _process_node(non_null_type)
            # If we can't simplify anyOf, take the first option
            if node['anyOf']:
                return _process_node(node['anyOf'][0])

        # Recursively process other keys in the dictionary
        cleaned_node = {}
        for key, value in node.items():
            # Skip all unsupported keys
            if key in ['title', 'default', 'additionalProperties', 'anyOf']:
                continue
            cleaned_node[key] = _process_node(value)
        
        return cleaned_node

    return _process_node(schema)


class GeminiToolOrchestrator:
    """
    Manages a conversational workflow with the Gemini API and a suite of chemistry tools.
    """
    def __init__(self, tools: List[Any], model_name="gemini-2.5-flash"):
        self.tools = {tool.name: tool for tool in tools}
        self.model = genai.GenerativeModel(
            model_name=model_name,
            tools=[self.convert_to_gemini_tool(tool) for tool in tools]
        )
        self.chat = self.model.start_chat()
        self.memory = {}  # Add a memory dictionary to store results
        print("--- Gemini Tool Orchestrator Initialized ---")
        print(f"Model: {model_name}")
        print(f"Available Tools: {list(self.tools.keys())}")
        print("---------------------------------------------")

    @staticmethod
    def convert_to_gemini_tool(tool: Any) -> Dict[str, Any]:
        """Converts a LangChain-style tool with Pydantic schema to Gemini's format."""
        pydantic_schema = tool.args_schema.model_json_schema()
        
        # Clean the schema to inline definitions and remove all unsupported keys
        cleaned_schema = clean_schema_for_gemini(pydantic_schema)

        return {
            "function_declarations": [{
                "name": tool.name,
                "description": tool.description,
                "parameters": cleaned_schema
            }]
        }

    def run_conversation(self, user_prompt: str):
        """
        Starts and manages a multi-turn conversation with the Gemini model,
        handling tool calls and responses.
        """
        print(f"\nUser >>> {user_prompt}\n")
    
        try:
            # Send the initial user prompt to the model
            response = self.chat.send_message(user_prompt)

            while response.candidates[0].content.parts[0].function_call.name:
                function_call = response.candidates[0].content.parts[0].function_call
                tool_name = function_call.name
                args = dict(function_call.args)

                print(f"LLM  >>> Tool Call: {tool_name}")
                print(f"       Arguments: {json.dumps(args, indent=2, default=str)}\n")

                if tool_name not in self.tools:
                    print(f"Error: Model called an unknown tool '{tool_name}'.")
                    break

                # --- Execute the requested tool ---
                tool = self.tools[tool_name]
                try:
                    tool_result = tool._run(**args, memory=self.memory)
                except Exception as e:
                    print(f"Error executing tool {tool_name}: {e}")
                    # Inform the model that the tool call failed
                    tool_result = {"error": f"Failed to execute tool: {str(e)}"}

                # --- Send the tool's result back to the model ---
                print(f"Tool >>> Result: {str(tool_result)[:500]}...\n")
                
                messages_to_send = [
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=tool_name,
                            response={"result": tool_result}
                        )
                    )
                ]

                # If the tool returned an error, prompt the model to continue
                if isinstance(tool_result, dict) and 'error' in tool_result:
                    messages_to_send.append(genai.protos.Part(
                        text="An error occurred. Please analyze the error, correct the tool call if possible, and continue with the plan."
                    ))

                response = self.chat.send_message(messages_to_send)
            
            # We check for text parts and join them, ignoring other part types.
            final_answer = ""
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text'):
                        final_answer += part.text
            
            if not final_answer:
                final_answer = "The model finished the workflow but did not provide a final text summary."

            print(f"LLM  >>> Final Answer:\n{final_answer}")

        except Exception as e:
            # ADDED: Logging block to inspect the malformed call
            print("\n--- An error occurred. Inspecting last model response. ---\n")
            if 'response' in locals() and hasattr(response, 'candidates') and response.candidates:
                print("Last model response candidate:")
                print(response.candidates[0])
            else:
                print("No valid response object was received from the model before the error.")
            
            print(f"\nError details: {e}")
            print("\n--- End of error inspection ---")


def main():
    """Main function to set up and run the discovery agent."""
    if not BAYBE_AVAILABLE:
        print("Warning: 'baybe' library is not installed.")
        print("The BayesianOptimizationTool will not be available.")
        # Only include tools that can function
        available_tools = [
            EnumeratorTool(),
            StoplightTool(),
            MoleculeCharacterizationTool(),
            MeasurementExtractor(),
            ReportGenerator(),
            WorkflowSummary(),
        ]
    else:
        available_tools = [
            EnumeratorTool(),
            BayesianOptimizationTool(),
            StoplightTool(),
            MoleculeCharacterizationTool(),
            MeasurementExtractor(),
            ReportGenerator(),
            WorkflowSummary(),
        ]

    orchestrator = GeminiToolOrchestrator(tools=available_tools)

    discovery_prompt = """
    I need to find new drug candidates. Please start with the molecule Aspirin, which has the SMILES 'CC(=O)OC1=CC=CC=C1C(=O)O'.

    My goal is to maximize 'QED'.

    Here is the plan you should follow:
    1. Use the Enumerator tool to generate a library of 20 molecules similar to Aspirin.
    2. Use the BayesianOptimizer tool to get an initial batch of 5 recommended molecules to test from the generated library. Do not provide any measurement data for this first run. Use MORDRED descriptors for the optimization.
    3. For each of the 5 recommended molecules, use the MoleculeCharacterizer tool to calculate their properties. To reference the recommendations from step 2, use "bo_recommendations" as the molecule_ids parameter.
    4. Use the MeasurementExtractor tool to extract the 'QED' values from the characterization results and format them as measurement data for the next optimization step. IMPORTANT: Use "Molecule_ID" as the id_column_name to match the BayesianOptimizer's expectations.
    5. Run the BayesianOptimizer tool a second time. This time, provide the measurement data you just extracted for 'QED' to get improved recommendations. Use "measurement_data" as the measurement_data parameter.
    6. IMPORTANT: Use the MoleculeCharacterizer tool again to characterize the NEW set of recommended molecules from step 5. Use "bo_recommendations" as the molecule_ids parameter to get the final set of recommendations with their properties.
    7. Use the ReportGenerator tool to create a final summary report of the recommended molecules and their properties. The report should prominently display the QED scores for each recommended molecule.
    8. Use the WorkflowSummary tool to generate a comprehensive summary showing all evaluated molecules, both optimization rounds, and the improvement analysis.
    
    Note: When referencing data stored in memory, use the simple key name (e.g., "bo_recommendations", "measurement_data") without any special syntax.
    """

    orchestrator.run_conversation(user_prompt=discovery_prompt)

    # # After the conversation, print the final results from memory
    # print("\n--- Final Results from Memory ---")
    # if 'bo_recommendations' in orchestrator.memory:
    #     print("\nBayesian Optimizer Recommendations:")
    #     recommendations = orchestrator.memory['bo_recommendations']
    #     all_molecules = orchestrator.memory.get('enumerated_molecules', {})
    #     for rec_id in recommendations:
    #         smiles = all_molecules.get(rec_id, "SMILES not found")
    #         print(f"  - ID: {rec_id}, SMILES: {smiles}")

    # if 'characterization_results' in orchestrator.memory:
    #     print("\nMolecule Characterization Results:")
    #     char_results = orchestrator.memory['characterization_results']
    #     for mol_id, properties in char_results.items():
    #         print(f"  - ID: {mol_id}")
    #         for prop, value in properties.items():
    #             print(f"    - {prop}: {value}")

    print("\n---------------------------------")


if __name__ == "__main__":
    main()