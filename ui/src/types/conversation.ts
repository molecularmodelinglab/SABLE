export type ConversationState =
  | 'greeting'
  | 'collecting_molecule'
  | 'collecting_targets'
  | 'collecting_parameters'
  | 'confirmation'
  | 'completed'
  | 'abandoned'

export type OptimizationMode = 'maximize' | 'minimize' | 'match'

export interface TargetProperty {
  name: string
  mode: OptimizationMode
  target_value?: number | null
  weight: number
}

export interface ConversationContext {
  starting_molecule?: string | null
  molecule_source?: string | null
  molecule_name?: string | null
  targets: TargetProperty[]
  max_iterations?: number | null
  batch_size?: number | null
  enumeration_size?: number | null
  notes?: string | null
  protein_target?: Record<string, unknown> | null
  needs_clarification: string[]
  clarifications_asked: string[]
  full_prompt: string
}

export interface ConversationStartRequest {
  initial_message?: string | null
}

export interface ConversationMessageRequest {
  message: string
}

export interface ConversationResponse {
  conversation_id: string
  state: ConversationState
  message: string
  context: ConversationContext
  suggestions: string[]
  can_proceed: boolean
}

export interface ConversationConfirmRequest {
  confirmed: boolean
  changes?: string | null
}

export interface ConversationCreateRunResponse {
  run_id: string
  message: string
}

export interface ConversationListResponse {
  conversations: Array<{
    id: string
    state: ConversationState
    created_at: string | null
    updated_at: string | null
    run_id?: string | null
  }>
  total: number
}
