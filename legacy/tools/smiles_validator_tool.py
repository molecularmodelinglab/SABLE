from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Optional, Type
from rdkit import Chem


# --- RDKit Validation Logic (from previous, improved response) ---

def validate_smiles(smiles_string):
    """
    Validates a SMILES string using RDKit.

    Args:
        smiles_string (str): The SMILES string to validate.

    Returns:
        bool: True if the SMILES string is valid, False otherwise.
        str or None:  Error message if invalid, None if valid.
    """
    try:
        mol = Chem.MolFromSmiles(smiles_string)
        if mol is None:  # Crucial: Check for None return value!
            return False, "RDKit could not parse the SMILES string (returned None)."
        # Further checks (optional, but recommended)
        Chem.SanitizeMol(mol)  # Check for chemical validity (valence, etc.)

        # Check for disconnected structures (if that's considered invalid in your context)
        if '.' in smiles_string:
            fragments = Chem.GetMolFrags(mol, asMols=True)
            if len(fragments) > 1:
                # Check if its salts, not truly disconnected molecules.
                is_salt = all('.' in Chem.MolToSmiles(frag) for frag in fragments)  # . indicates ions
                if not is_salt:
                    return False, "SMILES string represents disconnected molecules."
        return True, None


    except Chem.rdchem.KekulizeException:
        return False, "Kekulization failed.  This often indicates aromaticity problems."
    except Chem.rdchem.AtomValenceException:
        return False, "Invalid atom valence.  Check for incorrect bonding or charges."
    except Chem.rdchem.AtomKekulizeException:
        return False, "Atom kekulization failed.  Aromaticity issue with an atom."
    except Chem.rdchem.MolSanitizeException as e:
        return False, f"Molecule sanitization failed: {e}"
    except Exception as e:  # Catch any other RDKit errors
        return False, f"An unexpected RDKit error occurred: {e}"


# --- LangChain Tool Implementation ---

class SMILESValidationInput(BaseModel):
    smiles: str = Field(..., description="The SMILES string to validate")


class SMILESValidationTool(BaseTool):
    name: str = "SMILESValidator"
    description: str = "Validates a given SMILES string using RDKit, checking for syntax and chemical validity."
    args_schema: Type[BaseModel] = SMILESValidationInput

    def _run(self, smiles: str) -> str:
        """Use the tool."""
        is_valid, error_message = validate_smiles(smiles)
        if is_valid:
            return f"The SMILES string '{smiles}' is valid."
        else:
            return f"The SMILES string '{smiles}' is invalid: {error_message}"

    async def _arun(self, smiles: str) -> str:
        """Use the tool asynchronously (optional, but good practice)."""
        return self._run(smiles)  # For simplicity, call the sync version


# --- Example Usage with LangChain (minimal example) ---
if __name__ == "__main__":
    from langchain.agents import initialize_agent, AgentType
    from langchain.llms import OpenAI  # Or any other LLM

    # You'll need an OpenAI API key (or another LLM) for this to work
    import os

    # os.environ["OPENAI_API_KEY"] = "YOUR_OPENAI_API_KEY"  # Replace with your key

    llm = OpenAI(temperature=0)  # Initialize your LLM

    tools = [SMILESValidationTool()]

    agent = initialize_agent(
        tools, llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True
    )
    try:
        result = agent.run("Validate the SMILES string C1=CC=CC=C1")  # Benzene
        print(result)

        result2 = agent.run("Validate the SMILES string invalid_smiles")
        print(result2)

        result3 = agent.run("Validate the SMILES string C1C1")  # Cyclopropane (will fail Kekulization)
        print(result3)
    except ValueError as e:
        print(f"An error occurred: {e}")
        #  Often a missing API key or exceeding rate limits