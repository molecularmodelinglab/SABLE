import { ReactNode, useMemo, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { logout } from '../api'
import { useAuthProfile } from '../hooks/useAuthProfile'
import { BarChart3, ChevronDown, ChevronUp, LogOut, Settings, ShieldCheck, Target } from 'lucide-react'

export function AppShell({ header, children }: { header: ReactNode; children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: profile } = useAuthProfile()

  const [showUserMenu, setShowUserMenu] = useState(false)

  const navLinks = useMemo(() => {
    const baseLinks = [
      { to: '/dashboard', label: 'Dashboard', icon: BarChart3, end: true },
      { to: '/runs/new', label: 'New Run', icon: Target, end: false },
      { to: '/account', label: 'Settings & Boltz API', icon: Settings, end: false },
    ]

    if (profile?.user.roles?.includes('admin')) {
      baseLinks.push({ to: '/admin', label: 'Admin', icon: ShieldCheck, end: false })
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
          {/* <div className="app-shell__logo" aria-hidden="true">S</div> */}
          <div>
            <div className="app-shell__title">SABLE</div>
            {/* <div className="app-shell__subtitle">Synthetically-accessible Agentic Bayesian Ligand Exploration</div> */}
          </div>
        </div>
        <nav className="app-shell__nav">
          {navLinks.map((link) => {
            const Icon = link.icon
            return (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }: { isActive: boolean }) =>
                [
                  'app-shell__nav-link',
                  isActive ? 'app-shell__nav-link--active' : '',
                  link.to !== '/dashboard' && location.pathname.startsWith(link.to) && !isActive ? 'app-shell__nav-link--pending' : '',
                ]
                  .filter(Boolean)
                  .join(' ')
              }
            >
              <span className="app-shell__nav-icon" aria-hidden="true"><Icon size={18} /></span>
              <span>{link.label}</span>
            </NavLink>
            )
          })}
        </nav>
        
        {profile && (
          <div className="app-shell__profile">
            <button className="app-shell__profile-trigger" onClick={() => setShowUserMenu(!showUserMenu)} aria-expanded={showUserMenu}>
              <div className="app-shell__avatar">
                {profile.user.username.charAt(0).toUpperCase()}
              </div>
              <div className="app-shell__profile-details">
                <div className="app-shell__profile-name">
                  {profile.user.username}
                </div>
                <div className="app-shell__profile-email">
                  {profile.user.email}
                </div>
              </div>
              <div className="app-shell__profile-toggle">
                {showUserMenu ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </div>
            </button>
            
            {showUserMenu && (
              <div className="app-shell__user-menu">
                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    navigate('/account')
                  }}
                  className="app-shell__user-menu-item"
                >
                  <Settings size={17} />
                  <span>Account settings</span>
                </button>
                <button
                  onClick={handleLogout}
                  className="app-shell__user-menu-item app-shell__user-menu-item--danger"
                >
                  <LogOut size={17} />
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
