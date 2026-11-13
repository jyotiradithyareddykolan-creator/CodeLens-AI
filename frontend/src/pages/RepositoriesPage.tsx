import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  GitBranch,
  Plus,
  Trash2,
  FolderSearch,
  ShieldCheck,
  Lock,
  Globe,
  Loader2,
  Database,
  X,
} from 'lucide-react';
import { repositoryApi, ConnectRepositoryInput } from '../services/api';
import { Repository } from '../types';

export const RepositoriesPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [formData, setFormData] = useState<ConnectRepositoryInput>({
    github_repo_id: 0,
    name: '',
    owner: '',
    full_name: '',
    default_branch: 'main',
    is_private: false,
    description: '',
  });

  const { data: repositories, isLoading } = useQuery({
    queryKey: ['repositories'],
    queryFn: repositoryApi.getRepositories,
  });

  const connectMutation = useMutation({
    mutationFn: repositoryApi.connectRepository,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repositories'] });
      setIsModalOpen(false);
      setFormData({
        github_repo_id: 0,
        name: '',
        owner: '',
        full_name: '',
        default_branch: 'main',
        is_private: false,
        description: '',
      });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: repositoryApi.disconnectRepository,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repositories'] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.owner) return;
    const full_name = `${formData.owner}/${formData.name}`;
    connectMutation.mutate({ ...formData, full_name });
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">Connected Repositories</h2>
          <p className="text-sm text-slate-400 mt-1">
            Manage synchronized GitHub repositories, track AST indexing state, and review symbols.
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs transition shadow-lg shadow-blue-500/20"
        >
          <Plus className="w-4 h-4" />
          <span>Connect Repository</span>
        </button>
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

      {/* Repositories Table / List */}
      {isLoading ? (
        <div className="p-12 text-center rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-center gap-2 text-slate-400 text-sm">
          <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
          <span>Loading repositories...</span>
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
                  <tr key={repo.id} className="hover:bg-slate-800/40 transition">
                    <td className="py-3 px-4">
                      <div className="font-semibold text-slate-100 flex items-center gap-2">
                        <GitBranch className="w-4 h-4 text-blue-400" />
                        <span>{repo.full_name}</span>
                      </div>
                      {repo.description && (
                        <p className="text-[11px] text-slate-400 truncate max-w-sm mt-0.5">
                          {repo.description}
                        </p>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span className="font-mono bg-slate-800 px-2 py-0.5 rounded text-[11px] text-slate-300 border border-slate-700">
                        {repo.default_branch}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      {repo.is_private ? (
                        <span className="flex items-center gap-1 text-amber-400">
                          <Lock className="w-3 h-3" /> Private
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-slate-400">
                          <Globe className="w-3 h-3" /> Public
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                          repo.is_indexed
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/60'
                            : 'bg-slate-800 text-slate-400 border border-slate-700'
                        }`}
                      >
                        {repo.indexing_status.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-400">
                      {repo.total_files} files / {repo.total_chunks} chunks
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => disconnectMutation.mutate(repo.id)}
                        disabled={disconnectMutation.isPending}
                        title="Disconnect repository"
                        className="p-1.5 rounded-md hover:bg-rose-950/40 text-slate-400 hover:text-rose-400 transition"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
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

      {/* Connect Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-xl bg-slate-900 border border-slate-800 shadow-2xl p-6 space-y-5 animate-in fade-in zoom-in-95">
            <div className="flex justify-between items-center pb-2 border-b border-slate-800">
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                <Database className="w-4 h-4 text-blue-400" />
                <span>Connect GitHub Repository</span>
              </h3>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-slate-400 hover:text-slate-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4 text-xs">
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Repository Owner / Org</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. facebook, octocat, or my-org"
                  value={formData.owner}
                  onChange={(e) => setFormData({ ...formData, owner: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500 transition"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Repository Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. react, ai_code_analyzer"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500 transition"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Default Branch</label>
                <input
                  type="text"
                  required
                  placeholder="main"
                  value={formData.default_branch}
                  onChange={(e) => setFormData({ ...formData, default_branch: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500 transition"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Description (Optional)</label>
                <textarea
                  rows={2}
                  placeholder="Brief description of the service"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500 transition"
                />
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="is_private"
                  checked={formData.is_private}
                  onChange={(e) => setFormData({ ...formData, is_private: e.target.checked })}
                  className="rounded border-slate-800 text-blue-600 focus:ring-0 bg-slate-950"
                />
                <label htmlFor="is_private" className="text-slate-300">
                  Private Repository
                </label>
              </div>

              <div className="pt-2 flex justify-between items-center">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setIsModalOpen(false)}
                    className="px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={connectMutation.isPending}
                    className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition flex items-center gap-1.5"
                  >
                    {connectMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    <span>Connect</span>
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
