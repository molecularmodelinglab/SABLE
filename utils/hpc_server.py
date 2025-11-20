'''
    HPC API server for submitting and tracking jobs on a remote HPC cluster via SSH.
'''

import os
import uuid
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Header,
    HTTPException,
    Depends,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .hpc_client import HPCClient, SSHConfig          # your generic SSH client
from .job_managers import BoltzJobManager   # your job manager with Boltz child


app = FastAPI(title="Boltz-2 HPC Job API")


# Config & helpers

def getenv_bool(key: str, default: str = "false") -> bool:
    return os.environ.get(key, default).lower() in ("1", "true", "yes", "on")


def get_cfg() -> Dict[str, Any]:
    """
    Central place for configuration. Reads all env vars needed by the server.
    Adjust paths to match your HPC setup.
    """
    return {
        # Where results are stored locally after downloading from HPC
        "LOCAL_OUTPUT_DIR": os.environ.get("OUTPUT_DIR", "./outputs"),

        # Base dir on the HPC where job subdirectories will live
        "REMOTE_BASE_DIR": os.environ.get("REMOTE_BASE_DIR", "/nas/users/you/boltz/jobs"),

        # HPC SSH connection
        "HPC_HOST": os.environ.get("HPC_HOST", ""),
        "HPC_USER": os.environ.get("HPC_USER", os.environ.get("USER", "")),
        "SSH_KEY_PATH": os.environ.get(
            "HPC_SSH_KEY", str(Path.home() / ".ssh" / "id_rsa")
        ),

        # Slurm + Boltz specifics
        "SLURM_TEMPLATE": os.environ.get("SLURM_TEMPLATE", "boltz_template.sh"),
        "BOLTZ_RESOURCES_DIR": os.environ.get(
            "BOLTZ_RESOURCES_DIR", "/nas/users/you/boltz/2.2.1"
        ),
        "BOLTZ_CACHE_DIR_REMOTE": os.environ.get(
            "BOLTZ_CACHE_DIR_REMOTE", "/nas/users/you/.boltz"
        ),

        # API auth
        "API_TOKEN": os.environ.get("API_TOKEN", ""),

        # Testing helpers (optional)
        "TEST_ALLOW_FIXED_JOB_ID": getenv_bool("TEST_ALLOW_FIXED_JOB_ID", "false"),
    }


def _sanitize_job_id(s: str) -> Optional[str]:
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9_-]", "", s)
    return s[:32] or None


# Auth

def require_auth(authorization: str = Header(default="")):
    cfg = get_cfg()
    token = cfg["API_TOKEN"]
    if not token:
        # auth disabled if no token set (fine for internal testing)
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    supplied = authorization.split(" ", 1)[1].strip()
    if supplied != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


# Dependency: get a JobManager instance

def get_job_manager() -> BoltzJobManager:
    """
    Builds a fresh HPCClient + BoltzJobManager for each request.
    If you want connection reuse, you can optimize later, but this is simple & clear.
    """
    cfg = get_cfg()

    if not cfg["HPC_HOST"]:
        raise RuntimeError("HPC_HOST environment variable must be set")

    ssh_config = SSHConfig(
        hostname=cfg["HPC_HOST"],
        username=cfg["HPC_USER"],
        key_filename=cfg["SSH_KEY_PATH"],
    )
    
    hpc = HPCClient(ssh_config)

    manager = BoltzJobManager(
        hpc=hpc,
        remote_base_dir=cfg["REMOTE_BASE_DIR"],
        local_output_dir=cfg["LOCAL_OUTPUT_DIR"],
        slurm_template_path=cfg["SLURM_TEMPLATE"],
        resources_dir=cfg["BOLTZ_RESOURCES_DIR"],
        cache_dir_remote=cfg["BOLTZ_CACHE_DIR_REMOTE"],
    )
    return manager


# Request / response models

class YamlBody(BaseModel):
    yaml_text: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    slurm_job_id: Optional[str] = None
    outputs_dir: Optional[str] = None
    affinity: Optional[Dict[str, Any]] = None
    confidence: Optional[Dict[str, Any]] = None
    cif_url: Optional[str] = None


# Routes

