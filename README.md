# LIZARD — LIgand optimiZation via Agentic Research and Discovery

LIZARD is an agentic workflow that enumerates, characterizes, and optimizes small molecules toward user‑defined target properties. It wires together nodes (argument extraction, enumeration, Bayesian optimization, characterization, summarization) with clear telemetry and checkpoints.

<img src="images/lizard.png" width="300" />

## Features

- Prompt-driven optimization over a molecular search space
- Enumeration + RDKit/predictive model characterization + Bayesian optimization
- Checkpoints and resumability
- Clean summary with best molecules and baseline vs. optimized comparison

## Requirements

- Python 3.12
- RDKit (installed via conda/micromamba in Docker image)
- External dependency: healer (molecule enumerator)
- Optional: LLM API key (OpenAI or Google Gemini) for enhanced argument extraction

Note: Files in `legacy/` are old and not used by the current workflow. They remain for reference but are excluded from the Docker build context.

## LLM Configuration (Optional but Recommended)

LIZARD uses a hybrid approach for extracting arguments from natural language prompts:
- **LLM-based extraction** (if configured): Uses GPT or Gemini for intelligent parsing
- **Rule-based extraction** (fallback): Uses regex patterns when LLM is unavailable

To enable LLM-based extraction, set up your API keys:

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your API key:
# For OpenAI:
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here

# OR for Google Gemini:
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your_api_key_here
```

Get API keys:
- OpenAI: https://platform.openai.com/api-keys
- Google Gemini: https://aistudio.google.com/app/apikey

**Note:** LIZARD works without an LLM (using rule-based extraction), but LLM extraction provides better accuracy for complex prompts.

## Quick start (Docker)

Build the image and run with the example prompt. The image bundles Python 3.12, RDKit, and pip dependencies.

```bash
docker build -t lizard:latest .

# Run the example prompt
docker run --rm lizard:latest

# Provide your own prompt
docker run --rm lizard:latest run "Optimize aspirin for better QED. Enumerate 50 analogs and run 3 iterations."

# Resume from a checkpoint mounted from the host
docker run --rm -v "$PWD/checkpoints:/app/checkpoints" lizard:latest resume /app/checkpoints/<checkpoint_file>.pkl

# Export results to a mounted path
docker run --rm -v "$PWD:/app" lizard:latest export --output /app/results.json "Optimize caffeine for higher logP and lower TPSA"
```

### Using docker compose

The repo includes a `docker-compose.yml` you can customize. Examples:

```bash
# Build
docker compose build

# Run with the example prompt
docker compose up

# Run with a custom prompt via env var
PROMPT='Optimize aspirin for better QED and solubility. Enumerate 50 analogs and run 3 iterations.' \
docker compose up

# Or override the command
docker compose run --rm lizard run "Optimize ibuprofen for lower TPSA"

# Run the API service (port 8000) and UI dev server (port 5173)
docker compose up api
# In another terminal
cd ui && npm install && npm run dev
```

### Mounting configuration and API keys

Copy `config.example.yml` to `config.yml` and edit as needed (e.g., credentials for external services). Mount it into the container:

```bash
cp config.example.yml config.yml
docker run --rm -v "$PWD/config.yml:/app/config.yml" lizard:latest run "Your prompt here"
```

### Checkpoints and artifacts

- Checkpoints are written to `checkpoints/` (mount this directory to persist across runs).
- Final results are exportable via `--output` flag.

## Local development

You can run LIZARD locally if you have Python 3.12 and RDKit available.

```bash
# Optional: use conda/mamba to install rdkit, then pip install the rest
conda create -n lizard python=3.12 -c conda-forge rdkit
conda activate lizard
pip install -r requirements.txt

# Run
python run_workflow.py --example
```

## Extending the agent and workflow

The codebase is organized into clear modules so you can swap or add nodes/tools without changing the whole system.

- `edges/graph_builder.py`: builds the LangGraph state graph and wires node transitions
- `nodes/`: individual workflow nodes (e.g., `extract_arguments.py`, `enumerate_molecules.py`, `bo_iteration.py`, `characterize_molecules.py`, `check_exit_conditions.py`, `summarize_results.py`)
- `tools/`: pluggable tools used by nodes (e.g., `enumerator_tool.py`, `molecule_characterization_tool.py`, `bayesopt_tool.py`, `stoplight_tool.py`)
- `schemas/`: Pydantic models for state, characterization schemas, error types
- `run_workflow.py`: runner with checkpointing and exporting

Common extension points:

- Add a new property or tool: extend `schemas/characterization.py` mappings and implement a tool under `tools/`; wire it in `nodes/characterize_molecules.py`.
- Change selection strategy: update `nodes/bo_iteration.py` or the optimizer tool.
- New inputs or parsing rules: modify `nodes/extract_arguments.py`.

Tip: emit structured telemetry for new nodes/tools so failures are actionable.

## Configuration

Use `config.yml` to store credentials and options. See `config.example.yml` for the shape. At runtime the workflow will prefer `config.yml` if present.

## License

See `LICENSE`.
