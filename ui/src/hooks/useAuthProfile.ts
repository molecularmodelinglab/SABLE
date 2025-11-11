import { useQuery, UseQueryResult, UseQueryOptions } from '@tanstack/react-query'
import { getAccessToken, getAuthProfile } from '../api'
import type { AuthProfile } from '../types/session'

type AuthProfileQueryOptions = Omit<
  UseQueryOptions<AuthProfile, Error, AuthProfile, ['auth', 'profile']>,
  'queryKey' | 'queryFn' | 'enabled'
>

export function useAuthProfile(options?: AuthProfileQueryOptions): UseQueryResult<AuthProfile, Error> {
  const token = getAccessToken()

  return useQuery<AuthProfile, Error, AuthProfile, ['auth', 'profile']>({
    queryKey: ['auth', 'profile'],
    queryFn: getAuthProfile,
    enabled: Boolean(token),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    ...options,
  })
}
