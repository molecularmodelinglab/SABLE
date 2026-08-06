import { FormEvent, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createRun } from '../api'

const EXAMPLE_PROMPTS = [
  {
    title: 'Targeted inhibitor design',
    prompt: 'Starting from [ligand] design a selective small-molecule inhibitor for [target] with oral drug-like properties.',
  },
  {
    title: 'Analog exploration',
    prompt: 'Find purchasable building blocks suitable for synthesizing analogs of [compound or scaffold].',
  },
  {
    title: 'Candidate prioritization',
    prompt: 'Evaluate candidate compounds against [protein target] and optimize for binding, ADMET, and synthetic accessibility.',
  },
]

export function NewRunPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const promptRef = useRef<HTMLTextAreaElement | null>(null)
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const selectExample = (examplePrompt: string) => {
    setPrompt(examplePrompt)
    setError(null)
    promptRef.current?.focus()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedPrompt = prompt.trim()
    if (!trimmedPrompt || isSubmitting) return

    setIsSubmitting(true)
    setError(null)

    try {
      const run = await createRun(trimmedPrompt)
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
      navigate(`/runs/${run.id}`)
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : 'Failed to start the run.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="new-run">
      <div className="new-run__header">
        <div>
          <h1>Start a Run</h1>
          <p>Describe the molecular design or analysis task you want LIZARD to run.</p>
        </div>
      </div>

      <div className="prompt-run__layout">
        <form className="new-run__form prompt-run__form" onSubmit={handleSubmit}>
          <label htmlFor="run-prompt">
            Prompt
            <textarea
              ref={promptRef}
              id="run-prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Describe your target, desired compound properties, and any constraints."
              rows={10}
              disabled={isSubmitting}
              required
            />
          </label>

          {error && <p className="new-run__error" role="alert">{error}</p>}

          <div className="new-run__actions">
            <button type="submit" className="primary" disabled={isSubmitting || !prompt.trim()}>
              {isSubmitting ? 'Starting run...' : 'Start run'}
            </button>
          </div>
        </form>

        <aside className="prompt-examples" aria-labelledby="example-prompts-title">
          <div>
            <p className="prompt-examples__eyebrow">Get started</p>
            <h2 id="example-prompts-title">Example prompts</h2>
          </div>
          <div className="prompt-examples__list">
            {EXAMPLE_PROMPTS.map((example) => (
              <button
                key={example.title}
                className="prompt-example"
                type="button"
                onClick={() => selectExample(example.prompt)}
                disabled={isSubmitting}
              >
                <span>{example.title}</span>
                <small>{example.prompt}</small>
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  )
}
