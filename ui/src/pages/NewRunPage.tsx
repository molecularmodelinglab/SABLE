import { FormEvent, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createRun, type RunInfo } from '../api'

export function NewRunPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [prompt, setPrompt] = useState('Optimize aspirin for higher QED with lower TPSA across three BO iterations.')
  const [maxIterations, setMaxIterations] = useState<number | ''>('' as const)
  const [batchSize, setBatchSize] = useState<number | ''>('' as const)
  const [note, setNote] = useState('')

  const mutation = useMutation({
    mutationFn: () => createRun(prompt.trim(), typeof maxIterations === 'number' ? maxIterations : undefined, typeof batchSize === 'number' ? batchSize : undefined, note.trim() || undefined),
    onSuccess: (run: RunInfo) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      navigate(`/runs/${run.id}`)
    },
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!prompt.trim()) return
    mutation.mutate()
  }

  return (
    <div className="new-run">
      <div className="new-run__header">
        <div>
          <h1>Launch New Optimization</h1>
          <p>Specify a starting compound, target property, and bounds for the LIZARD agent.</p>
        </div>
      </div>

      <form className="new-run__form" onSubmit={onSubmit}>
        <label>
          <span>Optimization prompt</span>
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={8}
            placeholder="Describe the optimization objective, constraints, and any contextual notes."
            required
          />
        </label>

        <label>
          <span>Notebook note (optional)</span>
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Short reminder for future you."
          />
        </label>

        <div className="new-run__actions">
          <button type="button" onClick={() => navigate(-1)} className="ghost">Cancel</button>
          <button type="submit" className="primary" disabled={mutation.isLoading}>
            {mutation.isLoading ? 'Launching...' : 'Launch optimization'}
          </button>
        </div>
        {mutation.isError && (
          <div className="new-run__error">{(mutation.error as Error).message || 'Failed to start run.'}</div>
        )}
      </form>
    </div>
  )
}
