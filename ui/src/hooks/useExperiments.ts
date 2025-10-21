import { useState, useEffect } from 'react'
import { Experiment } from '../types/experiment'
import { listExperiments, getFailedExperiments } from '../api'

export function useExperiments(status?: string, autoRefresh = false) {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    load()
    
    if (autoRefresh) {
      const interval = setInterval(load, 5000)
      return () => clearInterval(interval)
    }
  }, [status, autoRefresh])

  async function load() {
    try {
      setLoading(true)
      const data = await listExperiments(status)
      setExperiments(data)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return {
    experiments,
    loading,
    error,
    reload: load,
  }
}

export function useFailedExperiments() {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    load()
  }, [])

  async function load() {
    try {
      setLoading(true)
      const data = await getFailedExperiments()
      setExperiments(data)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return {
    experiments,
    loading,
    error,
    reload: load,
  }
}
