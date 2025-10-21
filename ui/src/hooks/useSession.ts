import { useState, useEffect } from 'react'
import { Session, LoginRequest } from '../types/session'
import { login, logout, getCurrentSession } from '../api'

export function useSession() {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadSession()
  }, [])

  async function loadSession() {
    try {
      setLoading(true)
      const sess = await getCurrentSession()
      setSession(sess)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
      setSession(null)
    } finally {
      setLoading(false)
    }
  }

  async function handleLogin(req: LoginRequest) {
    try {
      setLoading(true)
      setError(null)
      const response = await login(req)
      await loadSession()
      return response
    } catch (err) {
      setError((err as Error).message)
      throw err
    } finally {
      setLoading(false)
    }
  }

  async function handleLogout() {
    try {
      await logout()
      setSession(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return {
    session,
    loading,
    error,
    login: handleLogin,
    logout: handleLogout,
    reload: loadSession,
  }
}
