import { useEffect, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { MoleculeViewer } from '../MoleculeViewer'
import { ResultsTable } from '../ResultsTable'
import { DistributionChart } from '../DistributionChart'
import { Block, csv, object, present, readable, Result } from './presentation'

export function saveFile(name: string, content: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function DataTable({ block }: { block: Extract<Block, { type: 'table' }> }) {
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState({ key: '', descending: false })
  const rows = [...block.rows].sort((a, b) => {
    if (!sort.key) return 0
    const left = a[sort.key], right = b[sort.key]
    if (left == null) return right == null ? 0 : 1
    if (right == null) return -1
    const diff = typeof left === 'number' && typeof right === 'number' ? left - right : String(left).localeCompare(String(right))
    return sort.descending ? -diff : diff
  })
  const current = Math.min(page, Math.max(0, Math.ceil(rows.length / 20) - 1))
  return <section className="campaign-data-table"><div className="campaign-result-toolbar"><label>Sort by <select value={sort.key} onChange={e => { setSort({ ...sort, key: e.target.value }); setPage(0) }}><option value="">Recorded order</option>{block.columns.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}</select></label><button disabled={!sort.key} onClick={() => setSort({ ...sort, descending: !sort.descending })}>{sort.descending ? 'Descending' : 'Ascending'}</button><button onClick={() => saveFile('observations.csv', csv(block), 'text/csv;charset=utf-8')}>Download CSV</button></div>
    <ResultsTable rows={rows.slice(current * 20, current * 20 + 20)} columns={block.columns.map(column => ({ ...column, render: (row: Record<string, unknown>) => { const value = row[column.key]; return value == null ? '—' : typeof value === 'number' ? Number.isInteger(value) ? String(value) : value.toPrecision(6) : String(value) } }))} />
    {rows.length > 20 && <div className="campaign-pagination"><button disabled={!current} onClick={() => setPage(current - 1)}>Previous rows</button><span>{current * 20 + 1}–{Math.min(rows.length, current * 20 + 20)} of {rows.length}</span><button disabled={(current + 1) * 20 >= rows.length} onClick={() => setPage(current + 1)}>Next rows</button></div>}
  </section>
}

function Molecules({ block }: { block: Extract<Block, { type: 'molecules' }> }) {
  const [page, setPage] = useState(0)
  return <section><div className="campaign-molecule-grid">{block.items.slice(page * 8, page * 8 + 8).map((item, index) => <MoleculeViewer key={`${item.id}-${index}`} smiles={item.smiles} caption={item.label || item.id} />)}</div>
    {block.items.length > 8 && <div className="campaign-pagination"><button disabled={!page} onClick={() => setPage(page - 1)}>Previous molecules</button><span>{page * 8 + 1}–{Math.min(block.items.length, page * 8 + 8)} of {block.items.length}</span><button disabled={(page + 1) * 8 >= block.items.length} onClick={() => setPage(page + 1)}>Next molecules</button></div>}
  </section>
}

function Chart({ block }: { block: Extract<Block, { type: 'chart' }> }) {
  if (block.style === 'distribution') return <DistributionChart label={block.x_label} values={block.points.map(point => point.x)} />
  return <figure className="campaign-chart"><figcaption>{block.title} · {block.x_label} / {block.y_label}</figcaption><ResponsiveContainer width="100%" height={280}>{block.style === 'line' ? <LineChart data={block.points}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="x" name={block.x_label} type="number" domain={['dataMin', 'dataMax']} /><YAxis name={block.y_label} domain={['auto', 'auto']} /><Tooltip /><Line dataKey="y" name={block.y_label} type="linear" stroke="#13294b" dot /></LineChart> : <ScatterChart><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="x" name={block.x_label} type="number" /><YAxis dataKey="y" name={block.y_label} type="number" /><Tooltip cursor={{ strokeDasharray: '3 3' }} /><Scatter data={block.points} fill="#4b9cd3" /></ScatterChart>}</ResponsiveContainer></figure>
}

export function CampaignResult({ id, result, skill, operation }: { id: string; result: Result; skill?: string; operation?: string }) {
  const view = present(result)
  const [expanded, setExpanded] = useState(false)
  const dialog = useRef<HTMLDialogElement | null>(null)
  useEffect(() => {
    if (expanded) dialog.current?.showModal()
    else if (dialog.current?.open) { dialog.current.close(); document.getElementById(`result-${id}`)?.querySelector('button')?.focus() }
  }, [expanded, id])
  const [chart, setChart] = useState(0)
  const [molecules, setMolecules] = useState(false)
  const charts = view.blocks.filter((block): block is Extract<Block, { type: 'chart' }> => block.type === 'chart')
  const png = typeof result.figure_png_base64 === 'string' && result.figure_png_base64.length < 2_000_000 && /^[A-Za-z0-9+/=]+$/.test(result.figure_png_base64) ? result.figure_png_base64 : null
  const moleculeBlocks = view.blocks.filter((block): block is Extract<Block, { type: 'molecules' }> => block.type === 'molecules')
  const card = <article id={`result-${id}`} className={`campaign-result-card${expanded ? ' campaign-result-card--expanded' : ''}`} aria-label={view.title}>
    <header><div><p className="workspace-muted">{skill ? readable(skill) : 'Scientific result'}</p><h3>{view.title}</h3></div><button aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? 'Close expanded view' : 'Expand result'}</button></header>
    <p>{view.summary}</p>
    {charts.length > 0 && <section>{charts.length > 1 && <label>Chart <select value={chart} onChange={e => setChart(Number(e.target.value))}>{charts.map((block, index) => <option key={index} value={index}>{block.title}</option>)}</select></label>}<Chart block={charts[Math.min(chart, charts.length - 1)]} /></section>}
    {view.blocks.filter((block): block is Extract<Block, { type: 'table' }> => block.type === 'table').map((block, index) => <section key={index}><h4>{block.title}</h4><DataTable block={block} /></section>)}
    {moleculeBlocks.length > 0 && <section><button aria-expanded={molecules} onClick={() => setMolecules(value => !value)}>{molecules ? 'Hide molecules' : `View molecules (${moleculeBlocks.reduce((n, b) => n + b.items.length, 0)})`}</button>{(molecules || moleculeBlocks.reduce((n, b) => n + b.items.length, 0) <= 3) && moleculeBlocks.map((block, index) => <Molecules key={index} block={block} />)}</section>}
    {Array.isArray(result.matches) && result.matches.filter(object).map(match => typeof match.cid === 'number' && Number.isInteger(match.cid) && match.cid > 0 ? <a key={match.cid} href={`https://pubchem.ncbi.nlm.nih.gov/compound/${match.cid}`} target="_blank" rel="noreferrer">PubChem CID {match.cid}</a> : null)}
    {result.source === 'CAS Common Chemistry' && typeof result.cas_rn === 'string' && /^[1-9][0-9]{1,6}-[0-9]{2}-[0-9]$/.test(result.cas_rn) && <a href={`https://commonchemistry.cas.org/detail?cas_rn=${result.cas_rn}`} target="_blank" rel="noreferrer">CAS Common Chemistry record</a>}
    <footer className="campaign-result-toolbar"><button onClick={() => saveFile('scientific-result.json', JSON.stringify(result, null, 2))}>Download result</button>{png && <a download="campaign-figure.png" href={`data:image/png;base64,${png}`}>Download figure (PNG)</a>}</footer>
    <details><summary>Source and technical details</summary><p>Recorded action: {id}{operation ? ` · ${operation}` : ''}</p><pre>{JSON.stringify(result, (key, value) => key === 'figure_png_base64' ? '[downloadable PNG figure]' : value, 2)}</pre></details>
  </article>
  return <>{!expanded && card}<dialog ref={dialog} className="campaign-result-dialog" onCancel={() => setExpanded(false)} onClose={() => setExpanded(false)} aria-label={view.title}>{expanded && card}</dialog></>
}
