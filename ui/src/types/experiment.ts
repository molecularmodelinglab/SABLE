export enum ExperimentStatus {
  PENDING = "pending",
  QUEUED = "queued",
  RUNNING = "running",
  SUCCESS = "success",
  FAILED = "failed",
  CANCELLED = "cancelled",
  TIMEOUT = "timeout",
}

export interface ExperimentError {
  message: string;
  error_type: string;
  stack_trace?: string;
  code?: string;
  node?: string;
  timestamp: string;
  recoverable: boolean;
  metadata?: Record<string, any>;
}

export interface ExperimentMetrics {
  duration_seconds?: number;
  iterations_completed: number;
  molecules_evaluated: number;
  molecules_generated: number;
  llm_calls: number;
  llm_tokens_used: number;
  characterization_calls: number;
  bo_iterations: number;
  peak_memory_mb?: number;
  cpu_time_seconds?: number;
}

export interface ExperimentLog {
  timestamp: string;
  level: string;
  message: string;
  node?: string;
  iteration?: number;
  data?: Record<string, any>;
}

export interface ExperimentCheckpoint {
  checkpoint_id: string;
  timestamp: string;
  iteration: number;
  state_snapshot: Record<string, any>;
  file_path?: string;
}

export interface Experiment {
  id: string;
  run_id: string;
  session_id: string;
  user_id: string;
  username: string;
  prompt: string;
  workflow_name: string;
  status: ExperimentStatus;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  parameters: Record<string, any>;
  parsed_arguments: Record<string, any>;
  targets: any[];
  result?: Record<string, any>;
  best_molecules: [string, number][];
  summary?: string;
  error?: ExperimentError;
  warnings: string[];
  metrics: ExperimentMetrics;
  logs: ExperimentLog[];
  checkpoints: ExperimentCheckpoint[];
  environment: Record<string, string>;
  git_commit?: string;
  tags: string[];
  notes?: string;
  parent_experiment_id?: string;
  metadata: Record<string, any>;
}

export interface ExperimentListResponse {
  experiments: Experiment[];
}
