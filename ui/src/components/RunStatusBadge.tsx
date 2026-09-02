import { RunInfo } from '../api'

const STATUS_VARIANTS: Record<string, string> = {
  pending: 'queued',
  running: 'running',
  completed: 'completed',
  succeeded: 'completed',
  success: 'completed',
  failed: 'failed',
  halted: 'halted',
  stopped: 'halted',
  cancelled: 'halted',
  queued: 'queued',
}

export function RunStatusBadge({ status }: { status: RunInfo['status'] }) {
  const normalized = status?.toLowerCase() || 'unknown'
  const variant = STATUS_VARIANTS[normalized] || 'unknown'
  return (
    <span className={`run-status-badge run-status-badge--${variant}`}>
      {status}
    </span>
  )
}
