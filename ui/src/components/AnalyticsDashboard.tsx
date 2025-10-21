import React from 'react'
import { useAnalytics } from '../hooks/useAnalytics'

export function AnalyticsDashboard() {
  const { analytics, loading, error, reload } = useAnalytics()

  if (loading) {
    return <div style={{ padding: '2rem' }}>Loading analytics...</div>
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', color: '#c00' }}>
        Error loading analytics: {error}
      </div>
    )
  }

  if (!analytics) {
    return <div style={{ padding: '2rem' }}>No data available</div>
  }

  const successRate = (analytics.success_rate * 100).toFixed(1)
  const avgDuration = analytics.average_duration_seconds
    ? `${(analytics.average_duration_seconds / 60).toFixed(1)} min`
    : 'N/A'

  return (
    <div style={{ padding: '2rem' }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '2rem'
      }}>
        <div>
          <h2 style={{ margin: 0 }}>Analytics Dashboard</h2>
          <p style={{ margin: '0.5rem 0 0', color: '#666' }}>
            User: {analytics.username} ({analytics.user_id})
          </p>
        </div>
        <button
          onClick={reload}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: '#007bff',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          Refresh
        </button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '1rem',
        marginBottom: '2rem'
      }}>
        <StatCard title="Total Experiments" value={analytics.total_experiments} />
        <StatCard title="Successful" value={analytics.successful} color="#28a745" />
        <StatCard title="Failed" value={analytics.failed} color="#dc3545" />
        <StatCard title="Running" value={analytics.running} color="#ffc107" />
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '1rem'
      }}>
        <StatCard title="Molecules Evaluated" value={analytics.total_molecules_evaluated} />
        <StatCard title="Total Iterations" value={analytics.total_iterations} />
        <StatCard title="Success Rate" value={`${successRate}%`} />
        <StatCard title="Avg Duration" value={avgDuration} />
      </div>
    </div>
  )
}

function StatCard({
  title,
  value,
  color = '#007bff'
}: {
  title: string
  value: string | number
  color?: string
}) {
  return (
    <div style={{
      padding: '1.5rem',
      backgroundColor: '#fff',
      border: '1px solid #ddd',
      borderRadius: '8px',
      boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
    }}>
      <div style={{
        fontSize: '0.875rem',
        color: '#666',
        marginBottom: '0.5rem',
        textTransform: 'uppercase',
        letterSpacing: '0.5px'
      }}>
        {title}
      </div>
      <div style={{
        fontSize: '2rem',
        fontWeight: 'bold',
        color: color
      }}>
        {value}
      </div>
    </div>
  )
}
