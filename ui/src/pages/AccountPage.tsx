import { FormEvent, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, KeyRound, RefreshCw, Server, Trash2 } from 'lucide-react'
import {
  createProviderCredential,
  deleteProviderCredential,
  getBoltzSettings,
  listProviderCredentials,
  requestSelfHostedBoltzAccess,
  updateBoltzSettings,
  validateProviderCredential,
  type BoltzExecutionPreference,
  type BoltzMetric,
  type BoltzProvider,
} from '../api'

const PLATFORM_METRICS: { value: BoltzMetric; label: string }[] = [
  { value: 'boltz_binding_confidence', label: 'Binding confidence' },
  { value: 'boltz_optimization_score', label: 'Optimization score' },
  { value: 'boltz_structure_confidence', label: 'Structure confidence' },
]

export function AccountPage() {
  const queryClient = useQueryClient()
  const [provider, setProvider] = useState<BoltzProvider>('self_hosted')
  const [credentialId, setCredentialId] = useState('')
  const [executionPreference, setExecutionPreference] = useState<BoltzExecutionPreference>('auto')
  const [metrics, setMetrics] = useState<BoltzMetric[]>(PLATFORM_METRICS.map(({ value }) => value))
  const [credentialName, setCredentialName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const settingsQuery = useQuery({ queryKey: ['boltz-settings'], queryFn: getBoltzSettings })
  const credentialsQuery = useQuery({ queryKey: ['provider-credentials'], queryFn: listProviderCredentials })
  const settings = settingsQuery.data
  const credentials = credentialsQuery.data ?? []

  useEffect(() => {
    if (!settings?.provider) return
    setProvider(settings.provider)
    setCredentialId(settings.credential_id ?? '')
    setExecutionPreference(settings.execution_preference)
    if (settings.provider === 'platform' && settings.metrics.length) setMetrics(settings.metrics)
  }, [settings])

  const clearNotice = () => {
    setMessage(null)
    setError(null)
  }

  const handleRequestAccess = async () => {
    clearNotice()
    setBusyAction('request')
    try {
      await requestSelfHostedBoltzAccess()
      await queryClient.invalidateQueries({ queryKey: ['boltz-settings'] })
      setMessage('Your request is now awaiting administrator review.')
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to submit the request.')
    } finally {
      setBusyAction(null)
    }
  }

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    clearNotice()
    if (provider === 'platform' && !credentialId) {
      setError('Select an active Boltz Platform credential.')
      return
    }
    setBusyAction('save')
    try {
      await updateBoltzSettings({
        provider,
        credential_id: provider === 'platform' ? credentialId : undefined,
        execution_preference: provider === 'platform' ? 'library_screen' : executionPreference,
        metrics: provider === 'platform' ? metrics : ['binding_affinity'],
      })
      await queryClient.invalidateQueries({ queryKey: ['boltz-settings'] })
      setMessage('Boltz defaults saved.')
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save Boltz defaults.')
    } finally {
      setBusyAction(null)
    }
  }

  const handleCreateCredential = async () => {
    clearNotice()
    if (!credentialName.trim() || !apiKey.trim()) return
    setBusyAction('credential-new')
    try {
      const credential = await createProviderCredential({
        name: credentialName.trim(),
        api_key: apiKey.trim(),
        validate: true,
      })
      setApiKey('')
      setCredentialName('')
      await queryClient.invalidateQueries({ queryKey: ['provider-credentials'] })
      if (credential.status === 'active') {
        setCredentialId(credential.id)
        setMessage('Credential validated. Save defaults to use it for future runs.')
      } else {
        setError('The credential was saved but Boltz Platform could not validate it.')
      }
    } catch (credentialError) {
      setApiKey('')
      setError(credentialError instanceof Error ? credentialError.message : 'Unable to save the credential.')
    } finally {
      setBusyAction(null)
    }
  }

  const handleValidate = async (id: string) => {
    clearNotice()
    setBusyAction(id)
    try {
      const credential = await validateProviderCredential(id)
      await queryClient.invalidateQueries({ queryKey: ['provider-credentials'] })
      if (credential.status === 'active') setCredentialId(id)
    } catch (validationError) {
      setError(validationError instanceof Error ? validationError.message : 'Credential validation failed.')
    } finally {
      setBusyAction(null)
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Remove this saved credential?')) return
    clearNotice()
    setBusyAction(id)
    try {
      await deleteProviderCredential(id)
      if (credentialId === id) setCredentialId('')
      await queryClient.invalidateQueries({ queryKey: ['provider-credentials'] })
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'Unable to remove the credential.')
    } finally {
      setBusyAction(null)
    }
  }

  const toggleMetric = (metric: BoltzMetric) => {
    setMetrics((current) => current.includes(metric)
      ? current.filter((value) => value !== metric)
      : [...current, metric])
  }

  if (settingsQuery.isLoading) return <div className="account-state">Loading account settings...</div>
  if (settingsQuery.isError || !settings) return <div className="account-state account-state--error">Unable to load account settings.</div>

  return (
    <div className="account-page">
      <section className="account-section">
        <div className="account-section__heading">
          <div>
            <h1>Boltz compute</h1>
            <p>Choose the default infrastructure used by every new optimization run.</p>
          </div>
          <span className={`access-status access-status--${settings.access_status}`}>{settings.access_status.replace('_', ' ')}</span>
        </div>

        <div className="access-request">
          <Server size={22} aria-hidden="true" />
          <div>
            <strong>SABLE-hosted Boltz</strong>
            <p>Managed compute requires administrator approval. Requests are reviewed inside SABLE.</p>
          </div>
          {!settings.can_use_self_hosted && settings.access_status !== 'pending' && (
            <button type="button" className="secondary" onClick={handleRequestAccess} disabled={busyAction !== null}>
              Request access
            </button>
          )}
          {settings.can_use_self_hosted && <Check size={20} className="access-request__approved" aria-label="Approved" />}
        </div>
      </section>

      <form className="account-section account-form" onSubmit={handleSave}>
        <div className="account-section__heading">
          <div>
            <h2>Run defaults</h2>
            <p>The launch screen uses these settings automatically.</p>
          </div>
        </div>

        <div className="run-options__segmented" role="group" aria-label="Boltz provider">
          <button type="button" className={provider === 'self_hosted' ? 'active' : ''} onClick={() => setProvider('self_hosted')} disabled={!settings.can_use_self_hosted}>
            <Server size={17} /> SABLE hosted
          </button>
          <button type="button" className={provider === 'platform' ? 'active' : ''} onClick={() => setProvider('platform')}>
            <KeyRound size={17} /> Bring your own key
          </button>
        </div>

        {provider === 'self_hosted' ? (
          <label htmlFor="execution-preference">
            Execution preference
            <select id="execution-preference" value={executionPreference} onChange={(event) => setExecutionPreference(event.target.value as BoltzExecutionPreference)}>
              <option value="auto">Automatic</option>
              <option value="prediction">Prediction</option>
              <option value="library_screen">Library screen</option>
            </select>
          </label>
        ) : (
          <div className="platform-options">
            <label htmlFor="provider-credential">
              Active credential
              <select id="provider-credential" value={credentialId} onChange={(event) => setCredentialId(event.target.value)}>
                <option value="">Select a credential</option>
                {credentials.map((credential) => (
                  <option key={credential.id} value={credential.id} disabled={credential.status !== 'active'}>
                    {credential.name} (ends in {credential.key_hint}) - {credential.status}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="metric-options">
              <legend>Platform metrics</legend>
              {PLATFORM_METRICS.map((metric) => (
                <label key={metric.value}>
                  <input type="checkbox" checked={metrics.includes(metric.value)} onChange={() => toggleMetric(metric.value)} />
                  {metric.label}
                </label>
              ))}
            </fieldset>
            <p className="run-options__hint">Platform uses library screening. Optimization scores are relative to the selected protein.</p>
          </div>
        )}

        {message && <p className="form-notice form-notice--success">{message}</p>}
        {error && <p className="form-notice form-notice--error" role="alert">{error}</p>}
        <div><button type="submit" className="primary" disabled={busyAction !== null || (provider === 'self_hosted' && !settings.can_use_self_hosted)}>Save defaults</button></div>
      </form>

      <section className="account-section">
        <div className="account-section__heading">
          <div>
            <h2>Boltz Platform credentials</h2>
            <p>Keys are encrypted at rest and are never returned by the API.</p>
          </div>
        </div>
        <div className="credential-list">
          {credentials.map((credential) => (
            <div className="credential-list__item" key={credential.id}>
              <span><strong>{credential.name}</strong> ending in {credential.key_hint}</span>
              <span className={`credential-status credential-status--${credential.status}`}>{credential.status}</span>
              {credential.status !== 'active' && <button type="button" className="icon-button" title="Validate credential" onClick={() => handleValidate(credential.id)}><RefreshCw size={17} /></button>}
              <button type="button" className="icon-button icon-button--danger" title="Remove credential" onClick={() => handleDelete(credential.id)}><Trash2 size={17} /></button>
            </div>
          ))}
          {!credentials.length && <p className="account-empty">No credentials saved.</p>}
        </div>
        <div className="credential-create">
          <label htmlFor="credential-name">Name<input id="credential-name" value={credentialName} onChange={(event) => setCredentialName(event.target.value)} placeholder="Research account" /></label>
          <label htmlFor="credential-key">API key<input id="credential-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" placeholder="Enter once to validate" /></label>
          <button type="button" className="secondary" onClick={handleCreateCredential} disabled={busyAction !== null || !credentialName.trim() || !apiKey.trim()}>Validate and save</button>
        </div>
      </section>
    </div>
  )
}