import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listRuns, type RunInfo } from '../api'
import { RunStatusBadge } from '../components/RunStatusBadge'

const STATUS_OPTIONS = ['all', 'running', 'completed', 'failed', 'halted', 'queued']

export function DashboardPage() {
  const navigate = useNavigate()
  const { data: runs = [], isLoading } = useQuery<RunInfo[]>({
    queryKey: ['runs'],
    queryFn: () => listRuns(),
    refetchInterval: 8000,
  })
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [search, setSearch] = useState('')

  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      const matchesStatus = statusFilter === 'all' || run.status.toLowerCase().includes(statusFilter)
      const matchesSearch = !search || run.id.toLowerCase().includes(search.toLowerCase()) || (run.exit_reason || '').toLowerCase().includes(search.toLowerCase())
      return matchesStatus && matchesSearch
    })
  }, [runs, statusFilter, search])

  const runningCount = runs.filter((run) => run.status.toLowerCase().includes('running')).length
  const completedCount = runs.filter((run) => run.status.toLowerCase().includes('complete')).length
  const lastRun = runs[0]

  return (
    <div className="dashboard">
      <div className="dashboard__intro">
        <div>
          <h1>Optimization Campaigns</h1>
          <p>Monitor, inspect, and relaunch molecular optimization workflows with real-time visibility.</p>
        </div>
        <button className="primary" onClick={() => navigate('/runs/new')}>New Optimization</button>
      </div>

      <div className="dashboard__metrics">
        <MetricCard label="Active Runs" value={runningCount} trendLabel="Monitoring" trendColor="var(--status-running)" />
        <MetricCard label="Completed" value={completedCount} trendLabel="Historical" trendColor="var(--status-completed)" />
        <MetricCard label="Latest Run" value={lastRun ? new Date(lastRun.created_at).toLocaleString() : '—'} trendLabel={lastRun?.status || 'Idle'} trendColor="var(--accent-muted)" />
      </div>

      <div className="dashboard__filters">
        <div className="dashboard__search">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search runs by id or exit reason" aria-label="Search runs" />
        </div>
        <div className="dashboard__status-filter" role="group" aria-label="Filter runs by status">
          {STATUS_OPTIONS.map((option) => (
            <button
              key={option}
              className={option === statusFilter ? 'active' : ''}
              onClick={() => setStatusFilter(option)}
            >
              {option.charAt(0).toUpperCase() + option.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="dashboard__table">
        {isLoading ? (
          <div className="dashboard__empty">Loading runs...</div>
        ) : filteredRuns.length ? (
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Created</th>
                <th>Updated</th>
                <th>Exit</th>
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
                    <div className="dashboard__run-id">{run.id}</div>
                  </td>
                  <td><RunStatusBadge status={run.status} /></td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                  <td>{new Date(run.updated_at).toLocaleString()}</td>
                  <td>{run.exit_reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="dashboard__empty">No runs match the current filters.</div>
        )}
      </div>
    </div>
  )
}

function MetricCard({ label, value, trendLabel, trendColor }: { label: string; value: number | string; trendLabel: string; trendColor: string }) {
  return (
    <div className="metric-card">
      <div className="metric-card__label">{label}</div>
      <div className="metric-card__value">{value}</div>
      <div className="metric-card__trend" style={{ color: trendColor }}>{trendLabel}</div>
    </div>
  )
}
