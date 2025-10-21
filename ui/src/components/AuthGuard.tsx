import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCurrentSession, getSessionToken } from '../api'

interface AuthGuardProps {
  children: React.ReactNode
}

export function AuthGuard({ children }: AuthGuardProps) {
  const navigate = useNavigate()
  const [isChecking, setIsChecking] = useState(true)
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  useEffect(() => {
    checkAuth()
  }, [])

  async function checkAuth() {
    const token = getSessionToken()
    
    if (!token) {
      setIsChecking(false)
      setIsAuthenticated(false)
      navigate('/login', { replace: true })
      return
    }

    try {
      // Validate token with server
      await getCurrentSession()
      setIsAuthenticated(true)
    } catch (error) {
      // Token invalid or expired
      setIsAuthenticated(false)
      navigate('/login', { replace: true })
    } finally {
      setIsChecking(false)
    }
  }

  if (isChecking) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#f5f5f5'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🦎</div>
          <div style={{ fontSize: '1.2rem', color: '#666' }}>Checking authentication...</div>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return null
  }

  return <>{children}</>
}
