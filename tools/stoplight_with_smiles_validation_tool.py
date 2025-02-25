from typing import Any, Type, Dict, Union
from pydantic import BaseModel, Field
from langchain.tools import BaseTool


class ValidatedStoplightToolInput(BaseModel):
    """Input for the combined SMILES validation and Stoplight API tool."""
    smiles: str = Field(..., description="SMILES string representation of the molecule")
    precision: str = Field(default="2", description="Number of decimal places for numerical results")


class ValidatedStoplightTool(BaseTool):
    """Your combined tool."""

    name: str = "ValidatedStoplight"
    description: str = """
    This tool first validates a SMILES string using the SMILESValidator tool and then,
    if valid, calculates molecular properties using the Stoplight API tool.
    """
    args_schema: Type[BaseModel] = ValidatedStoplightToolInput

    # Pydantic fields:
    validator_tool: Any
    stoplight_tool: Any

    def _run(self, tool_input: str = None, **kwargs) -> Union[Dict[str, Any], str]:
        # ... same as before ...
        smiles = tool_input if tool_input is not None else kwargs.get('smiles')
        precision = kwargs.get('precision', "2")

        if smiles is None:
            raise ValueError("SMILES string must be provided.")

        validation_result = self.validator_tool.run(smiles)
        if "is valid" not in validation_result:
            return validation_result

        return self.stoplight_tool.run({"smiles": smiles, "precision": precision})

    async def _arun(self, tool_input: str = None, **kwargs) -> Union[Dict[str, Any], str]:
        return self._run(tool_input=tool_input, **kwargs)


if __name__ == "__main__":
    from tools.smiles_validator_tool import SMILESValidationTool
    from tools.stoplight_tool import StoplightTool

    # You must pass the validator_tool and stoplight_tool as named args:
    combined_tool = ValidatedStoplightTool(
        validator_tool=SMILESValidationTool(),
        stoplight_tool=StoplightTool()
    )

    print("Testing with valid SMILES:")
    print(combined_tool.run("CO"))

    print("\nTesting with invalid SMILES:")
    print(combined_tool.run("C1C1"))
