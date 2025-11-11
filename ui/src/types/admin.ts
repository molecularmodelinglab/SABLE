export interface DailyCount {
  date: string;
  count: number;
}

export interface StatusBreakdown {
  total: number;
  by_status: Record<string, number>;
  last_30_days: DailyCount[];
}

export interface UserMetrics {
  total: number;
  active: number;
  inactive: number;
  verified: number;
  admins: number;
  recently_active: number;
}

export interface SessionMetrics {
  active: number;
  expiring_within_24h: number;
  average_duration_hours: number | null;
}

export interface AuditEventCount {
  event_type: string;
  count: number;
}

export interface AuditMetrics {
  total_last_7_days: number;
  critical_last_7_days: number;
  last_7_days_by_severity: Record<string, number>;
  top_events_last_30_days: AuditEventCount[];
}

export interface AdminAnalyticsSummary {
  generated_at: string;
  users: UserMetrics;
  runs: StatusBreakdown;
  experiments: StatusBreakdown;
  sessions: SessionMetrics;
  audit: AuditMetrics;
}
