import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { Check, LoaderCircle, TriangleAlert, X } from 'lucide-react'
import { getAdminAnalyticsSummary, listAdminBoltzUsers, listAdminRuns, reviewBoltzAccess, type RunInfo } from '../api'
import { RunStatusBadge } from '../components/RunStatusBadge'
import type { AdminAnalyticsSummary, DailyCount } from '../types/admin'

export function AdminDashboardPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [runSearch, setRunSearch] = useState('')
  const [reviewingUserId, setReviewingUserId] = useState<string | null>(null)

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

  const [limit, setLimit] = useState(10)

  const {
    data: allRuns = [],
    isLoading: runsLoading,
    isError: runsError,
    refetch: refetchRuns,
    isFetching: runsFetching,
  } = useQuery<RunInfo[]>({
    queryKey: ['admin', 'runs', limit],
    queryFn: () => listAdminRuns(limit),
    refetchInterval: 15000,
    placeholderData: keepPreviousData,
  })

  const filteredRuns = useMemo(() => {
    const term = runSearch.trim().toLowerCase()
    if (!term) return allRuns
    return allRuns.filter((run) => {
      const idMatch = run.id.toLowerCase().includes(term)
      const userMatch = (run.username || '').toLowerCase().includes(term)
      const reasonMatch = (run.exit_reason || '').toLowerCase().includes(term)
      return idMatch || userMatch || reasonMatch
    })
  }, [allRuns, runSearch])

  const { data: boltzUsers = [], isLoading: accessLoading } = useQuery({
    queryKey: ['admin', 'boltz-access'],
    queryFn: listAdminBoltzUsers,
    refetchInterval: 30_000,
  })

  const pendingAccess = boltzUsers.filter((user) => user.access_status === 'pending')

  const handleReview = async (userId: string, status: 'approved' | 'denied') => {
    setReviewingUserId(userId)
    try {
      await reviewBoltzAccess(userId, status)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'boltz-access'] })
    } finally {
      setReviewingUserId(null)
    }
  }

  if (isLoading) {
    return (
      <div className="admin-dashboard__empty">
        <LoaderCircle className="spin" size={36} aria-hidden="true" />
        <p>Loading administrator analytics…</p>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="admin-dashboard__empty">
        <TriangleAlert size={36} aria-hidden="true" />
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
          <h2>Hosted Boltz Access</h2>
          <span>{pendingAccess.length} pending</span>
        </div>
        <div className="admin-access-table">
          {accessLoading ? (
            <p className="admin-empty">Loading access requests...</p>
          ) : pendingAccess.length === 0 ? (
            <p className="admin-empty">No access requests are awaiting review.</p>
          ) : (
            <table>
              <thead><tr><th>User</th><th>Email</th><th>Requested</th><th>Decision</th></tr></thead>
              <tbody>
                {pendingAccess.map((user) => (
                  <tr key={user.user_id}>
                    <td>{user.username}</td>
                    <td>{user.email}</td>
                    <td>{user.requested_at ? new Date(user.requested_at).toLocaleString() : 'Unknown'}</td>
                    <td className="admin-access-table__actions">
                      <button className="icon-button icon-button--approve" title="Approve hosted access" onClick={() => handleReview(user.user_id, 'approved')} disabled={reviewingUserId !== null}><Check size={17} /></button>
                      <button className="icon-button icon-button--danger" title="Deny hosted access" onClick={() => handleReview(user.user_id, 'denied')} disabled={reviewingUserId !== null}><X size={17} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
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

      <section className="admin-section admin-runs">
        <div className="admin-runs__header">
          <div>
            <div className="admin-runs__title">All user runs</div>
            <p className="admin-empty">Browse and inspect optimization runs across all accounts.</p>
          </div>
          <div className="admin-runs__controls">
            <div className="admin-runs__search">
              <input
                type="search"
                placeholder="Search by run id, user, or exit reason"
                value={runSearch}
                onChange={(e) => setRunSearch(e.target.value)}
                aria-label="Search runs"
              />
            </div>
            <button className="ghost" onClick={() => refetchRuns()} disabled={runsLoading}>
              {runsLoading ? 'Refreshing…' : 'Refresh runs'}
            </button>
          </div>
        </div>

        <div className="admin-runs__table">
          {runsLoading ? (
            <div className="admin-runs__empty">Loading runs…</div>
          ) : runsError ? (
            <div className="admin-runs__empty">Unable to load runs. Try refreshing.</div>
          ) : filteredRuns.length === 0 ? (
            <div className="admin-runs__empty">No runs match the current filters.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>User</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Updated</th>
                  <th>Exit reason</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => navigate(`/runs/${run.id}`)}
                    title={run.note ? `Note: ${run.note}` : undefined}
                  >
                    <td>
                      <code className="dashboard__run-id">{run.id}</code>
                    </td>
                    <td>{run.username || '—'}</td>
                    <td>
                      <RunStatusBadge status={run.status} />
                    </td>
                    <td>{new Date(run.created_at).toLocaleString()}</td>
                    <td>{new Date(run.updated_at).toLocaleString()}</td>
                    <td>{run.exit_reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <button
            className="secondary"
            onClick={() => setLimit((l) => l + 10)}
            disabled={runsFetching}
          >
            {runsFetching ? 'Loading more…' : 'Load more runs'}
          </button>
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
