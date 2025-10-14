"""
Factory for creating LLM clients (e.g., OpenAI, Gemini).

This module provides a standardized way to instantiate a language model client
based on environment variables. It ensures that different models can be
used interchangeably by wrapping them in a consistent interface.

Required Environment Variables:
- LLM_PROVIDER: 'openai' or 'gemini'
- OPENAI_API_KEY: Your API key for OpenAI (if using 'openai').
- GOOGLE_API_KEY: Your API key for Google AI Studio (if using 'gemini').
"""

import os
from typing import Protocol, Optional
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    # Look for .env in the project root
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded environment file")
except ImportError:
    print("Note: python-dotenv not installed. Using system environment variables only.")

# Use try-except blocks to handle missing dependencies gracefully
try:
    import openai
except ImportError:
    openai = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None


class LLMClient(Protocol):
    """A protocol defining the common interface for all LLM clients."""

    def generate(self, prompt: str) -> str:
        """
        Generates a text response from the language model for a given prompt.
        """
        ...


class OpenAIClient:
    """Wrapper for the OpenAI API client."""

    def __init__(self, api_key: str, model: str = "gpt-4-turbo"):
        if not openai:
            raise ImportError("The 'openai' library is not installed. Please install it with 'pip install openai'.")
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def generate(self, prompt: str) -> str:
        """Generates a response using the OpenAI ChatCompletion endpoint."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content
            return content if content else ""
        except Exception as e:
            print(f"Error calling OpenAI API: {e}")
            return ""


class GeminiClient:
    """Wrapper for the Google Generative AI (Gemini) client."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-pro"):
        if not genai:
            raise ImportError("The 'google-generativeai' library is not installed. Please install it with 'pip install google-generativeai'.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model)

    def generate(self, prompt: str) -> str:
        """Generates a response using the Gemini API."""
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"Error calling Gemini API: {e}")
            return ""


def get_llm_client() -> Optional[LLMClient]:
    """
    Factory function to get an LLM client based on environment variables.

    Reads the `LLM_PROVIDER` environment variable to determine which client
    to instantiate ('openai' or 'gemini'). It then uses the corresponding
    API key environment variable (`OPENAI_API_KEY` or `GOOGLE_API_KEY`).

    Returns:
        An instance of a class that conforms to the LLMClient protocol,
        or None if the configuration is invalid or keys are missing.
    """
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("Warning: LLM_PROVIDER is 'openai' but OPENAI_API_KEY is not set.")
            return None
        print("Initializing OpenAI client...")
        return OpenAIClient(api_key=api_key)

    elif provider == "gemini":
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("Warning: LLM_PROVIDER is 'gemini' but GOOGLE_API_KEY is not set.")
            return None
        print("Initializing Gemini client...")
        return GeminiClient(api_key=api_key)

    elif provider:
        print(f"Warning: Unknown LLM_PROVIDER '{provider}'. Supported values are 'openai', 'gemini'.")
        return None

    else:
        # No provider specified, so no client will be used.
        return None