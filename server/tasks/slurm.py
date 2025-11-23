import os
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
        
    ssh_config = SSHConfig(
        hostname=hpc_host,
        username=os.getenv("HPC_USER", os.getenv("USER", "root")),
        key_filename=os.getenv("HPC_SSH_KEY", str(Path.home() / ".ssh" / "id_rsa")),
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

@shared_task(bind=True, name="server.tasks.slurm.submit_boltz_job")
def submit_boltz_job(self, yaml_text: str) -> Dict[str, Any]:
    """
    Submit a Boltz job to the HPC cluster.
    Returns the job record as a dict.
    """
    manager = get_job_manager()
    try:
        record = manager.submit_job(yaml_text)
        
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
    """
    Check the status of a Boltz job.
    If completed, downloads results.
    """
    manager = get_job_manager()
    
    # Check status (this updates the local record)
    record = manager.get_status(job_id)
    
    if not record:
        return {"status": "unknown", "job_id": job_id}
        
    result = {
        "job_id": record.job_id,
        "slurm_job_id": record.slurm_job_id,
        "status": record.state.value.lower(),
    }
    
    if record.state == JobState.COMPLETED:
        # Download results if not already done
        if not record.local_output_dir:
            manager.fetch_results(job_id)
            # Reload record to get local path
            record = manager.get_status(job_id)
            
            audit_logger.log(
                event_type=AuditEventType.SYSTEM_EVENT,
                message=f"Boltz job {job_id} completed and results downloaded",
                severity=AuditSeverity.INFO,
                details={
                    "job_id": job_id,
                    "slurm_job_id": record.slurm_job_id,
                    "output_dir": record.local_output_dir
                }
            )
            
        result["outputs_dir"] = record.local_output_dir
        
    elif record.state == JobState.FAILED:
        result["error"] = "Job failed on HPC"
        audit_logger.log(
            event_type=AuditEventType.SYSTEM_ERROR,
            message=f"Boltz job {job_id} failed on HPC",
            severity=AuditSeverity.ERROR,
            details={
                "job_id": job_id,
                "slurm_job_id": record.slurm_job_id
            }
        )
        
    return result

@shared_task(name="server.tasks.slurm.monitor_slurm_jobs")
def monitor_slurm_jobs() -> None:
    """
    Periodic task to check status of all pending/running jobs.
    """
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
                    
        except Exception as e:
            print(f"Error monitoring job {job.job_id}: {e}")
