import { ReactNode, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthProfile } from '../hooks/useAuthProfile'
import { ShieldCheck } from 'lucide-react'

interface AdminGuardProps {
  children: ReactNode
}

export function AdminGuard({ children }: AdminGuardProps) {
  const navigate = useNavigate()
  const { data: profile, isLoading } = useAuthProfile()

  const isAdmin = Boolean(profile?.user.roles?.includes('admin'))

  useEffect(() => {
    if (!isLoading && !isAdmin) {
      navigate('/', { replace: true })
    }
  }, [isLoading, isAdmin, navigate])

  if (isLoading) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#f5f5f5'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ marginBottom: '1rem' }}><ShieldCheck size={40} aria-hidden="true" /></div>
          <div style={{ fontSize: '1.2rem', color: '#666' }}>Loading admin access…</div>
        </div>
      </div>
    )
  }

  if (!isAdmin) {
    return null
  }

  return <>{children}</>
}
