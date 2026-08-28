import { useLocation, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import { AppShell } from './components/AppShell'
import { AuthGuard } from './components/AuthGuard'
import { AdminGuard } from './components/AdminGuard'
import { DashboardPage } from './pages/DashboardPage'
import { NewRunPage } from './pages/NewRunPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { AdminDashboardPage } from './pages/AdminDashboardPage'
import { AccountPage } from './pages/AccountPage'
import { LandingPage } from './pages/LandingPage'
import { useAuthProfile } from './hooks/useAuthProfile'

export function App() {
  const location = useLocation()
  const headerContent = useMemo(() => buildHeader(location.pathname), [location.pathname])

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/*"
        element={
          <AuthGuard>
            <AppShell header={<Header title={headerContent.title} description={headerContent.description} pathname={location.pathname} />}>
              <Routes>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/runs/new" element={<NewRunPage />} />
                <Route path="/runs/:id" element={<RunDetailPage />} />
                <Route path="/account" element={<AccountPage />} />
                <Route
                  path="/admin"
                  element={
                    <AdminGuard>
                      <AdminDashboardPage />
                    </AdminGuard>
                  }
                />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </AppShell>
          </AuthGuard>
        }
      />
    </Routes>
  )
}

function Header({ title, description, pathname }: { title: string; description: string; pathname: string }) {
  const [timestamp, setTimestamp] = useState(() => new Date().toLocaleString())
  const navigate = useNavigate()
  const { data: profile } = useAuthProfile()

  useEffect(() => {
    const timer = window.setInterval(() => setTimestamp(new Date().toLocaleString()), 30000)
    return () => window.clearInterval(timer)
  }, [])

  const isAdmin = Boolean(profile?.user.roles?.includes('admin'))
  const onAdminView = pathname.startsWith('/admin')

  return (
    <div className="app-header">
      <div>
        <div className="app-header__title">{title}</div>
        <div className="app-header__description">{description}</div>
      </div>
      <div className="app-header__status">
        <span>{timestamp}</span>
        {isAdmin && (
          <div className="view-toggle" role="group" aria-label="Select dashboard view">
            <button
              type="button"
              className={!onAdminView ? 'active' : ''}
              aria-pressed={!onAdminView}
              onClick={() => {
                if (onAdminView) {
                  navigate('/dashboard')
                }
              }}
            >
              Workflows
            </button>
            <button
              type="button"
              className={onAdminView ? 'active' : ''}
              aria-pressed={onAdminView}
              onClick={() => {
                if (!onAdminView) {
                  navigate('/admin')
                }
              }}
            >
              Admin
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function buildHeader(pathname: string) {
  if (pathname.startsWith('/runs/new')) {
    return {
      title: 'Launch Optimization',
      description: 'Provide the agent with a fresh optimization brief and parameters.',
    }
  }
  if (pathname.startsWith('/runs/')) {
    return {
      title: 'Run Details',
      description: 'Track live progress, checkpoints, and outputs for this campaign.',
    }
  }
  if (pathname.startsWith('/admin')) {
    return {
      title: 'Administrator Console',
      description: 'Operations, security signals, and aggregate analytics across the platform.',
    }
  }
  if (pathname.startsWith('/account')) {
    return {
      title: 'Account Settings',
      description: 'Manage compute access, provider credentials, and run defaults.',
    }
  }
  return {
    title: 'Campaign Dashboard',
    description: 'Overview of optimization runs, status, and historical metrics.',
  }
}
