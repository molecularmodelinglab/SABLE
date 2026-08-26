"""Audit logging system for compliance and security tracking.

Tracks all user actions, system events, and data access for security auditing,
compliance, and debugging purposes.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field
import json
from uuid import uuid4


class AuditEventType(str, Enum):
    """Types of auditable events."""
    # Authentication
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    SESSION_CREATED = "session_created"
    SESSION_EXPIRED = "session_expired"
    AUTH_FAILED = "auth_failed"
    
    # Experiment operations
    EXPERIMENT_CREATED = "experiment_created"
    EXPERIMENT_STARTED = "experiment_started"
    EXPERIMENT_COMPLETED = "experiment_completed"
    EXPERIMENT_FAILED = "experiment_failed"
    EXPERIMENT_CANCELLED = "experiment_cancelled"
    
    # Data access
    DATA_READ = "data_read"
    DATA_WRITE = "data_write"
    DATA_DELETE = "data_delete"
    DATA_EXPORT = "data_export"
    
    # System events
    SYSTEM_EVENT = "system_event"
    SYSTEM_START = "system_start"
    SYSTEM_SHUTDOWN = "system_shutdown"
    CONFIG_CHANGED = "config_changed"
    ERROR_OCCURRED = "error_occurred"
    SYSTEM_ERROR = "system_error"
    
    # Workflow operations
    WORKFLOW_MODIFIED = "workflow_modified"
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    RUN_CREATED = "run_created"

    # Provider credentials and jobs
    PROVIDER_CREDENTIAL_CREATED = "provider_credential_created"
    PROVIDER_CREDENTIAL_VALIDATED = "provider_credential_validated"
    PROVIDER_CREDENTIAL_RENAMED = "provider_credential_renamed"
    PROVIDER_CREDENTIAL_REPLACED = "provider_credential_replaced"
    PROVIDER_CREDENTIAL_REVOKED = "provider_credential_revoked"
    PROVIDER_JOB_SUBMITTED = "provider_job_submitted"
    PROVIDER_JOB_COMPLETED = "provider_job_completed"
    PROVIDER_JOB_FAILED = "provider_job_failed"
    PROVIDER_JOB_CANCELLED = "provider_job_cancelled"
    BOLTZ_ACCESS_REQUESTED = "boltz_access_requested"
    BOLTZ_ACCESS_REVIEWED = "boltz_access_reviewed"
    
    # Security events
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    API_REQUEST = "api_request"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEvent(BaseModel):
    """Single audit log entry."""
    
    # Identifiers
    id: str = Field(default_factory=lambda: f"audit_{uuid4().hex}")
    event_type: AuditEventType
    severity: AuditSeverity = AuditSeverity.INFO
    
    # Timestamp
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # Actor information
    user_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None
    
    # Request context
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    
    # Resource information
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # Event details
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    
    # Result
    success: bool = True
    error_message: Optional[str] = None
    
    # Related entities
    experiment_id: Optional[str] = None
    run_id: Optional[str] = None
    
    # Metadata
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AuditLogger:
    """Manages audit logging and querying."""
    
    def __init__(self, data_root: Optional[Path] = None):
        default_root = Path(os.getenv("SABLE_DATA_ROOT", "data"))
        self.data_root = data_root or default_root
        self.audit_dir = self.data_root / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        
        # Current log file (rotates daily)
        self._current_log_file = self._get_log_file()
        
        # In-memory buffer for recent events
        self._recent_events: List[AuditEvent] = []
        self._max_recent_events = 1000
    
    def _get_log_file(self, date: Optional[datetime] = None) -> Path:
        """Get the log file path for a specific date."""
        if date is None:
            date = datetime.now()
        date_str = date.strftime("%Y%m%d")
        return self.audit_dir / f"audit_{date_str}.ndjson"
    
    def log(
        self,
        event_type: AuditEventType,
        message: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
        severity: AuditSeverity = AuditSeverity.INFO,
        success: bool = True,
        error_message: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        run_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None
    ) -> AuditEvent:
        """Log an audit event."""
        event = AuditEvent(
            event_type=event_type,
            message=message,
            user_id=user_id,
            username=username,
            session_id=session_id,
            severity=severity,
            success=success,
            error_message=error_message,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type=resource_type,
            resource_id=resource_id,
            experiment_id=experiment_id,
            run_id=run_id,
            details=details or {},
            tags=tags or []
        )
        
        # Write to file
        log_file = self._get_log_file()
        with log_file.open("a") as f:
            f.write(json.dumps(event.model_dump(mode="json"), default=str) + "\n")
        
        # Add to recent events buffer
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events.pop(0)
        
        return event
    
    def get_recent_events(self, limit: int = 100) -> List[AuditEvent]:
        """Get recent audit events from memory."""
        return self._recent_events[-limit:]
    
    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        event_type: Optional[AuditEventType] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        run_id: Optional[str] = None,
        severity: Optional[AuditSeverity] = None,
        success: Optional[bool] = None,
        limit: Optional[int] = None
    ) -> List[AuditEvent]:
        """Query audit events with filters."""
        events: List[AuditEvent] = []
        
        # Determine date range
        if start_date is None:
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if end_date is None:
            end_date = datetime.now()
        
        # Read log files for the date range
        current_date = start_date
        while current_date <= end_date:
            log_file = self._get_log_file(current_date)
            if log_file.exists():
                with log_file.open() as f:
                    for line in f:
                        try:
                            data = json.loads(line)
                            event = AuditEvent(**data)
                            
                            # Apply filters
                            if event_type and event.event_type != event_type:
                                continue
                            if user_id and event.user_id != user_id:
                                continue
                            if session_id and event.session_id != session_id:
                                continue
                            if experiment_id and event.experiment_id != experiment_id:
                                continue
                            if run_id and event.run_id != run_id:
                                continue
                            if severity and event.severity != severity:
                                continue
                            if success is not None and event.success != success:
                                continue
                            
                            events.append(event)
                        except Exception:
                            continue
            
            current_date = current_date.replace(day=current_date.day + 1)
        
        # Sort by timestamp (most recent first)
        events.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Apply limit
        if limit:
            events = events[:limit]
        
        return events
    
    def get_user_activity(
        self,
        user_id: str,
        start_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get all activity for a specific user."""
        return self.get_events(
            user_id=user_id,
            start_date=start_date,
            limit=limit
        )
    
    def get_security_events(
        self,
        start_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get security-related events."""
        security_types = [
            AuditEventType.AUTH_FAILED,
            AuditEventType.UNAUTHORIZED_ACCESS,
            AuditEventType.PERMISSION_DENIED,
            AuditEventType.RATE_LIMIT_EXCEEDED
        ]
        
        all_events = []
        for event_type in security_types:
            events = self.get_events(
                event_type=event_type,
                start_date=start_date
            )
            all_events.extend(events)
        
        all_events.sort(key=lambda x: x.timestamp, reverse=True)
        return all_events[:limit] if limit else all_events
    
    def get_failed_operations(
        self,
        start_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get all failed operations for debugging."""
        return self.get_events(
            success=False,
            start_date=start_date,
            limit=limit
        )


# Global audit logger instance
audit_logger = AuditLogger()
