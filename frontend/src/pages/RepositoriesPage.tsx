import React, { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  FolderSearch,
  ShieldCheck,
  Lock,
  Globe,
  Loader2,
  Database,
  X,
  Github,
  ExternalLink,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react';
import { apiErrorMessage, githubApi, repositoryApi } from '../services/api';
import { startGitHubSignIn, useAuth } from '../hooks/useAuth';
import { Repository } from '../types';
import { RepositoryRow } from '../components/indexing/RepositoryRow';

const ConnectRepositoryModal: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const queryClient = useQueryClient();
  const [installationId, setInstallationId] = useState<string>('');

  const installationsQuery = useQuery({
    queryKey: ['github-installations'],
    queryFn: githubApi.getInstallations,
  });
  const installations = installationsQuery.data?.installations ?? [];

  useEffect(() => {
    if (!installationId && installations.length > 0) {
      setInstallationId(installations[0].id);
    }
  }, [installationId, installations]);

  const reposQuery = useQuery({
    queryKey: ['github-installation-repositories', installationId],
    queryFn: () => githubApi.getInstallationRepositories(installationId),
    enabled: Boolean(installationId),
  });

  const connectMutation = useMutation({
    mutationFn: repositoryApi.connectRepository,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repositories'] });
      queryClient.invalidateQueries({ queryKey: ['github-installation-repositories', installationId] });
    },
  });

  const installUrl = installationsQuery.data?.install_url;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-xl bg-slate-900 border border-slate-800 shadow-2xl p-6 space-y-5">
        <div className="flex justify-between items-center pb-2 border-b border-slate-800">
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            <Database className="w-4 h-4 text-blue-400" />
            <span>Connect GitHub Repository</span>
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200" title="Close">
            <X className="w-4 h-4" />
          </button>
        </div>

        {installationsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin text-blue-500" /> Loading GitHub App installations...
          </div>
        ) : installationsQuery.isError ? (
          <p className="text-xs text-amber-400">{apiErrorMessage(installationsQuery.error)}</p>
        ) : installations.length === 0 ? (
          <div className="space-y-3 text-xs text-slate-300">
            <p>
              No GitHub App installations are linked to your account yet. Install the CodeLens GitHub
              App on your account or organization and choose which repositories it may read.
            </p>
            {installUrl ? (
              <a
                href={installUrl}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition"
              >
                <Github className="w-3.5 h-3.5" /> Install GitHub App <ExternalLink className="w-3 h-3" />
              </a>
            ) : (
              <p className="text-slate-500">
                The install link is unavailable because GITHUB_APP_SLUG is not configured on the backend.
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-4 text-xs">
            <div className="space-y-1">
              <label htmlFor="installation" className="text-slate-300 font-medium">
                GitHub account
              </label>
              <select
                id="installation"
                value={installationId}
                onChange={(e) => setInstallationId(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500"
              >
                {installations.map((inst) => (
                  <option key={inst.id} value={inst.id}>
                    {inst.account_login} ({inst.account_type})
                  </option>
                ))}
              </select>
            </div>

            <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-800 divide-y divide-slate-800/60">
              {reposQuery.isLoading ? (
                <div className="p-4 flex items-center gap-2 text-slate-400">
                  <Loader2 className="w-4 h-4 animate-spin text-blue-500" /> Loading repositories from GitHub...
                </div>
              ) : reposQuery.isError ? (
                <p className="p-4 text-amber-400">{apiErrorMessage(reposQuery.error)}</p>
              ) : (reposQuery.data ?? []).length === 0 ? (
                <p className="p-4 text-slate-400">
                  This installation has no repositories granted. Update the App's repository access on GitHub.
                </p>
              ) : (
                (reposQuery.data ?? []).map((repo) => {
                  const pending =
                    connectMutation.isPending &&
                    connectMutation.variables?.github_repo_id === repo.github_repo_id;
                  return (
                    <div key={repo.github_repo_id} className="p-3 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-slate-100 flex items-center gap-1.5">
                          {repo.is_private ? (
                            <Lock className="w-3 h-3 text-amber-400" />
                          ) : (
                            <Globe className="w-3 h-3 text-slate-400" />
                          )}
                          <span className="truncate">{repo.full_name}</span>
                        </div>
                        {repo.description && (
                          <p className="text-[11px] text-slate-400 truncate">{repo.description}</p>
                        )}
                      </div>
                      {repo.connected_repository_id ? (
                        <span className="flex items-center gap-1 text-emerald-400 shrink-0">
                          <CheckCircle2 className="w-3.5 h-3.5" /> Connected
                        </span>
                      ) : (
                        <button
                          onClick={() =>
                            connectMutation.mutate({
                              installation_id: installationId,
                              github_repo_id: repo.github_repo_id,
                            })
                          }
                          disabled={connectMutation.isPending}
                          className="shrink-0 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition flex items-center gap-1.5 disabled:opacity-60"
                        >
                          {pending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                          <span>Connect</span>
                        </button>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            {connectMutation.isError && (
              <p className="text-amber-400 flex items-center gap-1.5">
                <AlertCircle className="w-3.5 h-3.5" /> {apiErrorMessage(connectMutation.error)}
              </p>
            )}
            {installUrl && (
              <a href={installUrl} className="inline-flex items-center gap-1 text-blue-400 hover:text-blue-300">
                Install on another account or change repository access <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export const RepositoriesPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { isAuthenticated, isLoading: authLoading } = useAuth();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  const { data: repositories, isLoading, isError, error } = useQuery({
    queryKey: ['repositories'],
    queryFn: repositoryApi.getRepositories,
    enabled: isAuthenticated,
  });

  const disconnectMutation = useMutation({
    mutationFn: repositoryApi.disconnectRepository,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repositories'] });
      queryClient.invalidateQueries({ queryKey: ['github-installation-repositories'] });
    },
  });

  const handleSignIn = async () => {
    setSignInError(null);
    try {
      await startGitHubSignIn();
    } catch (err) {
      setSignInError(apiErrorMessage(err, 'Sign-in is unavailable.'));
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">Connected Repositories</h2>
          <p className="text-sm text-slate-400 mt-1">
            Connect GitHub repositories, index their source code, and track indexing progress.
          </p>
        </div>
        {isAuthenticated && (
          <button
            onClick={() => setIsModalOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs transition shadow-lg shadow-blue-500/20"
          >
            <Plus className="w-4 h-4" />
            <span>Connect Repository</span>
          </button>
        )}
      </div>

      {/* Safety Notice */}
      <div className="p-4 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-between text-xs text-slate-400">
        <div className="flex items-center gap-2.5">
          <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>
            Connected repositories are accessed in <strong>read-only mode</strong>. CodeLens AI will never modify branch code, commits, or merge pull requests.
          </span>
        </div>
      </div>

      {authLoading || (isAuthenticated && isLoading) ? (
        <div className="p-12 text-center rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-center gap-2 text-slate-400 text-sm">
          <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
          <span>Loading repositories...</span>
        </div>
      ) : !isAuthenticated ? (
        <div className="p-12 text-center rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <div className="w-12 h-12 mx-auto rounded-full bg-slate-800 flex items-center justify-center text-slate-400">
            <Github className="w-6 h-6" />
          </div>
          <div className="max-w-md mx-auto space-y-1">
            <h3 className="text-base font-semibold text-slate-200">Sign in to manage repositories</h3>
            <p className="text-xs text-slate-400">
              Sign in with GitHub to see the repositories your CodeLens GitHub App installations can read.
            </p>
          </div>
          <button
            onClick={handleSignIn}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition inline-flex items-center gap-2"
          >
            <Github className="w-4 h-4" />
            <span>Sign in with GitHub</span>
          </button>
          {signInError && <p className="text-xs text-amber-400">{signInError}</p>}
        </div>
      ) : isError ? (
        <div className="p-6 rounded-xl bg-slate-900 border border-slate-800 text-xs text-amber-400 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {apiErrorMessage(error)}
        </div>
      ) : repositories && repositories.length > 0 ? (
        <div className="rounded-xl bg-slate-900 border border-slate-800 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/60 border-b border-slate-800 text-slate-400 font-medium">
                <tr>
                  <th className="py-3 px-4">Repository</th>
                  <th className="py-3 px-4">Default Branch</th>
                  <th className="py-3 px-4">Visibility</th>
                  <th className="py-3 px-4">Indexing State</th>
                  <th className="py-3 px-4">Indexed Files</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {repositories.map((repo: Repository) => (
                  <RepositoryRow
                    key={repo.id}
                    repo={repo}
                    disconnecting={disconnectMutation.isPending}
                    onDisconnect={(r) => {
                      if (window.confirm(`Disconnect ${r.full_name}? Indexed data in CodeLens will be deleted. The GitHub repository is not affected.`)) {
                        disconnectMutation.mutate(r.id);
                      }
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="p-12 text-center rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <div className="w-12 h-12 mx-auto rounded-full bg-slate-800 flex items-center justify-center text-slate-400">
            <FolderSearch className="w-6 h-6" />
          </div>
          <div className="max-w-md mx-auto space-y-1">
            <h3 className="text-base font-semibold text-slate-200">No Repositories Connected Yet</h3>
            <p className="text-xs text-slate-400">
              Connect a GitHub repository to begin AST indexing, RAG codebase chat, and automated Pull Request analysis.
            </p>
          </div>
          <button
            onClick={() => setIsModalOpen(true)}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            <span>Connect First Repository</span>
          </button>
        </div>
      )}

      {isModalOpen && isAuthenticated && <ConnectRepositoryModal onClose={() => setIsModalOpen(false)} />}
    </div>
  );
};
