export interface AnalyticsSummary {
  user_id: string;
  username: string;
  total_experiments: number;
  successful: number;
  failed: number;
  running: number;
  total_molecules_evaluated: number;
  total_iterations: number;
  average_duration_seconds?: number;
  success_rate: number;
}

export interface HealthCheck {
  status: string;
  timestamp: string;
  active_sessions: number;
  active_runs: number;
}
