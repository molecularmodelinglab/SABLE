"""Comprehensive experiment logging system for scientific reproducibility.

Tracks all experiments, prompts, results, errors, and user actions to ensure
full auditability and reproducibility of scientific workflows.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field
import json
from uuid import uuid4


class ExperimentStatus(str, Enum):
    """Status of an experiment."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class ExperimentError(BaseModel):
    """Detailed error information for failed experiments."""
    message: str
    error_type: str
    stack_trace: Optional[str] = None
    code: Optional[str] = None
    node: Optional[str] = Field(None, description="Graph node where error occurred")
    timestamp: datetime = Field(default_factory=datetime.now)
    recoverable: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExperimentMetrics(BaseModel):
    """Performance and resource metrics for an experiment."""
    duration_seconds: Optional[float] = None
    iterations_completed: int = 0
    molecules_evaluated: int = 0
    molecules_generated: int = 0
    llm_calls: int = 0
    llm_tokens_used: int = 0
    characterization_calls: int = 0
    bo_iterations: int = 0
    peak_memory_mb: Optional[float] = None
    cpu_time_seconds: Optional[float] = None


class ExperimentCheckpoint(BaseModel):
    """Checkpoint data for experiment recovery."""
    checkpoint_id: str = Field(default_factory=lambda: f"chkpt_{uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=datetime.now)
    iteration: int
    state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    file_path: Optional[str] = None


class ExperimentLog(BaseModel):
    """Individual log entry within an experiment."""
    timestamp: datetime = Field(default_factory=datetime.now)
    level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL")
    message: str
    node: Optional[str] = None
    iteration: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class Experiment(BaseModel):
    """Complete experiment record with full audit trail."""
    
    # Identifiers
    id: str = Field(default_factory=lambda: f"exp_{uuid4().hex}")
    run_id: str
    session_id: str
    user_id: str
    username: str
    
    # Experiment details
    prompt: str = Field(description="Original user prompt/query")
    workflow_name: str = Field(default="molecular_optimization")
    status: ExperimentStatus = ExperimentStatus.PENDING
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Configuration
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow parameters (max_iterations, batch_size, etc.)"
    )
    parsed_arguments: Dict[str, Any] = Field(default_factory=dict)
    targets: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Results
    result: Optional[Dict[str, Any]] = None
    best_molecules: List[tuple[str, float]] = Field(default_factory=list)
    summary: Optional[str] = None
    
    # Error tracking
    error: Optional[ExperimentError] = None
    warnings: List[str] = Field(default_factory=list)
    
    # Metrics and monitoring
    metrics: ExperimentMetrics = Field(default_factory=ExperimentMetrics)
    
    # Logging and audit trail
    logs: List[ExperimentLog] = Field(default_factory=list)
    checkpoints: List[ExperimentCheckpoint] = Field(default_factory=list)
    
    # Reproducibility
    environment: Dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables, versions, hardware info"
    )
    git_commit: Optional[str] = None
    
    # Metadata
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    parent_experiment_id: Optional[str] = Field(
        None,
        description="ID of parent experiment if this is a continuation"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def add_log(
        self,
        message: str,
        level: str = "INFO",
        node: Optional[str] = None,
        iteration: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None
    ):
        """Add a log entry to the experiment."""
        log = ExperimentLog(
            message=message,
            level=level,
            node=node,
            iteration=iteration,
            data=data or {}
        )
        self.logs.append(log)
    
    def add_checkpoint(
        self,
        iteration: int,
        state_snapshot: Dict[str, Any],
        file_path: Optional[str] = None
    ) -> ExperimentCheckpoint:
        """Add a checkpoint for experiment recovery."""
        checkpoint = ExperimentCheckpoint(
            iteration=iteration,
            state_snapshot=state_snapshot,
            file_path=file_path
        )
        self.checkpoints.append(checkpoint)
        return checkpoint
    
    def mark_started(self):
        """Mark experiment as started."""
        self.status = ExperimentStatus.RUNNING
        self.started_at = datetime.now()
    
    def mark_completed(self, summary: Optional[str] = None):
        """Mark experiment as successfully completed."""
        self.status = ExperimentStatus.SUCCESS
        self.completed_at = datetime.now()
        if summary:
            self.summary = summary
        if self.started_at:
            self.metrics.duration_seconds = (
                datetime.now() - self.started_at
            ).total_seconds()
    
    def mark_failed(self, error: ExperimentError):
        """Mark experiment as failed with error details."""
        self.status = ExperimentStatus.FAILED
        self.completed_at = datetime.now()
        self.error = error
        if self.started_at:
            self.metrics.duration_seconds = (
                datetime.now() - self.started_at
            ).total_seconds()


class ExperimentLogger:
    """Manages experiment logging and persistence."""
    
    def __init__(self, data_root: Optional[Path] = None):
        self.data_root = data_root or Path("data")
        self.experiments_dir = self.data_root / "experiments"
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache of experiments
        self._experiments: Dict[str, Experiment] = {}
        
        # Load existing experiments
        self._load_experiments()
    
    def _load_experiments(self):
        """Load experiments from disk."""
        if not self.experiments_dir.exists():
            return
        
        for exp_file in self.experiments_dir.glob("exp_*.json"):
            try:
                with exp_file.open() as f:
                    data = json.load(f)
                    exp = Experiment(**data)
                    self._experiments[exp.id] = exp
            except Exception as e:
                print(f"Error loading experiment {exp_file}: {e}")
    
    def _save_experiment(self, experiment: Experiment):
        """Persist experiment to disk."""
        exp_file = self.experiments_dir / f"{experiment.id}.json"
        with exp_file.open("w") as f:
            json.dump(experiment.model_dump(mode="json"), f, indent=2, default=str)
    
    def create_experiment(
        self,
        run_id: str,
        session_id: str,
        user_id: str,
        username: str,
        prompt: str,
        parameters: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        parent_experiment_id: Optional[str] = None
    ) -> Experiment:
        """Create a new experiment."""
        experiment = Experiment(
            run_id=run_id,
            session_id=session_id,
            user_id=user_id,
            username=username,
            prompt=prompt,
            parameters=parameters or {},
            tags=tags or [],
            notes=notes,
            parent_experiment_id=parent_experiment_id
        )
        
        self._experiments[experiment.id] = experiment
        self._save_experiment(experiment)
        
        return experiment
    
    def get_experiment(self, experiment_id: str) -> Optional[Experiment]:
        """Retrieve an experiment by ID."""
        return self._experiments.get(experiment_id)
    
    def get_experiments_by_run(self, run_id: str) -> List[Experiment]:
        """Get all experiments for a run."""
        return [
            exp for exp in self._experiments.values()
            if exp.run_id == run_id
        ]
    
    def get_experiments_by_user(
        self,
        user_id: str,
        limit: Optional[int] = None
    ) -> List[Experiment]:
        """Get all experiments for a user."""
        experiments = [
            exp for exp in self._experiments.values()
            if exp.user_id == user_id
        ]
        experiments.sort(key=lambda x: x.created_at, reverse=True)
        if limit:
            experiments = experiments[:limit]
        return experiments
    
    def get_experiments_by_session(self, session_id: str) -> List[Experiment]:
        """Get all experiments in a session."""
        return [
            exp for exp in self._experiments.values()
            if exp.session_id == session_id
        ]
    
    def update_experiment(self, experiment: Experiment):
        """Update an existing experiment."""
        self._experiments[experiment.id] = experiment
        self._save_experiment(experiment)
    
    def search_experiments(
        self,
        status: Optional[ExperimentStatus] = None,
        user_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Experiment]:
        """Search experiments with filters."""
        results = list(self._experiments.values())
        
        if status:
            results = [exp for exp in results if exp.status == status]
        
        if user_id:
            results = [exp for exp in results if exp.user_id == user_id]
        
        if tags:
            results = [
                exp for exp in results
                if any(tag in exp.tags for tag in tags)
            ]
        
        if start_date:
            results = [exp for exp in results if exp.created_at >= start_date]
        
        if end_date:
            results = [exp for exp in results if exp.created_at <= end_date]
        
        results.sort(key=lambda x: x.created_at, reverse=True)
        return results
    
    def get_failed_experiments(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Experiment]:
        """Get all failed experiments for debugging."""
        experiments = [
            exp for exp in self._experiments.values()
            if exp.status == ExperimentStatus.FAILED
        ]
        
        if user_id:
            experiments = [exp for exp in experiments if exp.user_id == user_id]
        
        experiments.sort(key=lambda x: x.created_at, reverse=True)
        
        if limit:
            experiments = experiments[:limit]
        
        return experiments


# Global experiment logger instance
experiment_logger = ExperimentLogger()
