import requests
from io import BytesIO
from PIL import Image as PILImage  # Use PIL.Image to avoid name conflict
from IPython.display import Image, display
from langchain.agents import initialize_agent
from langchain.tools import Tool
from langchain_ollama import ChatOllama


def display_image_from_url(image_url: str):
    """
    Fetches an image from a URL and displays it in the Jupyter Notebook.

    Args:
        image_url: The URL of the image.  This is passed as a string.
    """
    try:
        response = requests.get(image_url, stream=True)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # Use PIL to open the image from the response content
        img = PILImage.open(BytesIO(response.content))

        # Display the image using IPython.display.display
        display(img)


    except requests.exceptions.RequestException as e:
        print(f"Error fetching image from URL: {e}")
        return None  # Or handle the error as appropriate
    except Exception as e:
        print(f"Error displaying image: {e}")
        return None # or handle as appropriate

# Create the custom LangChain tool.  The 'func' parameter takes a *function*,
# not the result of calling a function.
image_display_tool = Tool(
    name="Image Displayer",
    func=display_image_from_url,  # Correct: Pass the function itself
    description="Useful for displaying an image from a URL.  Input should be a URL string.",
)

# Initialize the LangChain agent.  We still need an LLM, even if we're not
# using it for complex reasoning in this simple example.
llm = ChatOllama(model="llama3.1", temperature=0)
tools = [image_display_tool]
agent = initialize_agent(
    tools, llm, agent="zero-shot-react-description", verbose=True
)

# --- Example usage within the Jupyter Notebook ---

# 1.  A valid image URL (replace with a URL of your choice).
valid_image_url = "https://i.imgur.com/njmmyqV.jpeg"

# 2. Run the agent.  The agent will call the tool with the URL.
agent.run(f"Display the image at this URL: {valid_image_url}")
