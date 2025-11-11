import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getAdminAnalyticsSummary } from '../api'
import type { AdminAnalyticsSummary, DailyCount } from '../types/admin'

export function AdminDashboardPage() {
  const {
    data,
    isLoading,
    isError,
    isFetching,
    refetch,
  } = useQuery<AdminAnalyticsSummary>({
    queryKey: ['admin', 'analytics', 'summary'],
    queryFn: getAdminAnalyticsSummary,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 1,
  })

  if (isLoading) {
    return (
      <div className="admin-dashboard__empty">
        <div className="admin-dashboard__emoji" aria-hidden>🛠️</div>
        <p>Loading administrator analytics…</p>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="admin-dashboard__empty">
        <div className="admin-dashboard__emoji" aria-hidden>⚠️</div>
        <p>We couldn&apos;t load the administrative summary.</p>
        <button className="primary" onClick={() => refetch()} disabled={isFetching}>
          Try again
        </button>
      </div>
    )
  }

  const generatedAt = new Date(data.generated_at).toLocaleString()
  const runStatusEntries = Object.entries(data.runs.by_status)
  const experimentStatusEntries = Object.entries(data.experiments.by_status)
  const auditSeverityEntries = Object.entries(data.audit.last_7_days_by_severity)

  return (
    <div className="admin-dashboard">
      <div className="admin-dashboard__header">
        <div>
          <h1>Operations Overview</h1>
          <p>High-level insights across users, pipelines, sessions, and security events.</p>
        </div>
        <div className="admin-dashboard__header-actions">
          <span className="admin-dashboard__timestamp" aria-live="polite">Updated {generatedAt}</span>
          <button className="ghost" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? 'Refreshing…' : 'Refresh now'}
          </button>
        </div>
      </div>

      <section className="admin-section">
        <h2>User Accounts</h2>
        <div className="admin-grid">
          <StatCard label="Total users" value={data.users.total} accent="var(--accent-muted)" />
          <StatCard label="Active" value={data.users.active} accent="var(--status-running)" />
          <StatCard label="Inactive" value={data.users.inactive} accent="var(--status-queued)" />
          <StatCard label="Verified" value={data.users.verified} accent="var(--status-completed)" />
          <StatCard label="Admins" value={data.users.admins} accent="var(--status-failed)" />
          <StatCard label="Active (30 days)" value={data.users.recently_active} accent="var(--status-running)" />
        </div>
      </section>

      <section className="admin-section">
        <div className="admin-section__heading">
          <h2>Run Pipeline Health</h2>
          <span>{data.runs.total} total runs tracked</span>
        </div>
        <div className="admin-grid admin-grid--two">
          <div className="admin-panel">
            <h3>Status mix</h3>
            <StatusList entries={runStatusEntries} emptyLabel="No runs recorded" />
          </div>
          <div className="admin-panel">
            <h3>Last 30 days</h3>
            <TrendChart data={data.runs.last_30_days} fallback="No recent runs" label="runs" />
          </div>
        </div>
      </section>

      <section className="admin-section">
        <div className="admin-section__heading">
          <h2>Experiment Outcomes</h2>
          <span>{data.experiments.total} experiments observed</span>
        </div>
        <div className="admin-grid admin-grid--two">
          <div className="admin-panel">
            <h3>Status mix</h3>
            <StatusList entries={experimentStatusEntries} emptyLabel="No experiments yet" />
          </div>
          <div className="admin-panel">
            <h3>Last 30 days</h3>
            <TrendChart data={data.experiments.last_30_days} fallback="No recent experiments" label="experiments" />
          </div>
        </div>
      </section>

      <section className="admin-section">
        <div className="admin-grid admin-grid--two">
          <div className="admin-panel">
            <h2>Session Activity</h2>
            <div className="admin-grid admin-grid--auto">
              <StatCard label="Active sessions" value={data.sessions.active} accent="var(--status-running)" />
              <StatCard label="Expiring ≤24h" value={data.sessions.expiring_within_24h} accent="var(--status-halted)" />
              <StatCard
                label="Avg duration"
                value={formatHours(data.sessions.average_duration_hours)}
                accent="var(--accent-muted)"
              />
            </div>
          </div>
          <div className="admin-panel">
            <h2>Audit Signals (7 days)</h2>
            <div className="admin-audit__totals">
              <div className="admin-audit__total">
                <span className="admin-audit__total-label">Events</span>
                <span className="admin-audit__total-value">{data.audit.total_last_7_days}</span>
              </div>
              <div className="admin-audit__total">
                <span className="admin-audit__total-label">Warnings+</span>
                <span className="admin-audit__total-value">{data.audit.critical_last_7_days}</span>
              </div>
            </div>
            <StatusList
              entries={auditSeverityEntries.map(([severity, count]) => [severity, count])}
              emptyLabel="No audit events"
            />
            <div className="admin-audit__top-events">
              <h3>Top event types (30 days)</h3>
              {data.audit.top_events_last_30_days.length === 0 ? (
                <p className="admin-empty">No notable audit events.</p>
              ) : (
                <ul>
                  {data.audit.top_events_last_30_days.map((event) => (
                    <li key={event.event_type}>
                      <span>{event.event_type}</span>
                      <span>{event.count}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}

function StatCard({ label, value, accent }: { label: string; value: number | string; accent: string }) {
  return (
    <div className="admin-stat" style={{ borderTopColor: accent }}>
      <span className="admin-stat__label">{label}</span>
      <span className="admin-stat__value">{value}</span>
    </div>
  )
}

function StatusList({ entries, emptyLabel }: { entries: [string, number][]; emptyLabel: string }) {
  if (!entries.length) {
    return <p className="admin-empty">{emptyLabel}</p>
  }

  const total = entries.reduce((acc, [, count]) => acc + count, 0)

  return (
    <ul className="admin-status-list">
      {entries.map(([status, count]) => {
        const share = total > 0 ? Math.round((count / total) * 100) : 0
        return (
          <li key={status}>
            <div>
              <span className="admin-status-list__label">{status}</span>
              <span className="admin-status-list__muted">{share}%</span>
            </div>
            <span className="admin-status-list__count">{count}</span>
          </li>
        )
      })}
    </ul>
  )
}

function TrendChart({ data, fallback, label }: { data: DailyCount[]; fallback: string; label: string }) {
  const normalized = useMemo(() => {
    if (!data.length) return [] as { raw: string; date: string; count: number; ratio: number }[]
    const max = Math.max(...data.map((item) => item.count)) || 1
    return data.map((item) => ({
      raw: item.date,
      date: new Date(item.date).toLocaleDateString(),
      count: item.count,
      ratio: item.count / max,
    }))
  }, [data])

  if (!normalized.length) {
    return <p className="admin-empty">{fallback}</p>
  }

  return (
    <div className="admin-trend">
      {normalized.map(({ raw, date, count, ratio }) => (
        <div key={raw} className="admin-trend__bar" title={`${count} ${label} on ${date}`}>
          <div className="admin-trend__fill" style={{ height: `${Math.max(ratio * 100, 6)}%` }} />
          <span className="admin-trend__label">{date.split('/').slice(0, 2).join('/')}</span>
        </div>
      ))}
    </div>
  )
}

function formatHours(value: number | null): string {
  if (value == null) {
    return '—'
  }
  return `${value.toFixed(1)} h`
}
