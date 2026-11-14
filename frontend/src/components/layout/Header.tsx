import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiErrorMessage, authApi, healthApi } from '../../services/api';
import { startGitHubSignIn, useAuth } from '../../hooks/useAuth';
import { Activity, Github, CheckCircle2, AlertCircle, LogOut } from 'lucide-react';

export const Header: React.FC = () => {
  const { user, isAuthenticated, isLoading } = useAuth();
  const [signingIn, setSigningIn] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  const handleSignIn = async () => {
    setSignInError(null);
    setSigningIn(true);
    try {
      await startGitHubSignIn();
    } catch (err) {
      setSigningIn(false);
      setSignInError(apiErrorMessage(err, 'Sign-in is unavailable.'));
    }
  };

  const { isSuccess, isError } = useQuery({
    queryKey: ['system-health'],
    queryFn: healthApi.getHealth,
    refetchInterval: 30000,
  });

  return (
    <header className="h-16 border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 flex items-center justify-between sticky top-0 z-30">
      <div className="flex items-center gap-3">
        <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
          v0.1.0-alpha
        </span>
      </div>

      <div className="flex items-center gap-4">
        {/* Backend Connectivity Status */}
        <div className="flex items-center gap-2 text-xs px-2.5 py-1 rounded-full bg-slate-800/80 border border-slate-700">
          <Activity className="w-3.5 h-3.5 text-blue-400 animate-pulse" />
          <span className="text-slate-400">Backend:</span>
          {isSuccess && (
            <span className="flex items-center gap-1 text-emerald-400 font-medium">
              <CheckCircle2 className="w-3 h-3" /> Online
            </span>
          )}
          {isError && (
            <span className="flex items-center gap-1 text-amber-400 font-medium">
              <AlertCircle className="w-3 h-3" /> Standalone
            </span>
          )}
        </div>

        {/* GitHub Account */}
        <div className="flex items-center gap-2 pl-2 border-l border-slate-800">
          {isAuthenticated && user ? (
            <>
              <span className="flex items-center gap-2 text-xs text-slate-300">
                {user.avatar_url ? (
                  <img src={user.avatar_url} alt="" className="w-6 h-6 rounded-full" />
                ) : (
                  <Github className="w-3.5 h-3.5" />
                )}
                <span className="font-medium">{user.username}</span>
              </span>
              <button
                onClick={() => authApi.logout()}
                title="Sign out"
                className="p-1.5 rounded-md text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            </>
          ) : (
            <button
              onClick={handleSignIn}
              disabled={isLoading || signingIn}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition disabled:opacity-60"
            >
              <Github className="w-3.5 h-3.5" />
              <span>{signingIn ? 'Redirecting...' : 'Sign in with GitHub'}</span>
            </button>
          )}
        </div>
        {signInError && <span className="text-[11px] text-amber-400">{signInError}</span>}
      </div>
    </header>
  );
};
