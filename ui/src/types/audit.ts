export enum AuditEventType {
  // Authentication
  USER_LOGIN = "user_login",
  USER_LOGOUT = "user_logout",
  SESSION_CREATED = "session_created",
  SESSION_EXPIRED = "session_expired",
  AUTH_FAILED = "auth_failed",
  
  // Experiment operations
  EXPERIMENT_CREATED = "experiment_created",
  EXPERIMENT_STARTED = "experiment_started",
  EXPERIMENT_COMPLETED = "experiment_completed",
  EXPERIMENT_FAILED = "experiment_failed",
  EXPERIMENT_CANCELLED = "experiment_cancelled",
  
  // Data access
  DATA_READ = "data_read",
  DATA_WRITE = "data_write",
  DATA_DELETE = "data_delete",
  DATA_EXPORT = "data_export",
  
  // System events
  SYSTEM_START = "system_start",
  SYSTEM_SHUTDOWN = "system_shutdown",
  CONFIG_CHANGED = "config_changed",
  ERROR_OCCURRED = "error_occurred",
  
  // Workflow operations
  WORKFLOW_MODIFIED = "workflow_modified",
  CHECKPOINT_CREATED = "checkpoint_created",
  CHECKPOINT_RESTORED = "checkpoint_restored",
  
  // Security events
  UNAUTHORIZED_ACCESS = "unauthorized_access",
  PERMISSION_DENIED = "permission_denied",
  RATE_LIMIT_EXCEEDED = "rate_limit_exceeded",
}

export enum AuditSeverity {
  DEBUG = "debug",
  INFO = "info",
  WARNING = "warning",
  ERROR = "error",
  CRITICAL = "critical",
}

export interface AuditEvent {
  id: string;
  event_type: AuditEventType;
  severity: AuditSeverity;
  timestamp: string;
  user_id?: string;
  username?: string;
  session_id?: string;
  ip_address?: string;
  user_agent?: string;
  request_id?: string;
  resource_type?: string;
  resource_id?: string;
  message: string;
  details: Record<string, any>;
  success: boolean;
  error_message?: string;
  experiment_id?: string;
  run_id?: string;
  tags: string[];
  metadata: Record<string, any>;
}

export interface AuditEventsResponse {
  events: AuditEvent[];
}
