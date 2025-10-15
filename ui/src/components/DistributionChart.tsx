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
          <defs>
            <linearGradient id="distributionGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#2563eb" stopOpacity={0.4} />
              <stop offset="95%" stopColor="#2563eb" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.25)" />
          <XAxis dataKey="bucket" tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} allowDecimals />
          <Tooltip formatter={(value: number) => value.toString()} />
          <Area type="monotone" dataKey="count" stroke="#2563eb" fillOpacity={1} fill="url(#distributionGradient)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
