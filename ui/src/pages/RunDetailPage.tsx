import { ReactNode, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  API_BASE,
  deleteRun,
  generateRunPlots,
  getCheckpoints,
  getRun,
  getRunLogs,
  getRunPlotUrl,
  getRunResults,
  getRunSummary,
  getAccessToken,
  listRunPlots,
  type RunEvent,
  type GenerateRunPlotsResponse,
  type RunInfo,
  type RunPlotArtifact,
  type RunResults,
} from '../api'
import { useEventStream } from '../hooks/useEventStream'
import { RunStatusBadge } from '../components/RunStatusBadge'
import { EventLog } from '../components/EventLog'
import { MoleculeViewer } from '../components/MoleculeViewer'
import { DistributionChart } from '../components/DistributionChart'
import { ResultsTable, type ResultsColumn } from '../components/ResultsTable'
import { extractMoleculeRecords, extractNumericSeries, flattenRecord } from '../utils/results'

const TABS = ['overview', 'logs', 'checkpoints', 'results', 'visualizations'] as const

type TabKey = (typeof TABS)[number]

// Helper function to download file with authentication
async function downloadFile(url: string, filename: string) {
  const token = getAccessToken()
  const headers: HeadersInit = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  
  const response = await fetch(url, { headers })
  if (!response.ok) {
    throw new Error('Download failed')
  }
  
  const blob = await response.blob()
  const downloadUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = downloadUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(downloadUrl)
}

