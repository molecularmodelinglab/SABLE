import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { clearAccessToken, getAccessToken } from '../api'
import { useAuthProfile } from '../hooks/useAuthProfile'
import { LoaderCircle } from 'lucide-react'

interface AuthGuardProps {
  children: React.ReactNode
}

export function AuthGuard({ children }: AuthGuardProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = getAccessToken()
  const { data: profile, isLoading, isError } = useAuthProfile()

  useEffect(() => {
    if (!token) {
      navigate('/login', { replace: true })
    }
  }, [token, navigate])

  useEffect(() => {
    if (token && !isLoading && !profile) {
      if (isError) {
        clearAccessToken()
        queryClient.removeQueries({ queryKey: ['auth'] })
        navigate('/login', { replace: true })
      }
    }
  }, [token, isLoading, profile, isError, navigate, queryClient])

  if (!token || isLoading) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#f5f5f5'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ marginBottom: '1rem', color: '#4B9CD3' }}><LoaderCircle className="spin" size={40} aria-hidden="true" /></div>
          <div style={{ fontSize: '1.2rem', color: '#666' }}>Checking authentication...</div>
        </div>
      </div>
    )
  }

  if (!profile) {
    return null
  }

  return <>{children}</>
}
