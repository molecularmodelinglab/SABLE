import { ReactNode } from 'react'

export type ResultsColumn = {
  key: string
  label: string
  render?: (row: Record<string, unknown>) => ReactNode
}

export function ResultsTable({
  rows,
  columns,
}: {
  rows: Record<string, unknown>[]
  columns: ResultsColumn[]
}) {
  if (!rows.length) {
    return <div className="results-table__empty">No results reported yet.</div>
  }

  return (
    <div className="results-table">
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx}>
              {columns.map((col) => (
                <td key={col.key}>
                  {col.render ? col.render(row) : formatCellValue(row[col.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatCellValue(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return value.toFixed(3)
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
