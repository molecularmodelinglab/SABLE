import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { login, getAccessToken } from '../api'
import { CircleAlert, Info, LoaderCircle } from 'lucide-react'

export function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Check if already logged in
  useEffect(() => {
    const token = getAccessToken()
    if (token) {
      navigate('/dashboard', { replace: true })
    }
  }, [navigate])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    
    if (!email.trim()) {
      setError('Email is required')
      return
    }

    if (!password.trim()) {
      setError('Password is required')
      return
    }

    setLoading(true)
    setError(null)

    try {
      await login({
        email: email.trim(),
        password: password,
      })
      
      // Redirect to dashboard
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError((err as Error).message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page" style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: '#f5f5f5',
      padding: '1rem'
    }}>
      <div className="auth-card" style={{
        maxWidth: '450px',
        width: '100%',
        padding: '3rem 2rem',
        backgroundColor: '#fff',
        borderRadius: '12px',
        boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 className="auth-title" style={{
            fontSize: '2.5rem',
            fontWeight: 'bold',
            marginBottom: '0.5rem',
            color: '#13294B'
          }}>
             SABLE
          </h1>
          <p style={{ color: '#666', fontSize: '0.95rem' }}>
            Synthetically-accessible Agentic Bayesian Ligand Exploration
          </p>
        </div>

        <form className="auth-form" onSubmit={handleSubmit}>
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{
              display: 'block',
              marginBottom: '0.5rem',
              fontWeight: 600,
              color: '#333',
              fontSize: '0.9rem'
            }}>
              Email *
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="your.email@example.com"
              required
              autoFocus
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '2px solid #e0e0e0',
                borderRadius: '8px',
                fontSize: '1rem',
                transition: 'border-color 0.2s',
                outline: 'none'
              }}
              onFocus={(e) => e.target.style.borderColor = '#4B9CD3'}
              onBlur={(e) => e.target.style.borderColor = '#e0e0e0'}
            />
          </div>

          <div style={{ marginBottom: '2rem' }}>
            <label style={{
              display: 'block',
              marginBottom: '0.5rem',
              fontWeight: 600,
              color: '#333',
              fontSize: '0.9rem'
            }}>
              Password *
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              required
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '2px solid #e0e0e0',
                borderRadius: '8px',
                fontSize: '1rem',
                transition: 'border-color 0.2s',
                outline: 'none'
              }}
              onFocus={(e) => e.target.style.borderColor = '#4B9CD3'}
              onBlur={(e) => e.target.style.borderColor = '#e0e0e0'}
            />
          </div>

          {error && (
            <div className="auth-error" style={{
              padding: '0.75rem 1rem',
              marginBottom: '1.5rem',
              backgroundColor: '#fee',
              border: '1px solid #fcc',
              borderRadius: '8px',
              color: '#c00',
              fontSize: '0.9rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem'
            }}>
              <CircleAlert size={18} aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          <button
            className="auth-submit"
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '0.875rem',
              backgroundColor: loading ? 'rgba(19, 41, 75, 0.45)' : '#4B9CD3',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '1rem',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s',
              boxShadow: 'none',
            }}
            onMouseOver={(e) => {
              if (!loading) {
                e.currentTarget.style.backgroundColor = '#13294B'
              }
            }}
            onMouseOut={(e) => {
              if (!loading) {
                e.currentTarget.style.backgroundColor = '#4B9CD3'
              }
            }}
          >
            {loading ? (
              <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
                <LoaderCircle size={18} style={{ animation: 'spin 1s linear infinite' }} aria-hidden="true" />
                Logging in...
              </span>
            ) : (
              'Login'
            )}
          </button>
        </form>

        <div className="auth-footer" style={{
          marginTop: '2rem',
          padding: '1rem',
          backgroundColor: '#f8f9fa',
          borderRadius: '8px',
          fontSize: '0.85rem',
          color: '#666'
        }}>
          <p style={{ margin: 0, marginBottom: '0.5rem', fontWeight: 600 }}>
            <Info size={16} style={{ verticalAlign: 'text-bottom', marginRight: '0.35rem' }} aria-hidden="true" /> Session Information:
          </p>
          <ul style={{ margin: 0, paddingLeft: '1.5rem' }}>
            <li>Sessions expire after 24 hours of inactivity</li>
          </ul>
          <p style={{ marginTop: '1rem', fontSize: '0.85rem' }}>
            Need an account? <Link to="/register">Create one here</Link>.
          </p>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
