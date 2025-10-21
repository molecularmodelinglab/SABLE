import { useLocation, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import { AppShell } from './components/AppShell'
import { AuthGuard } from './components/AuthGuard'
import { DashboardPage } from './pages/DashboardPage'
import { NewRunPage } from './pages/NewRunPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { LoginPage } from './pages/LoginPage'

export function App() {
  const location = useLocation()
  const headerContent = useMemo(() => buildHeader(location.pathname), [location.pathname])

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <AuthGuard>
            <AppShell header={<Header title={headerContent.title} description={headerContent.description} />}>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/runs/new" element={<NewRunPage />} />
                <Route path="/runs/:id" element={<RunDetailPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          </AuthGuard>
        }
      />
    </Routes>
  )
}

function Header({ title, description }: { title: string; description: string }) {
  const [timestamp, setTimestamp] = useState(() => new Date().toLocaleString())

  useEffect(() => {
    const timer = window.setInterval(() => setTimestamp(new Date().toLocaleString()), 30000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="app-header">
      <div>
        <div className="app-header__title">{title}</div>
        <div className="app-header__description">{description}</div>
      </div>
      <div className="app-header__status">
        <span>{timestamp}</span>
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
  return {
    title: 'Campaign Dashboard',
    description: 'Overview of optimization runs, status, and historical metrics.',
  }
}
