export interface AuthUser {
  id: string;
  email: string;
  username: string;
  auth_provider: string;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  last_login?: string | null;
}

export interface Session {
  id: string;
  user_id: string;
  created_at: string;
  last_activity: string;
  expires_at: string;
  ip_address?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
  session: Session;
}

export interface SessionListResponse {
  sessions: Session[];
  total: number;
}

export interface AuthProfile {
  user: AuthUser;
  session: Session | null;
}
