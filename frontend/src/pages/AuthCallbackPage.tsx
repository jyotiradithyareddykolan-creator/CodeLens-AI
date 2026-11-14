import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2 } from 'lucide-react';
import { sessionToken } from '../services/api';

const ERROR_MESSAGES: Record<string, string> = {
  access_denied: 'GitHub authorization was cancelled.',
  invalid_state: 'The sign-in request expired or was not started from CodeLens. Please try again.',
  missing_code: 'GitHub did not return an authorization code. Please try again.',
  token_exchange_failed: 'GitHub rejected the sign-in. Check the GitHub App client credentials.',
  profile_failed: 'Could not read your GitHub profile. Please try again.',
  github_unavailable: 'GitHub could not be reached. Please try again shortly.',
  account_disabled: 'This CodeLens account is disabled.',
};

/**
 * Landing page for the OAuth redirect. The backend puts the session token (or an
 * error code) in the URL fragment, which is never sent to any server.
 */
export const AuthCallbackPage: React.FC = () => {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // StrictMode runs effects twice in development; the fragment is consumed on the first run.
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    // Remove the token from the address bar and browser history immediately.
    window.history.replaceState(null, '', window.location.pathname);

    const token = params.get('token');
    const errorCode = params.get('error');
    if (token) {
      sessionToken.set(token);
      navigate('/repositories', { replace: true });
    } else {
      setError(ERROR_MESSAGES[errorCode ?? ''] ?? 'Sign-in failed. Please try again.');
    }
  }, [navigate]);

  if (!error) {
    return (
      <div className="p-12 flex items-center justify-center gap-2 text-sm text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
        <span>Completing sign-in...</span>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto mt-12 p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-3 text-sm">
      <div className="flex items-center gap-2 text-amber-400 font-semibold">
        <AlertCircle className="w-4 h-4" />
        <span>Sign-in did not complete</span>
      </div>
      <p className="text-slate-300">{error}</p>
      <Link to="/repositories" className="inline-block text-blue-400 hover:text-blue-300">
        Back to repositories
      </Link>
    </div>
  );
};
