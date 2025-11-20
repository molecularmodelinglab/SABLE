import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  abandonConversation,
  confirmConversation,
  sendConversationMessage,
  startConversation,
} from '../api'
import type { ConversationContext, ConversationState, TargetProperty } from '../types/conversation'

type ChatMessage = {
  id: string
  role: 'assistant' | 'user'
  text: string
}

function formatStateLabel(state: ConversationState | null): string {
  switch (state) {
    case 'greeting':
      return 'Greeting'
    case 'collecting_molecule':
      return 'Collecting molecule'
    case 'collecting_targets':
      return 'Collecting targets'
    case 'collecting_parameters':
      return 'Collecting parameters'
    case 'confirmation':
      return 'Awaiting confirmation'
    case 'completed':
      return 'Run launched'
    case 'abandoned':
      return 'Conversation abandoned'
    default:
      return 'Setting up'
  }
}

function describeTarget(target: TargetProperty): string {
  const modeLabel = target.mode === 'match'
    ? 'Match'
    : target.mode === 'maximize'
      ? 'Maximize'
      : 'Minimize'

  const base = `${modeLabel} ${target.name}`
  const value = target.mode === 'match' && target.target_value !== undefined && target.target_value !== null
    ? ` to ${target.target_value}`
    : ''
  const weight = target.weight && target.weight !== 1 ? ` (weight ${target.weight})` : ''
  return `${base}${value}${weight}`
}

