import os
import json
import yaml
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
from utils import clean_schema_for_gemini


with open('config.yml') as f:
    config = yaml.safe_load(f)

google_api_key = config['api_credentials']['google_gemmini']['api_key']

try:
    genai.configure(api_key=google_api_key)
except KeyError:
    print("ERROR: GOOGLE_API_KEY environment variable not set.")
    print("Please set your API key to run this script.")
    exit()





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
        self.memory = {}
        self.transcript: List[str] = []
        self.tool_specs_text = self._build_tool_specs()
        self.system_preamble = f"""You are a chemistry optimization agent using ReACT.
        Follow this loop strictly:
        Thought: brief reasoning
        Action: <ToolName>
        Action Input: JSON object ONLY containing the tool parameters
        (then wait for Observation)
        Repeat until you can conclude, then output:
        Final Answer: <comprehensive summary>

        Available Tools:
        {self.tool_specs_text}

        Rules:
        - One Action per turn.
        - Always supply all required parameters.
        - Use MeasurementExtractor with properties_to_extract as a list of strings (e.g. ["QED","ALogP"]).
        - Do not hallucinate tool names or params.
        """

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
    
    def _build_tool_specs(self) -> str:
        lines = []
        for tool in self.tools.values():
            schema = tool.args_schema.model_json_schema()
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            param_lines = []
            for name, meta in props.items():
                t = meta.get("type", meta.get("anyOf", [{}])[0].get("type", "object"))
                desc = meta.get("description", "")
                req = "required" if name in required else "optional"
                param_lines.append(f"  - {name} ({t}, {req}): {desc}")
            lines.append(f"{tool.name}: {tool.description}\nParameters:\n" + "\n".join(param_lines))
        return "\n\n".join(lines)
    
    def _format_observation_part(self, tool_name: str, tool_result: Any) -> str:
        summary = str(tool_result)
        if isinstance(summary, str) and len(summary) > 800:
            summary = summary[:800] + "...[truncated]"
        return f"Observation ({tool_name}): {summary}"

    def run_react_turn(self, user_or_observation: str, is_observation=False):
        prompt = user_or_observation if not is_observation else user_or_observation
        response = self.chat.send_message(prompt)
        self.transcript.append(f"MODEL_RAW: {response}")
        return response

    def _extract_function_call(self, response):
        try:
            parts = response.candidates[0].content.parts
            for part in parts:
                if getattr(part, "function_call", None):
                    return part.function_call
        except Exception:
            return None
        return None
    

    def conversation_loop(self, initial_user_task: str, max_iterations: int = 25):
        # Seed with system + user
        opening = self.system_preamble + "\nUser Task:\n" + initial_user_task.strip()
        response = self.run_react_turn(opening)
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            fn = self._extract_function_call(response)

            # If no function call, check for Final Answer
            full_text = ""
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text"):
                    full_text += part.text
            if "Final Answer:" in full_text:
                print("LLM >>> " + full_text)
                return full_text

            if not fn:
                # Ask model to either call a tool or finish
                response = self.run_react_turn("Please either provide an Action with a tool call or a Final Answer.")
                continue

            tool_name = fn.name
            args = dict(fn.args)
            print(tool_name, args)
            # print(f"LLM >>> Action: {tool_name}\nArgs: {json.dumps(args, indent=2)}")
            print(f"LLM >>> Action: {tool_name}\nArgs: {args}")

            if tool_name not in self.tools:
                response = self.run_react_turn(f"Observation: Unknown tool '{tool_name}'. Use a valid tool.")
                continue

            tool = self.tools[tool_name]
            try:
                result = tool._run(**args, memory=self.memory)
            except Exception as e:
                result = f"Tool error: {e}"

            print(f"Tool >>> Result (truncated): {str(result)[:400]}")
            observation = self._format_observation_part(tool_name, result)
            response = self.run_react_turn(observation, is_observation=True)

        print("Max iterations reached; forcing termination.")
        return "Terminated without Final Answer."


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

    target_properties = ["BBB Permeability"]
    targets_config = [
        {"name": prop, "mode": "MAX", "weight": 1.0/len(target_properties)}#, "bounds": [0, 1]}
        for prop in target_properties
    ]

    # discovery_prompt = """
    # I need to find new drug candidates. Please start with the molecule Aspirin, which has the SMILES 'CC(=O)OC1=CC=CC=C1C(=O)O'.

    # My goal is to maximize 'QED'.

    # Here is the plan you should follow:
    # 1. Use the Enumerator tool to generate a library of 20 molecules similar to Aspirin.
    # 2. Use the BayesianOptimizer tool to get an initial batch of 5 recommended molecules to test from the generated library. Do not provide any measurement data for this first run. Use MORDRED descriptors for the optimization.
    # 3. For each of the 5 recommended molecules, use the MoleculeCharacterizer tool to calculate their properties. To reference the recommendations from step 2, use "bo_recommendations" as the molecule_ids parameter.
    # 4. Use the MeasurementExtractor tool to extract the 'QED' values from the characterization results and format them as measurement data for the next optimization step. IMPORTANT: Use "Molecule_ID" as the id_column_name to match the BayesianOptimizer's expectations.
    # 5. Run the BayesianOptimizer tool a second time. This time, provide the measurement data you just extracted for 'QED' to get improved recommendations. Use "measurement_data" as the measurement_data parameter.
    # 6. IMPORTANT: Use the MoleculeCharacterizer tool again to characterize the NEW set of recommended molecules from step 5. Use "bo_recommendations" as the molecule_ids parameter to get the final set of recommendations with their properties.
    # 7. Use the ReportGenerator tool to create a final summary report of the recommended molecules and their properties. The report should prominently display the QED scores for each recommended molecule.
    # 8. Use the WorkflowSummary tool to generate a comprehensive summary showing all evaluated molecules, both optimization rounds, and the improvement analysis.
    
    # Note: When referencing data stored in memory, use the simple key name (e.g., "bo_recommendations", "measurement_data") without any special syntax.
    # """

    # orchestrator.run_conversation(user_prompt=discovery_prompt)

    discovery_prompt = f"""
    You must optimize Aspirin (SMILES 'CC(=O)OC1=CC=CC=C1C(=O)O').

    Optimization Targets (BayesianOptimizer.targets JSON): {json.dumps(targets_config)}

    Plan:
    1. Enumerate 20 analogs.
    2. Run BayesianOptimizer (first round) with MORDRED.
    3. Characterize recommended molecules with MoleculeCharacterizer or StoplightTool using bo_recommendations. Use the right characterization tool based on the property.
    4. Extract properties {target_properties} via MeasurementExtractor using a list of properties.
    5. Run BayesianOptimizer second round with measurement_data.
    6. Re-characterize new bo_recommendations.
    7. Generate ReportGenerator + WorkflowSummary.

    Ensure every tool call is preceded by Thought and formatted with Action / Action Input.
    """

    orchestrator.conversation_loop(discovery_prompt)

    print("\n---------------------------------")


if __name__ == "__main__":
    main()