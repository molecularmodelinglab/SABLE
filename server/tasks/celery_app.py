import os
from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "sable_tasks",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[
        "server.tasks.workflow",
        "server.tasks.slurm",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "monitor-slurm-jobs-every-minute": {
        "task": "server.tasks.slurm.monitor_slurm_jobs",
        "schedule": 60.0,
    },
}

if __name__ == "__main__":
    celery_app.start()
