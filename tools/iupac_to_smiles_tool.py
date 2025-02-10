from langchain.tools import BaseTool
import pubchempy

class IUPACToSMILESTool(BaseTool):
    name: str = "IUPAC_to_SMILES"
    description: str = "Useful for converting an IUPAC chemical name to its SMILES representation."

    def _run(self, iupac_name: str) -> str:
        """Use the tool."""
        try:
            results = pubchempy.get_compounds(iupac_name, 'name')
            if results:
                # We'll take the first result and return its isomeric SMILES.
                #  It's good to handle cases where multiple compounds might match.
                #  Here, we prioritize the first result, but you could add logic to handle
                #  multiple matches (e.g., return a list, ask the user to choose, etc.)
                return results[0].isomeric_smiles
            else:
                return "No compound found with that IUPAC name."
        except pubchempy.PubChemHTTPError as e:
            # Handle PubChem API errors (e.g., too many requests, server issues)
            return f"PubChem API error: {e}"
        except Exception as e:
            # Handle other potential errors (e.g., invalid input)
            return f"An unexpected error occurred: {e}"

    async def _arun(self, iupac_name: str) -> str:
        """Use the tool asynchronously (optional, but good practice)."""
        # PubChemPy doesn't have native async support, so we just call the sync version.
        #  If you had a library with async capabilities, you'd implement the async logic here.
        return self._run(iupac_name)



# Example usage (outside the class, for testing):
if __name__ == '__main__':
    tool = IUPACToSMILESTool()
    smiles_string = tool.run("Glucose")  # Corrected: Pass the IUPAC name directly.
    print(f"SMILES for Glucose: {smiles_string}")

    smiles_string = tool.run("2-methylpropane")
    print(f"SMILES for 2-methylpropane: {smiles_string}")


    smiles_string = tool.run("InvalidNameThatDoesNotExist") # Test invalid Name
    print(f"SMILES for InvalidNameThatDoesNotExist: {smiles_string}")

    smiles_string = tool.run("Benzene")
    print(f"SMILES for Benzene: {smiles_string}")