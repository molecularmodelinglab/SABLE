import { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

const navLinks = [
  { to: '/', label: 'Dashboard', icon: '📊', end: true },
  { to: '/runs/new', label: 'New Run', icon: '🎯', end: false },
]

export function AppShell({ header, children }: { header: ReactNode; children: ReactNode }) {
  const location = useLocation()

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
        <div className="app-shell__footer">
          <div>Molecular Modelling Lab</div>
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
