import { RunInfo } from '../api'

const STATUS_COLORS: Record<string, string> = {
  pending: 'var(--status-queued)',
  running: 'var(--status-running)',
  completed: 'var(--status-completed)',
  succeeded: 'var(--status-completed)',
  success: 'var(--status-completed)',
  failed: 'var(--status-failed)',
  halted: 'var(--status-halted)',
  stopped: 'var(--status-halted)',
  cancelled: 'var(--status-halted)',
  queued: 'var(--status-queued)',
}

export function RunStatusBadge({ status }: { status: RunInfo['status'] }) {
  const normalized = status?.toLowerCase() || 'unknown'
  const color = STATUS_COLORS[normalized] || 'var(--status-unknown)'
  return (
    <span className="run-status-badge" style={{ backgroundColor: color }}>
      {status}
    </span>
  )
}
