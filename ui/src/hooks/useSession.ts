import { useState, useEffect } from 'react'
import { AuthProfile, AuthUser, Session, LoginRequest } from '../types/session'
import { clearAccessToken, getAuthProfile, login, logout } from '../api'

export function useSession() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadSession()
  }, [])

  async function loadSession() {
    try {
      setLoading(true)
      const profile = await getAuthProfile()
      applyProfile(profile)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
      setUser(null)
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
      applyProfile({ user: response.user, session: response.session })
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
      clearAccessToken()
      setUser(null)
      setSession(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function applyProfile(profile: AuthProfile) {
    setUser(profile.user)
    setSession(profile.session)
  }

  return {
    user,
    session,
    loading,
    error,
    login: handleLogin,
    logout: handleLogout,
    reload: loadSession,
  }
}
