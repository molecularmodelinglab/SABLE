"""Run service for managing optimization runs in the database."""

from typing import Optional, List, Dict, Any, Iterable
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError, StaleDataError
from sqlalchemy import desc, func
from sqlalchemy.exc import ProgrammingError, OperationalError

from server.models.run import Run as RunModel, RunLog
from server.schemas.run import RunInfo


class RunService:
    """Service for managing optimization runs."""

    def create_run(
        self,
        db: Session,
        run_id: str,
        user_id,
        session_id,
        prompt: str,
        starting_molecules: Optional[List[str]] = None,
        note: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        status: str = "pending"
    ) -> RunModel:
        """Create a new run in the database.

        Args:
            db: Database session
            run_id: Unique run identifier
            user_id: User UUID
            prompt: Optimization prompt
            max_iterations: Maximum iterations
            batch_size: Batch size
            note: Optional note
            metadata: Additional metadata

        Returns:
            Created run model
        """
        run = RunModel(
            id=run_id,
            user_id=user_id,
            session_id=session_id,
            prompt=prompt,
            status=status,
            note=note,
            starting_molecules=starting_molecules or [],
            summary_available=False,
            results_available=False,
            extra_metadata=extra_metadata or {}
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        return run

    def get_run(
        self,
        db: Session,
        run_id: str,
        user_id: Optional[str] = None
    ) -> Optional[RunModel]:
        """Get a run by ID.

        Args:
            db: Database session
            run_id: Run identifier
            user_id: Optional user ID filter

        Returns:
            Run model or None
        """
        query = db.query(RunModel).filter(RunModel.id == run_id)

        if user_id:
            query = query.filter(RunModel.user_id == user_id)

        return query.first()

    def list_runs(
        self,
        db: Session,
        user_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[RunModel]:
        """List runs for a user.

        Args:
            db: Database session
            user_id: User UUID
            limit: Maximum number of runs to return
            offset: Offset for pagination

        Returns:
            List of run models
        """
        return db.query(RunModel).filter(
            RunModel.user_id == user_id
        ).order_by(
            desc(RunModel.created_at)
        ).limit(limit).offset(offset).all()

    def count_runs_with_status(
        self,
        db: Session,
        statuses: Iterable[str]
    ) -> int:
        """Count runs matching any of the provided statuses."""
        statuses = list(set(statuses))
        query = db.query(func.count(RunModel.id))
        if statuses:
            query = query.filter(RunModel.status.in_(statuses))
        try:
            return query.scalar() or 0
        except (ProgrammingError, OperationalError):
            # Database schema may not be initialized yet (e.g., during test bootstrap)
            return 0

    def get_next_queued_run(self, db: Session) -> Optional[RunModel]:
        """Fetch next queued run in FIFO order."""
        try:
            return db.query(RunModel).filter(
                RunModel.status == "queued"
            ).order_by(
                RunModel.created_at.asc(),
                RunModel.updated_at.asc()
            ).first()
        except (ProgrammingError, OperationalError):
            return None

    def update_run_status(
        self,
        db: Session,
        run_id: str,
        status: str,
        exit_reason: Optional[str] = None
    ) -> Optional[RunModel]:
        """Update run status.

        Args:
            db: Database session
            run_id: Run identifier
            status: New status
            exit_reason: Optional exit reason

        Returns:
            Updated run model or None
        """
        run = db.query(RunModel).filter(RunModel.id == run_id).first()

        if run:
            run.status = status
            run.updated_at = datetime.now()

            if exit_reason:
                run.exit_reason = exit_reason

            if status == "completed":
                run.completed_at = datetime.now()

            try:
                db.commit()
            except StaleDataError:
                db.rollback()
                return None
            db.refresh(run)

        return run

    def update_run_molecules(
        self,
        db: Session,
        run_id: str,
        starting_molecules: List[str]
    ) -> Optional[RunModel]:
        """Update run's starting molecules.

        Args:
            db: Database session
            run_id: Run identifier
            starting_molecules: List of SMILES strings

        Returns:
            Updated run model or None
        """
        run = db.query(RunModel).filter(RunModel.id == run_id).first()

        if run:
            run.starting_molecules = starting_molecules
            run.updated_at = datetime.now()
            try:
                db.commit()
            except StaleDataError:
                db.rollback()
                return None
            db.refresh(run)

        return run

    def add_run_log(
        self,
        db: Session,
        run_id: str,
        level: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        node: Optional[str] = None,
        iteration: Optional[int] = None
    ) -> RunLog:
        """Add a log entry for a run.

        Args:
            db: Database session
            run_id: Run identifier
            level: Log level (INFO, ERROR, etc.)
            message: Log message
            data: Additional log data
            node: Node name
            iteration: Iteration number

        Returns:
            Created log entry
        """
        log = RunLog(
            run_id=run_id,
            level=level,
            message=message,
            data=data or {},
            node=node,
            iteration=iteration
        )

        db.add(log)
        db.commit()
        db.refresh(log)

        return log

    def get_run_logs(
        self,
        db: Session,
        run_id: str,
        limit: Optional[int] = None,
        level: Optional[str] = None
    ) -> List[RunLog]:
        """Get logs for a run.

        Args:
            db: Database session
            run_id: Run identifier
            limit: Optional limit
            level: Optional level filter

        Returns:
            List of log entries
        """
        query = db.query(RunLog).filter(RunLog.run_id == run_id)

        if level:
            query = query.filter(RunLog.level == level)

        query = query.order_by(RunLog.timestamp)

        if limit:
            query = query.limit(limit)

        return query.all()

    def run_to_info(
        self,
        run: RunModel,
        summary_available: bool = False,
        results_available: bool = False,
        paths: Optional[Dict[str, str]] = None
    ) -> RunInfo:
        """Convert run model to RunInfo schema.

        Args:
            run: Run model
            summary_available: Whether summary is available
            results_available: Whether results are available
            paths: File paths

        Returns:
            RunInfo schema
        """
        from server.models.user import User

        # Get user info if available
        username = None
        user_obj = None
        if isinstance(getattr(run, "__dict__", {}), dict):
            user_obj = run.__dict__.get("user")

        if user_obj is not None:
            try:
                username = user_obj.username
            except DetachedInstanceError:
                username = None

        metadata = run.extra_metadata if isinstance(run.extra_metadata, dict) else {}

        return RunInfo(
            id=run.id,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
            prompt=getattr(run, "prompt", None),
            exit_reason=run.exit_reason,
            summary_available=summary_available,
            results_available=results_available,
            paths=paths or metadata.get("paths", {}),
            note=run.note,
            user_id=str(run.user_id),
            username=username,
            session_id=None,  # Deprecated
            starting_molecules=run.starting_molecules or []
        )

    def delete_run(
        self,
        db: Session,
        run_id: str,
        user_id: str
    ) -> bool:
        """Delete a run.

        Args:
            db: Database session
            run_id: Run identifier
            user_id: User UUID (for authorization)

        Returns:
            True if deleted, False if not found
        """
        run = db.query(RunModel).filter(
            RunModel.id == run_id,
            RunModel.user_id == user_id
        ).first()

        if run:
            # Delete associated logs
            db.query(RunLog).filter(RunLog.run_id == run_id).delete()
            # Delete run
            db.delete(run)
            db.commit()
            return True

        return False


# Global run service instance
run_service = RunService()
