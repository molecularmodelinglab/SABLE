"""The LLM factory must honour the documented model environment variables."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import llm_factory  # noqa: E402


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


def _record_gemini(monkeypatch):
    """Replace GeminiClient with a recorder so no SDK or network call happens."""
    captured = {}

    class Recorder:
        def __init__(self, api_key, system_prompt=None, model="gemini-2.5-pro"):
            captured["api_key"] = api_key
            captured["model"] = model
            captured["system_prompt"] = system_prompt

    monkeypatch.setattr(llm_factory, "GeminiClient", Recorder)
    return captured


def test_gemini_model_defaults_to_pro(monkeypatch, gemini_env):
    captured = _record_gemini(monkeypatch)

    llm_factory.create_llm_client("gemini")

    assert captured["model"] == "gemini-2.5-pro"


def test_empty_gemini_model_falls_back_to_the_default(monkeypatch, gemini_env):
    # Compose passes unset variables through as empty strings.
    monkeypatch.setenv("GEMINI_MODEL", "")
    captured = _record_gemini(monkeypatch)

    llm_factory.create_llm_client("gemini")

    assert captured["model"] == "gemini-2.5-pro"


def test_gemini_model_is_read_from_the_environment(monkeypatch, gemini_env):
    # Free-tier keys have no gemini-2.5-pro quota, so the model must be overridable.
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    captured = _record_gemini(monkeypatch)

    llm_factory.create_llm_client("gemini")

    assert captured["model"] == "gemini-2.5-flash"


def test_gemini_client_receives_the_extraction_system_prompt(monkeypatch, gemini_env):
    # Without it Gemini answers the prompt in prose and extraction finds no JSON.
    captured = _record_gemini(monkeypatch)

    llm_factory.create_llm_client("gemini")

    assert captured["system_prompt"] == llm_factory.system_instruction
