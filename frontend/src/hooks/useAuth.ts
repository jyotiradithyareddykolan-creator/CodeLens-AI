import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AUTH_CHANGED_EVENT, authApi, sessionToken } from '../services/api';

export const AUTH_QUERY_KEY = ['auth', 'me'];

/** Current session state. Re-evaluates when the token is set or cleared. */
export function useAuth() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const onChange = () => {
      queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
      // Data fetched under a different identity must not be shown.
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' && q.queryKey[0] !== 'system-health' });
    };
    window.addEventListener(AUTH_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, onChange);
  }, [queryClient]);

  const query = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: authApi.getMe,
    enabled: Boolean(sessionToken.get()),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const hasToken = Boolean(sessionToken.get());
  return {
    user: query.data?.authenticated ? query.data.user : null,
    isAuthenticated: hasToken && Boolean(query.data?.authenticated),
    isLoading: hasToken && query.isLoading,
  };
}

/** Navigate the browser to the backend sign-in endpoint (sets the OAuth state cookie). */
export async function startGitHubSignIn(): Promise<void> {
  const { url } = await authApi.getLoginUrl();
  window.location.assign(url);
}