export function NewRunPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [conversationId, setConversationId] = useState<string | null>(null)
  const [chat, setChat] = useState<ChatMessage[]>([])
  const [context, setContext] = useState<ConversationContext | null>(null)
  const [state, setState] = useState<ConversationState | null>(null)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [canProceed, setCanProceed] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isInitializing, setIsInitializing] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const beginConversation = useCallback(async (previousId?: string | null) => {
    setIsInitializing(true)
    setError(null)

    if (previousId) {
      try {
        await abandonConversation(previousId)
      } catch {
        // Ignore abandonment failures – the conversation may already be closed.
      }
    }

    try {
      const response = await startConversation()
      setConversationId(response.conversation_id)
      setChat([
        {
          id: `assistant-${response.conversation_id}-${Date.now()}`,
          role: 'assistant',
          text: response.message,
        },
      ])
      setContext(response.context)
      setState(response.state)
      setSuggestions(response.suggestions ?? [])
      setCanProceed(response.can_proceed)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start conversation.'
      setError(message)
      setConversationId(null)
      setChat([])
    } finally {
      setIsInitializing(false)
      setInput('')
      inputRef.current?.focus()
    }
  }, [])

  useEffect(() => {
    void beginConversation()
  }, [beginConversation])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chat, isInitializing])

  const sendMessage = useCallback(
    async (text: string) => {
      if (!conversationId) return
      const trimmed = text.trim()
      if (!trimmed) return

      setInput('')
      setChat((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: 'user', text: trimmed },
      ])
      setIsSending(true)
      setError(null)

      try {
        const response = await sendConversationMessage(conversationId, { message: trimmed })
        setChat((prev) => [
          ...prev,
          {
            id: `assistant-${conversationId}-${Date.now()}`,
            role: 'assistant',
            text: response.message,
          },
        ])
        setContext(response.context)
        setState(response.state)
        setSuggestions(response.suggestions ?? [])
        setCanProceed(response.can_proceed)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to process message.'
        setError(message)
      } finally {
        setIsSending(false)
        inputRef.current?.focus()
      }
    },
    [conversationId]
  )

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      if (isInitializing || isSending || isConfirming) return
      await sendMessage(input)
    },
    [input, isConfirming, isInitializing, isSending, sendMessage]
  )

  const handleSuggestionClick = useCallback((suggestion: string) => {
    setInput(suggestion)
    inputRef.current?.focus()
  }, [])

  const handleConfirm = useCallback(async () => {
    if (!conversationId) return
    setIsConfirming(true)
    setError(null)

    try {
      const response = await confirmConversation(conversationId, { confirmed: true })
      setState('completed')
      setCanProceed(false)
      setChat((prev) => [
        ...prev,
        {
          id: `assistant-${conversationId}-confirmation-${Date.now()}`,
          role: 'assistant',
          text: response.message,
        },
      ])

      await queryClient.invalidateQueries({ queryKey: ['runs'] })
      navigate(`/runs/${response.run_id}`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to launch optimization.'
      setError(message)
    } finally {
      setIsConfirming(false)
    }
  }, [conversationId, navigate, queryClient])

  const handleRestart = useCallback(() => {
    if (isInitializing || isSending || isConfirming) return
    void beginConversation(conversationId)
  }, [beginConversation, conversationId, isConfirming, isInitializing, isSending])

  const stateLabel = useMemo(() => formatStateLabel(state), [state])

  const targetSummary = useMemo(() => {
    if (!context || !context.targets?.length) return []
    return context.targets.map(describeTarget)
  }, [context])

  const missingPieces = useMemo(() => {
    const result: string[] = []
    if (!context?.starting_molecule) result.push('Starting molecule')
    if (!context?.targets?.length) result.push('Optimization targets')
    if (context?.max_iterations == null) result.push('Max iterations')
    if (context?.batch_size == null) result.push('Batch size')
    return result
  }, [context])

  const clarifyList = useMemo(() => {
    if (!context?.needs_clarification?.length) return []
    return Array.from(new Set(context.needs_clarification))
  }, [context])

  const canLaunch =
    canProceed &&
    (state === 'confirmation' || state === 'completed') &&
    !isInitializing &&
    !isSending &&
    !isConfirming
  const inputDisabled = isInitializing || isConfirming || state === 'completed'

  return (
    <div className="new-run">
      <div className="new-run__header">
        <div>
          <h1>Guided Run Setup</h1>
          <p>Chat with the LIZARD agent to collect everything needed for a new optimization run.</p>
        </div>
        <button
          type="button"
          className="ghost"
          onClick={handleRestart}
          disabled={isInitializing || isSending || isConfirming}
        >
          Start over
        </button>
      </div>

      <div className="new-run__layout">
        <section className="conversation-panel">
          <div className="conversation-panel__messages">
            {chat.map((message) => (
              <div key={message.id} className={`conversation-message conversation-message--${message.role}`}>
                <div className="conversation-message__bubble">{message.text}</div>
              </div>
            ))}
            {isInitializing && (
              <div className="conversation-message conversation-message--assistant">
                <div className="conversation-message__bubble">Starting conversation…</div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {error && <div className="conversation-error">{error}</div>}

          <form className="conversation-input" onSubmit={handleSubmit}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={inputDisabled ? 'Conversation completed.' : 'Type your response here…'}
              rows={3}
              disabled={inputDisabled || isSending}
            />
            <div className="conversation-input__footer">
              <div className="conversation-suggestions">
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => handleSuggestionClick(suggestion)}
                    disabled={inputDisabled || isSending || isConfirming}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
              <button
                type="submit"
                className="primary"
                disabled={inputDisabled || isSending || !input.trim()}
              >
                {isSending ? 'Sending…' : 'Send'}
              </button>
            </div>
          </form>
        </section>

        <aside className="conversation-summary">
          <header className="conversation-summary__header">
            <span className="conversation-summary__status-label">{stateLabel}</span>
            {state === 'confirmation' && canProceed && <span className="conversation-summary__ready">Ready to launch</span>}
          </header>

          <div className="conversation-summary__section">
            <h2>Run draft</h2>
            <dl>
              <div>
                <dt>Starting molecule</dt>
                <dd>{context?.molecule_name || context?.starting_molecule || <em>Pending</em>}</dd>
              </div>
              <div>
                <dt>Targets</dt>
                <dd>
                  {targetSummary.length ? (
                    <ul>
                      {targetSummary.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <em>Pending</em>
                  )}
                </dd>
              </div>
              <div>
                <dt>Iterations</dt>
                <dd>{context?.max_iterations ?? <em>Pending</em>}</dd>
              </div>
              <div>
                <dt>Batch size</dt>
                <dd>{context?.batch_size ?? <em>Pending</em>}</dd>
              </div>
              {context?.notes && (
                <div>
                  <dt>Notes</dt>
                  <dd>{context.notes}</dd>
                </div>
              )}
            </dl>
          </div>

          {(missingPieces.length > 0 || clarifyList.length > 0) && (
            <div className="conversation-summary__section">
              <h3>Still needed</h3>
              {missingPieces.length > 0 && (
                <ul>
                  {missingPieces.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
              {clarifyList.length > 0 && (
                <p className="conversation-summary__hint">
                  Agent needs clarification on: {clarifyList.join(', ')}
                </p>
              )}
            </div>
          )}

          <div className="conversation-summary__actions">
            <button
              type="button"
              className="primary"
              onClick={handleConfirm}
              disabled={!canLaunch}
            >
              {isConfirming ? 'Launching…' : 'Launch optimization'}
            </button>
            <p className="conversation-summary__disclaimer">
              The run will use the prompt assembled from this conversation. You can revisit it in the run detail view once launched.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
