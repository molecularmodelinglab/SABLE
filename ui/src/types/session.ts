export interface Session {
  id: string;
  user_id: string;
  username: string;
  email?: string;
  created_at: string;
  last_activity: string;
  expires_at: string;
  ip_address?: string;
  user_agent?: string;
  metadata?: Record<string, any>;
  is_active: boolean;
}

export interface LoginRequest {
  username?: string;
  user_id?: string;
  email?: string;
  metadata?: Record<string, any>;
}

export interface LoginResponse {
  session_id: string;
  token: string;
  user_id: string;
  username: string;
  expires_at: string;
}

export interface SessionInfo {
  session: Session;
  active_sessions: number;
}
