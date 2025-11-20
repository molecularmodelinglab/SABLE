'''
    Job manager classes for submitting and tracking jobs on an HPC via Slurm.
'''

from __future__ import annotations
import json
import os
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict

from .hpc_client import HPCClient


# TODO: Switch json storage to a lightweight DB (SQLite?) if needed.

class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class JobRecord:
    job_id: str
    slurm_job_id: str
    tool_name: str
    remote_input_dir: str
    remote_output_dir: str
    remote_logs_dir: str
    local_output_dir: Optional[str] = None
    state: JobState = JobState.UNKNOWN
    extra: dict[str, Any] = None


class BaseJobManager(ABC):
    """
    Base class for job managers that submit jobs to an HPC via Slurm.
    Keeps simple JSON-based metadata for job tracking.
    """

    def __init__(
        self,
        hpc_client: "HPCClient",
        remote_base_dir: str,
        local_output_dir: str,
        job_store_path: str = "jobs.json",
        tool_name: str = "generic",
    ) -> None:
        self.hpc = hpc_client
        self.remote_base_dir = remote_base_dir.rstrip("/")
        self.local_output_dir = Path(local_output_dir)
        self.local_output_dir.mkdir(parents=True, exist_ok=True)
        self.job_store_path = Path(job_store_path)
        self.tool_name = tool_name

        if not self.job_store_path.exists():
            self.job_store_path.write_text("{}", encoding="utf-8")

    # ----- public API -----

    @abstractmethod
    def submit_job(self, payload: Any) -> JobRecord:
        """Submit a new job and return its JobRecord."""
        raise NotImplementedError

    @abstractmethod
    def get_status(self, job_id: str) -> Optional[JobRecord]:
        """Check job status on the cluster and update the record."""
        raise NotImplementedError

    @abstractmethod
    def fetch_results(self, job_id: str) -> Optional[JobRecord]:
        """
        Download results for a completed job, update record.local_output_dir,
        and return the updated record.
        """
        raise NotImplementedError

    # ----- helpers shared by subclasses -----

    def _create_job_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _load_store(self) -> dict[str, Any]:
        try:
            return json.loads(self.job_store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_store(self, store: dict[str, Any]) -> None:
        self.job_store_path.write_text(
            json.dumps(store, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_record(self, record: JobRecord) -> None:
        store = self._load_store()
        store[record.job_id] = asdict(record)
        self._save_store(store)

    def _load_record(self, job_id: str) -> Optional[JobRecord]:
        store = self._load_store()
        data = store.get(job_id)
        if not data:
            return None
        return JobRecord(
            **data,
            state=JobState(data.get("state", JobState.UNKNOWN)),
        )

    def _update_record(self, record: JobRecord) -> None:
        self._save_record(record)

    def _parse_slurm_state(self, raw: str) -> JobState:
        """
        Map Slurm state strings to JobState.
        You can refine this later (e.g. COMPLETED, FAILED, CANCELLED, etc.).
        """
        text = raw.upper()
        if "PENDING" in text or "PD" in text:
            return JobState.PENDING
        if "RUNNING" in text or "R " in text:
            return JobState.RUNNING
        if "COMPLETED" in text or "CD" in text:
            return JobState.COMPLETED
        if "FAILED" in text or "F " in text or "TIMEOUT" in text:
            return JobState.FAILED
        return JobState.UNKNOWN
    

class BoltzJobManager(BaseJobManager):
    """
    Job manager specialized for Boltz jobs.

    Responsibilities:
      - create per-job dirs on the HPC
      - upload YAML input
      - render a Slurm script from a template
      - submit via sbatch
      - query status via sacct/squeue
      - download results when done
    """

    def __init__(
        self,
        hpc: "HPCClient",
        remote_base_dir: str,
        local_output_dir: str,
        job_store_path: str = "boltz_jobs.json",
        slurm_template_path: str | None = None,
        resources_dir: str | None = None,
        cache_dir_remote: str | None = None,
        partition: str = "tropshalab",   #TODO: may want to change the default
        qos: str = "gpu_access",
    ) -> None:
        super().__init__(
            hpc_client=hpc,
            remote_base_dir=remote_base_dir,
            local_output_dir=local_output_dir,
            job_store_path=job_store_path,
            tool_name="boltz",
        )
        # Load slurm template from file if provided
        if slurm_template_path and Path(slurm_template_path).exists():
            self.slurm_template = Path(slurm_template_path).read_text()
        else:
            self.slurm_template = self._default_template()
        
        self.resources_dir = resources_dir or "$BOLTZ_RESOURCES_DIR"
        self.cache_dir_remote = cache_dir_remote or "$HOME/.boltz"
        self.partition = partition
        self.qos = qos

    # ----- public interface used by FastAPI -----

    def submit_job(self, yaml_text: str) -> JobRecord:
        job_id = self._create_job_id()

        # Remote dirs for this job
        remote_job_dir = f"{self.remote_base_dir}/{job_id}"
        remote_input_dir = f"{remote_job_dir}/input"
        remote_output_dir = f"{remote_job_dir}/output"
        remote_logs_dir = f"{remote_job_dir}/logs"

        # Make dirs on HPC
        cmd = f"mkdir -p {remote_input_dir} {remote_output_dir} {remote_logs_dir}"
        rc, out, err = self.hpc.run(cmd)
        if rc != 0:
            raise RuntimeError(f"Failed to create remote dirs: {err}")

        # Upload YAML
        yaml_filename = f"{job_id}.yaml"
        local_tmp = Path(f"/tmp/{yaml_filename}")
        local_tmp.write_text(yaml_text, encoding="utf-8")
        self.hpc.put_file(str(local_tmp), f"{remote_input_dir}/{yaml_filename}")

        # Render Slurm script
        slurm_text = self._render_slurm_script(
            job_id=job_id,
            remote_input_dir=remote_input_dir,
            remote_output_dir=remote_output_dir,
            remote_logs_dir=remote_logs_dir,
            yaml_filename=yaml_filename,
        )

        local_slurm = Path(f"/tmp/{job_id}.sbatch")
        local_slurm.write_text(slurm_text, encoding="utf-8")
        remote_slurm = f"{remote_job_dir}/run_{job_id}.sbatch"
        self.hpc.put_file(str(local_slurm), remote_slurm)

        # Submit via sbatch
        rc, out, err = self.hpc.run(f"cd {remote_job_dir} && sbatch {remote_slurm}")
        if rc != 0:
            raise RuntimeError(f"sbatch failed: {err}")

        # Parse Slurm job id: "Submitted batch job 123456"
        slurm_job_id = out.strip().split()[-1]

        record = JobRecord(
            job_id=job_id,
            slurm_job_id=slurm_job_id,
            tool_name=self.tool_name,
            remote_input_dir=remote_input_dir,
            remote_output_dir=remote_output_dir,
            remote_logs_dir=remote_logs_dir,
            state=JobState.PENDING,
            extra={"yaml_filename": yaml_filename},
        )
        self._save_record(record)
        return record

    def get_status(self, job_id: str) -> Optional[JobRecord]:
        record = self._load_record(job_id)
        if not record:
            return None

        # Try sacct first (for finished jobs), fall back to squeue
        rc, out, err = self.hpc.run(
            f"sacct -j {record.slurm_job_id} --format=State --noheader"
        )
        if rc == 0 and out.strip():
            state = self._parse_slurm_state(out)
        else:
            rc, out, err = self.hpc.run(f"squeue -j {record.slurm_job_id} -o '%T' -h")
            if rc == 0 and out.strip():
                state = self._parse_slurm_state(out)
            else:
                state = JobState.UNKNOWN

        record.state = state
        self._update_record(record)
        return record

    def fetch_results(self, job_id: str) -> Optional[JobRecord]:
        record = self._load_record(job_id)
        if not record:
            return None

        # Ensure job is completed
        record = self.get_status(job_id) or record
        if record.state != JobState.COMPLETED:
            return record  # or raise

        # Download output dir
        local_dir = self.local_output_dir / job_id
        local_dir.mkdir(parents=True, exist_ok=True)

        #TODO: you probably only want predictions dir from outputs, check Boltz output structure
        self.hpc.get_dir(record.remote_output_dir, str(local_dir))
        record.local_output_dir = str(local_dir)
        self._update_record(record)
        return record

    # ----- internal helpers -----

    def _default_template(self) -> str:
        # Simplified from your boltz_template.sh
        return """#!/bin/bash
#SBATCH --job-name={JOB_NAME}
#SBATCH --partition={PARTITION}
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=32
#SBATCH --mem=64GB
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00
#SBATCH --qos={QOS}
#SBATCH --output={REMOTE_LOGS_DIR}/{JOB_NAME}-%j.out

hostname
nvidia-smi

module load apptainer/1.4.1

export BOLTZ_INPUT_DIR={REMOTE_INPUT_DIR}
export BOLTZ_OUTPUT_DIR={REMOTE_OUTPUT_DIR}
export BOLTZ_RESOURCES_DIR={BOLTZ_RESOURCES_DIR}
export BOLTZ_IMAGE=${{BOLTZ_RESOURCES_DIR}}/image/boltz.sif
export BOLTZ_CACHE_DIR={REMOTE_CACHE_DIR}
mkdir -p $BOLTZ_OUTPUT_DIR

apptainer exec \\
    --nv \\
    --bind $BOLTZ_INPUT_DIR:/root/boltz_input \\
    --bind $BOLTZ_OUTPUT_DIR:/root/boltz_output \\
    --bind $BOLTZ_CACHE_DIR:/root/.boltz \\
    $BOLTZ_IMAGE \\
    boltz predict /root/boltz_input/{YAML_FILENAME} \\
    --cache=/root/.boltz \\
    --out_dir=/root/boltz_output
"""

    def _render_slurm_script(
        self,
        job_id: str,
        remote_input_dir: str,
        remote_output_dir: str,
        remote_logs_dir: str,
        yaml_filename: str,
    ) -> str:
        # You can adjust REMOTE_CACHE_DIR if you want per-job vs shared cache
        remote_cache_dir = f"{self.remote_base_dir}/cache"
        return self.slurm_template.format(
            JOB_NAME=job_id,
            PARTITION=self.partition,
            QOS=self.qos,
            REMOTE_INPUT_DIR=remote_input_dir,
            REMOTE_OUTPUT_DIR=remote_output_dir,
            REMOTE_LOGS_DIR=remote_logs_dir,
            BOLTZ_RESOURCES_DIR=self.resources_dir,
            REMOTE_CACHE_DIR=remote_cache_dir,
            YAML_FILENAME=yaml_filename,
        )

    # High-level API methods expected by hpc_server.py
    def submit_yaml_text(self, yaml_text: str, job_id_override: str | None = None) -> Dict[str, Any]:
        """Submit a YAML text as a Boltz job and return metadata dict."""
        try:
            record = self.submit_job(yaml_text)
            return {
                "job_id": record.job_id,
                "slurm_job_id": record.slurm_job_id,
                "status": "pending",  # jobs start as pending
                "outputs_dir": None,  # will be set when results are fetched
            }
        except Exception as e:
            raise RuntimeError(f"Failed to submit job: {e}")

    def get_job(self, job_id: str) -> Dict[str, Any] | None:
        """Get job status and results. Downloads results if job completed."""
        record = self.get_status(job_id)
        if not record:
            return None
        
        # Convert job state to API status
        status_map = {
            JobState.PENDING: "pending",
            JobState.RUNNING: "running", 
            JobState.COMPLETED: "completed",
            JobState.FAILED: "failed",
            JobState.UNKNOWN: "unknown"
        }
        api_status = status_map.get(record.state, "unknown")
        
        result = {
            "job_id": record.job_id,
            "slurm_job_id": record.slurm_job_id,
            "status": api_status,
            "outputs_dir": record.local_output_dir,
            "affinity": None,
            "confidence": None,
            "cif_path": None,
        }
        
        # If job completed and results not yet downloaded, fetch them
        if record.state == JobState.COMPLETED and not record.local_output_dir:
            try:
                record = self.fetch_results(job_id)
                result["outputs_dir"] = record.local_output_dir
            except Exception as e:
                print(f"Warning: Failed to fetch results for job {job_id}: {e}")
                
        # Parse results if available
        if record.local_output_dir and Path(record.local_output_dir).exists():
            self._parse_boltz_results(record.local_output_dir, result)
            
        return result

    def get_cif_path(self, job_id: str) -> str | None:
        """Get local CIF file path for a completed job."""
        record = self._load_record(job_id)
        if not record or not record.local_output_dir:
            return None
            
        output_dir = Path(record.local_output_dir)
        paths = self._pred_paths(job_id, output_dir)
        
        if paths["cif"].exists():
            return str(paths["cif"])
                
        return None
    
    def _predictions_base(self, out_dir: Path) -> Path | None:
        """Find the predictions base directory when standard path doesn't exist."""
        # Look for any boltz_results_* directory
        for results_dir in out_dir.glob("boltz_results_*"):
            pred_dir = results_dir / "predictions"
            if pred_dir.is_dir():
                # Find the first job directory inside predictions
                for job_dir in pred_dir.iterdir():
                    if job_dir.is_dir():
                        return job_dir
        return None
    
    def _pred_paths(self, job_id: str, out_dir: Path) -> dict[str, Path]:
        """Define paths for Boltz prediction outputs."""
        pred_dir = out_dir / f"boltz_results_{job_id}" / "predictions" / job_id
        if not pred_dir.is_dir():
            alt = self._predictions_base(out_dir)
            if alt is not None:
                pred_dir = alt
        return {
            "cif": pred_dir / f"{job_id}_model_0.cif",
            "confidence": pred_dir / f"confidence_{job_id}_model_0.json",
            "affinity": pred_dir / f"affinity_{job_id}.json",
            "pred_dir": pred_dir,
        }
    
    def _parse_boltz_results(self, output_dir: str, result_dict: Dict[str, Any]) -> None:
        """Parse Boltz output files and populate result dict."""
        output_path = Path(output_dir)
        job_id = result_dict["job_id"]
        
        # Get the correct paths for Boltz outputs
        paths = self._pred_paths(job_id, output_path)
        
        # Parse affinity JSON
        if paths["affinity"].exists():
            try:
                affinity_data = json.loads(paths["affinity"].read_text())
                result_dict["affinity"] = affinity_data
            except Exception as e:
                print(f"Warning: Could not parse affinity file {paths['affinity']}: {e}")
                
        # Parse confidence JSON  
        if paths["confidence"].exists():
            try:
                confidence_data = json.loads(paths["confidence"].read_text())
                result_dict["confidence"] = confidence_data
            except Exception as e:
                print(f"Warning: Could not parse confidence file {paths['confidence']}: {e}")
                
        # Set CIF path
        if paths["cif"].exists():
            result_dict["cif_path"] = str(paths["cif"])

