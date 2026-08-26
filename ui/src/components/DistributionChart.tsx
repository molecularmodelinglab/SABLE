import { useMemo } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export function DistributionChart({ label, values }: { label: string; values: number[] }) {
  const data = useMemo(() => {
    if (!values.length) return []
    const sorted = [...values].sort((a, b) => a - b)
    const min = sorted[0]
    const max = sorted[sorted.length - 1]
    if (min === max) {
      return [{ bucket: min.toFixed(2), count: sorted.length }]
    }
    const binCount = Math.min(20, Math.max(10, Math.floor(Math.sqrt(sorted.length))))
    const binSize = (max - min) / binCount
    const buckets = Array.from({ length: binCount }, (_, idx) => ({
      bucket: (min + idx * binSize + binSize / 2).toFixed(2),
      count: 0,
    }))
    sorted.forEach((value) => {
      const index = Math.min(binCount - 1, Math.floor((value - min) / binSize))
      buckets[index].count += 1
    })
    return buckets
  }, [values])

  if (!data.length) {
    return <div className="distribution-chart__empty">Not enough data to plot {label}.</div>
  }

  return (
    <div className="distribution-chart">
      <div className="distribution-chart__title">{label}</div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(19, 41, 75, 0.2)" />
          <XAxis dataKey="bucket" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} allowDecimals />
          <Tooltip formatter={(value: number) => value.toString()} />
          <Area type="monotone" dataKey="count" stroke="#13294B" fill="#4B9CD3" fillOpacity={0.35} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