@app.post("/submit_yaml_text", dependencies=[Depends(require_auth)], response_model=JobResponse)
async def submit_yaml_text(
    body: YamlBody,
    x_test_job_id: Optional[str] = Header(default=None),
    manager: BoltzJobManager = Depends(get_job_manager),
):
    """
    Submit a Boltz job using YAML text (JSON body: {"yaml_text": "..."}).

    Returns a job object with at least job_id and status.
    affinity/confidence will be None initially; the client polls /jobs/{job_id}.
    """
    cfg = get_cfg()

    job_id: Optional[str] = None
    if cfg.get("TEST_ALLOW_FIXED_JOB_ID") and x_test_job_id:
        job_id = _sanitize_job_id(x_test_job_id)

    if not job_id:
        job_id = str(uuid.uuid4())[:8]

    # BoltzJobManager is expected to:
    # - create remote job dir
    # - upload YAML
    # - create & submit Slurm script
    # - return a dict with job_id, slurm_job_id, status
    meta = manager.submit_yaml_text(yaml_text=body.yaml_text, job_id_override=job_id)

    return JobResponse(
        job_id=meta["job_id"],
        status=meta.get("status", "pending"),
        slurm_job_id=meta.get("slurm_job_id"),
        outputs_dir=None,
        affinity=None,
        confidence=None,
        cif_url=None,
    )


@app.post("/submit_yaml_file", dependencies=[Depends(require_auth)], response_model=JobResponse)
async def submit_yaml_file(
    file: UploadFile = File(...),
    manager: BoltzJobManager = Depends(get_job_manager),
):
    """
    Optional convenience endpoint to submit a YAML file upload.

    Not required by your agent, but handy for manual testing.
    """
    contents = await file.read()
    yaml_text = contents.decode("utf-8")

    job_id = str(uuid.uuid4())[:8]
    meta = manager.submit_yaml_text(yaml_text=yaml_text, job_id_override=job_id)

    return JobResponse(
        job_id=meta["job_id"],
        status=meta.get("status", "pending"),
        slurm_job_id=meta.get("slurm_job_id"),
        outputs_dir=None,
        affinity=None,
        confidence=None,
        cif_url=None,
    )


@app.get("/jobs/{job_id}", dependencies=[Depends(require_auth)], response_model=JobResponse)
async def get_job(job_id: str, manager: BoltzJobManager = Depends(get_job_manager)):
    """
    Poll job status and, once complete, return results.

    Expected behavior from BoltzJobManager.get_job(job_id):

    - Looks up job metadata (slurm_job_id, remote dirs, etc.).
    - Checks Slurm state via HPCClient.
    - If job just completed and results not yet fetched:
        - download remote output dir to local_output_dir/job_id
        - parse affinity/confidence JSON, CIF path
        - update stored metadata
    - Returns a dict with keys:
        job_id, status, slurm_job_id, outputs_dir, affinity, confidence, cif_path
    """
    meta = manager.get_job(job_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    outputs_dir = meta.get("outputs_dir")
    cif_path = meta.get("cif_path")

    cif_url: Optional[str] = None
    if cif_path:
        # Expose via /files/{job_id}/model_0.cif
        cif_url = f"/files/{job_id}/model_0.cif"

    return JobResponse(
        job_id=meta["job_id"],
        status=meta.get("status", "pending"),
        slurm_job_id=meta.get("slurm_job_id"),
        outputs_dir=str(outputs_dir) if outputs_dir else None,
        affinity=meta.get("affinity"),
        confidence=meta.get("confidence"),
        cif_url=cif_url,
    )


@app.get(
    "/files/{job_id}/model_0.cif",
    dependencies=[Depends(require_auth)],
    response_class=FileResponse,
)
async def download_cif(job_id: str, manager: BoltzJobManager = Depends(get_job_manager)):
    """
    Serve the CIF file associated with a completed job.
    BoltzJobManager.get_cif_path(job_id) should return a local Path.
    """
    cif_path = manager.get_cif_path(job_id)
    if cif_path is None or not Path(cif_path).is_file():
        raise HTTPException(status_code=404, detail="CIF file not found")

    return FileResponse(
        path=str(cif_path),
        filename="model_0.cif",
        media_type="chemical/x-cif",
    )
