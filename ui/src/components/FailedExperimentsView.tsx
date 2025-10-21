import React from 'react'
import { useFailedExperiments } from '../hooks/useExperiments'
import { Experiment } from '../types/experiment'

export function FailedExperimentsView() {
  const { experiments, loading, error, reload } = useFailedExperiments()

  if (loading) {
    return <div style={{ padding: '2rem' }}>Loading failed experiments...</div>
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', color: '#c00' }}>
        Error: {error}
      </div>
    )
  }

  return (
    <div style={{ padding: '2rem' }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '1.5rem'
      }}>
        <h2 style={{ margin: 0 }}>Failed Experiments</h2>
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

      {experiments.length === 0 ? (
        <div style={{
          padding: '2rem',
          textAlign: 'center',
          backgroundColor: '#f8f9fa',
          borderRadius: '8px'
        }}>
          <p style={{ margin: 0, color: '#666' }}>No failed experiments found 🎉</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {experiments.map((exp) => (
            <ExperimentCard key={exp.id} experiment={exp} />
          ))}
        </div>
      )}
    </div>
  )
}

function ExperimentCard({ experiment }: { experiment: Experiment }) {
  const [expanded, setExpanded] = React.useState(false)

  const formatDate = (date: string) => {
    return new Date(date).toLocaleString()
  }

  return (
    <div style={{
      border: '1px solid #dc3545',
      borderRadius: '8px',
      backgroundColor: '#fff',
      overflow: 'hidden'
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '1rem',
          cursor: 'pointer',
          backgroundColor: '#fff5f5',
          borderBottom: expanded ? '1px solid #dc3545' : 'none',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}
      >
        <div>
          <div style={{ fontWeight: 'bold', marginBottom: '0.25rem' }}>
            {experiment.id}
          </div>
          <div style={{ fontSize: '0.875rem', color: '#666' }}>
            Run: {experiment.run_id} | User: {experiment.username}
          </div>
          <div style={{ fontSize: '0.875rem', color: '#666', marginTop: '0.25rem' }}>
            Failed at: {formatDate(experiment.completed_at || experiment.created_at)}
          </div>
        </div>
        <div style={{
          backgroundColor: '#dc3545',
          color: 'white',
          padding: '0.25rem 0.75rem',
          borderRadius: '12px',
          fontSize: '0.875rem',
          fontWeight: 500
        }}>
          FAILED
        </div>
      </div>

      {expanded && (
        <div style={{ padding: '1rem' }}>
          <div style={{ marginBottom: '1rem' }}>
            <strong>Prompt:</strong>
            <div style={{
              marginTop: '0.5rem',
              padding: '0.75rem',
              backgroundColor: '#f8f9fa',
              borderRadius: '4px',
              fontSize: '0.875rem'
            }}>
              {experiment.prompt}
            </div>
          </div>

          {experiment.error && (
            <div style={{ marginBottom: '1rem' }}>
              <strong>Error:</strong>
              <div style={{
                marginTop: '0.5rem',
                padding: '0.75rem',
                backgroundColor: '#fee',
                border: '1px solid #fcc',
                borderRadius: '4px',
                fontSize: '0.875rem'
              }}>
                <div style={{ fontWeight: 'bold', marginBottom: '0.5rem' }}>
                  {experiment.error.error_type}: {experiment.error.message}
                </div>
                {experiment.error.node && (
                  <div style={{ marginBottom: '0.5rem' }}>
                    Node: {experiment.error.node}
                  </div>
                )}
                {experiment.error.stack_trace && (
                  <details>
                    <summary style={{ cursor: 'pointer', marginTop: '0.5rem' }}>
                      Stack Trace
                    </summary>
                    <pre style={{
                      marginTop: '0.5rem',
                      padding: '0.5rem',
                      backgroundColor: '#fff',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      fontSize: '0.75rem',
                      overflow: 'auto',
                      maxHeight: '300px'
                    }}>
                      {experiment.error.stack_trace}
                    </pre>
                  </details>
                )}
              </div>
            </div>
          )}

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: '1rem',
            marginTop: '1rem'
          }}>
            <MetricItem label="Iterations" value={experiment.metrics.iterations_completed} />
            <MetricItem label="Molecules" value={experiment.metrics.molecules_evaluated} />
            <MetricItem label="LLM Calls" value={experiment.metrics.llm_calls} />
            <MetricItem
              label="Duration"
              value={
                experiment.metrics.duration_seconds
                  ? `${(experiment.metrics.duration_seconds / 60).toFixed(1)} min`
                  : 'N/A'
              }
            />
          </div>
        </div>
      )}
    </div>
  )
}

function MetricItem({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: '0.75rem', color: '#666', marginBottom: '0.25rem' }}>
        {label}
      </div>
      <div style={{ fontWeight: 'bold' }}>{value}</div>
    </div>
  )
}
