import os
from pathlib import Path
from typing import Dict

# Use LIZARD_DATA_ROOT if set; otherwise default to a writable local ./data directory
DATA_ROOT = Path(os.environ.get("LIZARD_DATA_ROOT", str(Path.cwd() / "data")))


def run_dir(run_id: str) -> Path:
    return DATA_ROOT / "runs" / run_id


def ensure_run_dirs(run_id: str) -> Dict[str, str]:
    base = run_dir(run_id)
    (base / "inputs").mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    (base / "checkpoints").mkdir(parents=True, exist_ok=True)
    (base / "results").mkdir(parents=True, exist_ok=True)
    (base / "artifacts").mkdir(parents=True, exist_ok=True)
    return {
        "base": str(base),
        "inputs": str(base / "inputs"),
        "logs": str(base / "logs"),
        "checkpoints": str(base / "checkpoints"),
        "results": str(base / "results"),
        "artifacts": str(base / "artifacts"),
    }


def results_json_path(run_id: str) -> Path:
    return run_dir(run_id) / "results" / "results.json"


def summary_txt_path(run_id: str) -> Path:
    return run_dir(run_id) / "results" / "summary.txt"