function getBoltzMapping(results: any): Record<string, string> {
  const mapping: Record<string, string> = {}
  if (!results || !results.experimental_data || !Array.isArray(results.experimental_data)) {
    return mapping
  }
  
  for (const entry of results.experimental_data) {
    const boltz = entry.metadata?.boltz
    if (boltz && boltz.job_id) {
      // Prefer molecule_id, fallback to truncated SMILES
      let label = entry.molecule_id
      if (!label && entry.smiles) {
        label = entry.smiles.length > 15 ? entry.smiles.substring(0, 12) + '...' : entry.smiles
      }
      mapping[boltz.job_id] = label || 'Unknown Ligand'
    }
  }
  return mapping
}

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabKey>('overview')
  const [selectedPlotPath, setSelectedPlotPath] = useState<string | null>(null)
  const apiBase = useMemo(() => API_BASE.replace(/\/$/, ''), [])

  const { data: run, isLoading, isError } = useQuery<RunInfo>({
    queryKey: ['run', id],
    queryFn: () => getRun(id as string),
    enabled: Boolean(id),
    refetchInterval: 5000,
  })

  const checkpointsQuery = useQuery<string[]>({
    queryKey: ['run', id, 'checkpoints'],
    queryFn: () => getCheckpoints(id as string),
    enabled: Boolean(id),
    refetchInterval: 15000,
  })

  const summaryQuery = useQuery<string | null>({
    queryKey: ['run', id, 'summary'],
    queryFn: () => getRunSummary(id as string),
    enabled: Boolean(run?.summary_available) && Boolean(id),
    refetchInterval: 30000,
  })

  const resultsQuery = useQuery<RunResults | null>({
    queryKey: ['run', id, 'results'],
    queryFn: () => getRunResults(id as string),
    enabled: Boolean(run?.results_available) && Boolean(id),
    refetchInterval: 45000,
  })

  const logsQuery = useQuery<RunEvent[]>({
    queryKey: ['run', id, 'logs'],
    queryFn: () => getRunLogs(id as string),
    enabled: Boolean(id),
    refetchInterval: 60000,
  })

  const plotsQuery = useQuery<RunPlotArtifact[]>({
    queryKey: ['run', id, 'plots'],
    queryFn: () => listRunPlots(id as string),
    enabled: Boolean(id),
    refetchInterval: 60000,
  })

  const mutation = useMutation({
    mutationFn: () => deleteRun(id as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      navigate('/')
    },
  })

  const generatePlotsMutation = useMutation<GenerateRunPlotsResponse, Error, string>({
    mutationFn: (runId: string) => generateRunPlots(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['run', id, 'plots'] })
    },
  })

  const liveEvents = useEventStream(id)

  useEffect(() => {
    setActiveTab('overview')
    setSelectedPlotPath(null)
  }, [id])

  useEffect(() => {
    if (!plotsQuery.data || !plotsQuery.data.length) {
      setSelectedPlotPath(null)
      return
    }

    if (!selectedPlotPath || !plotsQuery.data.some((plot) => plot.path === selectedPlotPath)) {
      setSelectedPlotPath(plotsQuery.data[0].path)
    }
  }, [plotsQuery.data, selectedPlotPath])

  const mergedEvents = useMemo(() => {
    const combined = [...(logsQuery.data || []), ...liveEvents]
    const seen = new Set<string>()
    const filtered: RunEvent[] = []
    combined.forEach((evt, idx) => {
      const key = `${evt.ts || idx}-${evt.event || evt.action || idx}`
      if (seen.has(key)) return
      seen.add(key)
      filtered.push(evt)
    })
    return filtered.slice(-400)
  }, [logsQuery.data, liveEvents])

  const boltzMapping = useMemo(() => getBoltzMapping(resultsQuery.data), [resultsQuery.data])

  const moleculeRecords = useMemo(() => extractMoleculeRecords(resultsQuery.data ?? null), [resultsQuery.data])
  const flatRows = useMemo(
    () => moleculeRecords.map((record) => ({ candidate: record.id, smiles: record.smiles, ...flattenRecord(record.data) })),
    [moleculeRecords]
  )
  const numericSeries = useMemo(() => extractNumericSeries(flatRows), [flatRows])
  const numericEntries = useMemo(() => Object.entries(numericSeries).sort((a, b) => b[1].length - a[1].length), [numericSeries])
  const numericKeys = useMemo(() => numericEntries.map(([key]) => key).filter((k) => !k.startsWith('metadata')), [numericEntries])
  const columns = useMemo(() => buildColumns(numericKeys), [numericKeys])
  const chartSeries = useMemo(() => numericEntries.filter(([key]) => !key.startsWith('metadata')).slice(0, 6), [numericEntries])
  const selectedPlot = useMemo(
    () => plotsQuery.data?.find((plot) => plot.path === selectedPlotPath) ?? null,
    [plotsQuery.data, selectedPlotPath]
  )

  if (isLoading) {
    return <div className="run-detail__placeholder">Loading run...</div>
  }

  if (isError || !run) {
    return <div className="run-detail__placeholder">Run not found.</div>
  }

  const startingMolecules = run.starting_molecules ?? []

  return (
    <div className="run-detail">
      <div className="run-detail__header">
        <div>
          <button className="ghost" onClick={() => navigate(-1)}>&larr; Back</button>
          <h1>{run.id}</h1>
          <p>Created {new Date(run.created_at).toLocaleString()} • Updated {new Date(run.updated_at).toLocaleString()}</p>
        </div>
        <div className="run-detail__header-actions">
          <RunStatusBadge status={run.status} />
          <button className="ghost" onClick={() => queryClient.invalidateQueries({ queryKey: ['run', id] })}>Refresh</button>
          <button
            className="ghost"
            disabled={!run.results_available}
            onClick={() => {
              if (!id || !run.results_available) return
              downloadFile(`${apiBase}/runs/${id}/artifacts/results.json`, `${id}_results.json`)
            }}
          >
            Download results
          </button>
          <button
            className="ghost"
            disabled={!run.summary_available}
            onClick={() => {
              if (!id || !run.summary_available) return
              downloadFile(`${apiBase}/runs/${id}/artifacts/summary.txt`, `${id}_summary.txt`)
            }}
          >
            Download summary
          </button>
          <button
            className="danger"
            onClick={() => {
              if (!id) return
              if (!window.confirm('Delete this run and all artifacts?')) return
              mutation.mutate()
            }}
            disabled={mutation.isPending}
          >
            Delete
          </button>
        </div>
      </div>

      <div className="run-detail__tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={activeTab === tab}
            className={activeTab === tab ? 'active' : ''}
            onClick={() => setActiveTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      <section hidden={activeTab !== 'overview'}>
        <div className="run-detail__panel">
          <h2>Overview</h2>
          <div className="run-detail__grid">
            <InfoCard label="Status" value={<RunStatusBadge status={run.status} />} />
            <InfoCard label="Exit reason" value={run.exit_reason || '—'} />
            <InfoCard label="Results" value={run.results_available ? 'Ready' : 'Not yet'} />
            <InfoCard label="Summary" value={run.summary_available ? 'Ready' : 'Not yet'} />
          </div>
          {run.prompt && (
            <div className="run-detail__prompt">
              <h3>User prompt</h3>
              <pre>{run.prompt}</pre>
            </div>
          )}
          {run.note && (
            <div className="run-detail__note">
              <h3>Launch note</h3>
              <p>{run.note}</p>
            </div>
          )}
          {summaryQuery.data && (
            <div className="run-detail__summary">
              <h3>Agent Summary</h3>
              <pre>{summaryQuery.data}</pre>
            </div>
          )}
        </div>
      </section>

      <section hidden={activeTab !== 'logs'}>
        <div className="run-detail__panel">
          <h2>Live event stream</h2>
          <EventLog events={mergedEvents} />
        </div>
      </section>

      <section hidden={activeTab !== 'checkpoints'}>
        <div className="run-detail__panel">
          <h2>Checkpoints</h2>
          {checkpointsQuery.isLoading ? (
            <div>Discovering checkpoints…</div>
          ) : checkpointsQuery.data && checkpointsQuery.data.length ? (
            <ul className="run-detail__checkpoints">
              {checkpointsQuery.data.map((name: string) => {
                let displayName = name
                const match = name.match(/^(.+)_model_0\.cif$/)
                if (match) {
                  const jobId = match[1]
                  if (boltzMapping[jobId]) {
                    displayName = `${boltzMapping[jobId]} (${name})`
                  }
                }
                
                return (
                  <li key={name}>
                    <span>{displayName}</span>
                    <button
                      className="ghost"
                      disabled={!id}
                      onClick={async () => {
                        if (!id) return
                        try {
                          await downloadFile(
                            `${apiBase}/runs/${id}/checkpoints/${encodeURIComponent(name)}`,
                            name
                          )
                        } catch (error) {
                          console.error('Failed to download checkpoint', error)
                          window.alert('Failed to download checkpoint. Please try again.')
                        }
                      }}
                    >
                      Download
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <div>No checkpoints available.</div>
          )}
        </div>
      </section>

      <section hidden={activeTab !== 'results'}>
        <div className="run-detail__panel">
          <div className="run-detail__panel-header">
            <h2>Results table</h2>
            <div className="run-detail__panel-actions">
              <button
                className="ghost"
                disabled={!flatRows.length}
                onClick={() => handleResultsDownloadCsv(id ?? run.id, columns, flatRows)}
              >
                Download CSV
              </button>
            </div>
          </div>
          <ResultsTable
            rows={flatRows}
            columns={columns}
          />
        </div>
      </section>

      <section hidden={activeTab !== 'visualizations'}>
        <div className="run-detail__panel">
          <h2>Molecular visualizations</h2>
          <div className="run-detail__visualization-section">
            <h3>Starting molecules</h3>
            {startingMolecules.length ? (
              <div className="run-detail__molecule-grid">
                {startingMolecules.map((smiles, index) => (
                  <MoleculeViewer
                    key={`${smiles}-${index}`}
                    smiles={smiles}
                    caption={`Seed ${index + 1}`}
                  />
                ))}
              </div>
            ) : (
              <div className="distribution-chart__empty">No starting molecules recorded for this run.</div>
            )}
          </div>
          <div className="run-detail__visualization-section">
            <h3>Evaluated molecules</h3>
          {moleculeRecords.length ? (
            <div className="run-detail__molecule-grid">
              {moleculeRecords.slice(0, 8).map((record) => (
                <MoleculeViewer key={record.id} smiles={record.smiles} caption={record.id} />
              ))}
            </div>
          ) : (
              <div>No molecular structures detected in results.</div>
          )}
          </div>
          <div className="run-detail__charts">
            {chartSeries.length ? (
              chartSeries.map(([metric, values]) => (
                <DistributionChart key={metric} label={metric.split('.').pop() ?? metric} values={values} />
              ))
            ) : (
              <div className="distribution-chart__empty">No numeric metrics detected yet.</div>
            )}
          </div>

          <div className="run-detail__visualization-section">
            <h3>Workflow plots</h3>
            <div className="run-detail__plot-toolbar">
              <button
                className="primary"
                disabled={!id || generatePlotsMutation.isPending}
                onClick={() => {
                  if (!id) return
                  generatePlotsMutation.mutate(id)
                }}
              >
                {generatePlotsMutation.isPending ? 'Generating…' : 'Generate extended plots'}
              </button>
            </div>
            {generatePlotsMutation.isError ? (
              <div className="distribution-chart__empty">Failed to generate plots: {generatePlotsMutation.error.message}</div>
            ) : null}
            {plotsQuery.isLoading ? (
              <div>Discovering generated plots…</div>
            ) : plotsQuery.data && plotsQuery.data.length ? (
              <>
                <div className="run-detail__plot-toolbar">
                  <select
                    value={selectedPlotPath ?? ''}
                    onChange={(event) => setSelectedPlotPath(event.target.value || null)}
                  >
                    {plotsQuery.data.map((plot) => (
                      <option key={plot.path} value={plot.path}>
                        {plot.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className="ghost"
                    disabled={!id || !selectedPlot}
                    onClick={() => {
                      if (!id || !selectedPlot) return
                      window.open(getRunPlotUrl(id, selectedPlot.path), '_blank', 'noopener,noreferrer')
                    }}
                  >
                    Open in new tab
                  </button>
                  <button
                    className="ghost"
                    disabled={!id || !selectedPlot}
                    onClick={async () => {
                      if (!id || !selectedPlot) return
                      await downloadFile(getRunPlotUrl(id, selectedPlot.path), selectedPlot.name)
                    }}
                  >
                    Download plot
                  </button>
                </div>
                {selectedPlot && id ? (
                  <iframe
                    key={selectedPlot.path}
                    title={selectedPlot.name}
                    className="run-detail__plot-frame"
                    src={getRunPlotUrl(id, selectedPlot.path)}
                  />
                ) : null}
              </>
            ) : (
              <div className="distribution-chart__empty">
                No workflow plot HTML files found yet. Save utility output under run <code>results/plots</code> or <code>artifacts/plots</code>.
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}

function InfoCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="info-card">
      <div className="info-card__label">{label}</div>
      <div className="info-card__value">{value}</div>
    </div>
  )
}

function buildColumns(metricKeys: string[]): ResultsColumn[] {
  const base: ResultsColumn[] = [
    { key: 'candidate', label: 'Candidate' },
    {
      key: 'smiles',
      label: 'SMILES',
      render: (row) => {
        const value = row['smiles']
        return typeof value === 'string' ? <code>{value}</code> : '—'
      },
    },
  ]
  const metricColumns = metricKeys.map((key) => ({ key, label: key.split('.').pop() ?? key }))
  return [...base, ...metricColumns]
}

function escapeCsvValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : ''
  }
  const serialized = typeof value === 'string' ? value : JSON.stringify(value)
  const raw = serialized ?? String(value)
  const escaped = raw.replace(/"/g, '""')
  if (/[",\n\r]/.test(raw)) {
    return `"${escaped}"`
  }
  return escaped
}

function handleResultsDownloadCsv(
  runId: string,
  columns: ResultsColumn[],
  rows: Record<string, unknown>[]
) {
  if (!rows.length) return

  const header = columns.map((col) => escapeCsvValue(col.label ?? col.key)).join(',')
  const data = rows.map((row) =>
    columns.map((col) => escapeCsvValue(row[col.key])).join(',')
  )
  const csvContent = [header, ...data].join('\n')
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${runId}_results.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}
