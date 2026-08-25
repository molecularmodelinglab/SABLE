import json
import os
import stat
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from celery import shared_task
from server.audit import audit_logger, AuditEventType, AuditSeverity
from utils.hpc_client import HPCClient, SSHConfig
from utils.job_managers import BoltzJobManager, JobState

def get_job_manager() -> BoltzJobManager:
    """Factory to create a BoltzJobManager instance from environment config."""
    
    hpc_host = os.getenv("HPC_HOST")
    if not hpc_host:
        raise ValueError("HPC_HOST environment variable not set")
    key_path_env = os.getenv("HPC_SSH_KEY")
    default_key = str(Path.home() / ".ssh" / "id_rsa")
    key_filename = key_path_env or (default_key if os.path.isfile(default_key) else None)

    if key_filename and (not os.path.isfile(key_filename) or not os.access(key_filename, os.R_OK)):
        key_content = os.getenv("HPC_SSH_KEY_CONTENT")
        if key_content:
            tf = tempfile.NamedTemporaryFile(prefix="hpc_key_", delete=False)
            tf.write(key_content.encode("utf-8"))
            tf.flush()
            tf.close()
            os.chmod(tf.name, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            key_filename = tf.name
        else:
            if key_path_env:
                raise ValueError(
                    f"Configured SSH key '{key_path_env}' is not readable by the process. "
                    "Either mount the key into the container with correct permissions, "
                    "or set the key contents via the HPC_SSH_KEY_CONTENT environment variable."
                )
    elif not key_filename:
        key_content = os.getenv("HPC_SSH_KEY_CONTENT")
        if key_content:
            tf = tempfile.NamedTemporaryFile(prefix="hpc_key_", delete=False)
            tf.write(key_content.encode("utf-8"))
            tf.flush()
            tf.close()
            os.chmod(tf.name, stat.S_IRUSR | stat.S_IWUSR)
            key_filename = tf.name

    ssh_config = SSHConfig(
        hostname=hpc_host,
        username=os.getenv("HPC_USER", os.getenv("USER", "root")),
        key_filename=key_filename,
        password=os.getenv("HPC_PASSWORD"),
    )
    
    hpc = HPCClient(ssh_config)
    
    return BoltzJobManager(
        hpc=hpc,
        remote_base_dir=os.getenv("REMOTE_BASE_DIR", "/tmp/boltz_jobs"),
        local_output_dir=os.getenv("LOCAL_OUTPUT_DIR", "./data/hpc_outputs"),
        resources_dir=os.getenv("BOLTZ_RESOURCES_DIR"),
        job_store_path=os.getenv("JOB_STORE_PATH", "./data/boltz_jobs.json"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    )


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def poll_boltz_job_once(job_id: str, *, emit_audit: bool = True) -> Dict[str, Any]:
    manager = get_job_manager()
    record = manager.get_status(job_id)
    if not record:
        return {"status": "unknown", "job_id": job_id}

    result = {
        "job_id": record.job_id,
        "slurm_job_id": record.slurm_job_id,
        "status": record.state.value.lower(),
    }

    if record.state == JobState.COMPLETED:
        if not record.local_output_dir:
            manager.fetch_results(job_id)
            record = manager.get_status(job_id) or record
            if emit_audit:
                audit_logger.log(
                    event_type=AuditEventType.SYSTEM_EVENT,
                    message=f"Boltz job {job_id} completed and results downloaded",
                    severity=AuditSeverity.INFO,
                    details={
                        "job_id": job_id,
                        "slurm_job_id": record.slurm_job_id,
                        "output_dir": record.local_output_dir,
                    },
                )

        result["outputs_dir"] = record.local_output_dir
        if record.local_output_dir:
            local_output_path = Path(record.local_output_dir)
            try:
                paths = manager._pred_paths(record.job_id, local_output_path)
                affinity_data = _load_json_file(paths["affinity"])
                confidence_data = _load_json_file(paths["confidence"])
                if affinity_data is not None:
                    result["affinity"] = affinity_data
                if confidence_data is not None:
                    result["confidence"] = confidence_data
                if paths["cif"].is_file():
                    result["cif_path"] = str(paths["cif"])
            except Exception:
                pass

    elif record.state == JobState.FAILED:
        result["error"] = "Job failed on HPC"
        if emit_audit:
            audit_logger.log(
                event_type=AuditEventType.SYSTEM_ERROR,
                message=f"Boltz job {job_id} failed on HPC",
                severity=AuditSeverity.ERROR,
                details={
                    "job_id": job_id,
                    "slurm_job_id": record.slurm_job_id,
                },
            )

    return result

@shared_task(bind=True, name="server.tasks.slurm.submit_boltz_job")
def submit_boltz_job(self, yaml_text: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Submit a Boltz job to the HPC cluster.
    Returns the job record as a dict.
    """
    manager = get_job_manager()
    try:
        record = manager.submit_job(yaml_text, job_id=job_id)
        
        audit_logger.log(
            event_type=AuditEventType.SYSTEM_EVENT,
            message=f"Submitted Boltz job {record.job_id} (Slurm: {record.slurm_job_id})",
            severity=AuditSeverity.INFO,
            details={
                "job_id": record.job_id,
                "slurm_job_id": record.slurm_job_id,
                "task_id": self.request.id
            }
        )
        
        return {
            "job_id": record.job_id,
            "slurm_job_id": record.slurm_job_id,
            "status": "submitted"
        }
    except Exception as e:
        audit_logger.log(
            event_type=AuditEventType.SYSTEM_ERROR,
            message=f"Failed to submit Boltz job: {str(e)}",
            severity=AuditSeverity.ERROR,
            details={"error": str(e), "task_id": self.request.id}
        )
        raise e

@shared_task(bind=True, name="server.tasks.slurm.poll_boltz_job")
def poll_boltz_job(self, job_id: str) -> Dict[str, Any]:
    """Celery wrapper that delegates to Poll helper."""
    return poll_boltz_job_once(job_id)

@shared_task(name="server.tasks.slurm.monitor_slurm_jobs")
def monitor_slurm_jobs() -> None:
    """
    Periodic task to check status of all pending/running jobs.
    """
    if os.getenv("BOLTZ_EXECUTION_MODE", "api").lower() != "celery":
        return

    manager = get_job_manager()
    active_jobs = manager.list_active_jobs()
    
    for job in active_jobs:
        try:
            old_state = job.state
            updated_record = manager.get_status(job.job_id)
            
            if updated_record and updated_record.state != old_state:
                audit_logger.log(
                    event_type=AuditEventType.SYSTEM_EVENT,
                    message=f"Job {job.job_id} state changed: {old_state} -> {updated_record.state}",
                    severity=AuditSeverity.INFO,
                    details={"job_id": job.job_id, "new_state": str(updated_record.state)}
                )
                
                if updated_record.state == JobState.COMPLETED:
                    manager.fetch_results(job.job_id)
                    try:
                        cif_path = manager.get_cif_path(job.job_id)
                        if cif_path:
                            target_dir = Path(f"/data/runs/{job.job_id}/artifacts/boltz_cifs")
                            target_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(cif_path, target_dir / Path(cif_path).name)
                            audit_logger.log(
                                event_type=AuditEventType.SYSTEM_EVENT,
                                message=f"Copied CIF artifacts for job {job.job_id}",
                                severity=AuditSeverity.INFO,
                                details={"target_dir": str(target_dir)}
                            )
                    except Exception as e:
                        print(f"Error copying artifacts for job {job.job_id}: {e}")
                        audit_logger.log(
                            event_type=AuditEventType.SYSTEM_ERROR,
                            message=f"Failed to copy artifacts for job {job.job_id}: {e}",
                            severity=AuditSeverity.ERROR,
                            details={"error": str(e)}
                        )
                    
        except Exception as e:
            print(f"Error monitoring job {job.job_id}: {e}")
