import { useState, useEffect } from 'react'
import { AnalyticsSummary } from '../types/analytics'
import { getAnalyticsSummary } from '../api'

export function useAnalytics() {
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    load()
  }, [])

  async function load() {
    try {
      setLoading(true)
      const data = await getAnalyticsSummary()
      setAnalytics(data)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return {
    analytics,
    loading,
    error,
    reload: load,
  }
}
