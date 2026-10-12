import type { RunInfo } from '../api'

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

const QUEUE_EXPLANATIONS: Record<NonNullable<RunInfo['queue_reason']>, string> = {
  waiting_for_admission: 'Waiting for capacity; users with fewer admitted runs go first.',
  dispatch_retry: 'Delivery failed; waiting to retry.',
  dispatching: 'Capacity reserved; sending to a worker.',
  waiting_for_worker: 'Capacity reserved; waiting for a worker.',
}

export function RunStatusBadge({ status, queueReason, retryAt }: {
  status: RunInfo['status']
  queueReason?: RunInfo['queue_reason']
  retryAt?: RunInfo['queue_retry_at']
}) {
  const normalized = status?.toLowerCase() || 'unknown'
  const variant = STATUS_VARIANTS[normalized] || 'unknown'
  const explanation = normalized === 'queued' && queueReason ? QUEUE_EXPLANATIONS[queueReason] : undefined
  const retryDate = queueReason === 'dispatch_retry' && retryAt ? new Date(retryAt) : null
  const badge = (
    <span className={`run-status-badge run-status-badge--${variant}`}>
      {status}
    </span>
  )

  if (!explanation) return badge

  return (
    <span className="run-status">
      {badge}
      <span className="run-queue-reason">
        {explanation}
        {retryDate && !Number.isNaN(retryDate.getTime()) && (
          <> Eligible after <time dateTime={retryDate.toISOString()}>{retryDate.toLocaleString()}</time>.</>
        )}
      </span>
    </span>
  )
}
