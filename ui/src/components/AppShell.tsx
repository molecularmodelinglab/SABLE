import { ReactNode, useMemo, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { logout } from '../api'
import { useAuthProfile } from '../hooks/useAuthProfile'

export function AppShell({ header, children }: { header: ReactNode; children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: profile } = useAuthProfile()
  const [showUserMenu, setShowUserMenu] = useState(false)

  const navLinks = useMemo(() => {
    const baseLinks = [
      { to: '/', label: 'Dashboard', icon: '📊', end: true },
      { to: '/runs/new', label: 'New Run', icon: '🎯', end: false },
    ]

    if (profile?.user.roles?.includes('admin')) {
      baseLinks.push({ to: '/admin', label: 'Admin', icon: '🛡️', end: false })
    }

    return baseLinks
  }, [profile?.user.roles])

  async function handleLogout() {
    try {
      await logout()
      queryClient.removeQueries({ queryKey: ['auth'] })
      navigate('/login', { replace: true })
    } catch (error) {
      console.error('Logout error:', error)
      navigate('/login', { replace: true })
    }
  }

  return (
    <div className="app-shell">
      <aside className="app-shell__sidebar">
        <div className="app-shell__brand">
          <div className="app-shell__logo">🦎</div>
          <div>
            <div className="app-shell__title">LIZARD</div>
            <div className="app-shell__subtitle">LIgand optimiZation via Agentic Research and Discovery</div>
          </div>
        </div>
        <nav className="app-shell__nav">
          {navLinks.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }: { isActive: boolean }) =>
                [
                  'app-shell__nav-link',
                  isActive ? 'app-shell__nav-link--active' : '',
                  link.to !== '/' && location.pathname.startsWith(link.to) && !isActive ? 'app-shell__nav-link--pending' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
              }
            >
              <span className="app-shell__nav-icon" aria-hidden="true">{link.icon}</span>
              <span>{link.label}</span>
            </NavLink>
          ))}
        </nav>
        
        {profile && (
          <div style={{
            padding: '1rem',
            borderTop: '1px solid rgba(255, 255, 255, 0.1)',
            marginTop: 'auto'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.75rem',
              backgroundColor: 'rgba(255, 255, 255, 0.05)',
              borderRadius: '8px',
              marginBottom: '0.5rem',
              cursor: 'pointer',
              position: 'relative'
            }}
            onClick={() => setShowUserMenu(!showUserMenu)}
            >
              <div style={{
                width: '36px',
                height: '36px',
                borderRadius: '50%',
                backgroundColor: '#667eea',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontWeight: 'bold',
                fontSize: '0.9rem'
              }}>
                {profile.user.username.charAt(0).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: '0.9rem',
                  fontWeight: 600,
                  color: 'white',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap'
                }}>
                  {profile.user.username}
                </div>
                <div style={{
                  fontSize: '0.75rem',
                  color: 'rgba(255, 255, 255, 0.6)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap'
                }}>
                  {profile.user.email}
                </div>
              </div>
              <div style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                {showUserMenu ? '▲' : '▼'}
              </div>
            </div>
            
            {showUserMenu && (
              <div style={{
                backgroundColor: 'rgba(0, 0, 0, 0.3)',
                borderRadius: '8px',
                overflow: 'hidden',
                marginTop: '0.5rem'
              }}>
                <button
                  onClick={handleLogout}
                  style={{
                    width: '100%',
                    padding: '0.75rem',
                    backgroundColor: 'transparent',
                    border: 'none',
                    color: '#ff6b6b',
                    fontSize: '0.9rem',
                    fontWeight: 500,
                    cursor: 'pointer',
                    textAlign: 'left',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    transition: 'background-color 0.2s'
                  }}
                  onMouseOver={(e) => e.currentTarget.style.backgroundColor = 'rgba(255, 107, 107, 0.1)'}
                  onMouseOut={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                >
                  <span>🚪</span>
                  <span>Logout</span>
                </button>
              </div>
            )}
          </div>
        )}
        
        <div className="app-shell__footer">
          <div>Molecular Modelling Lab</div>
          <div>UNC - Chapel Hill</div>
          <div className="app-shell__footer-meta">{new Date().getFullYear()}</div>
        </div>
      </aside>
      <div className="app-shell__main">
        <header className="app-shell__header">{header}</header>
        <main className="app-shell__content">{children}</main>
      </div>
    </div>
  )
}
