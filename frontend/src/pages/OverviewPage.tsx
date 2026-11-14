import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { repositoryApi } from '../services/api';
import { SystemStatusPanel } from '../components/overview/SystemStatusPanel';
import { useAuth } from '../hooks/useAuth';
import {
  GitBranch,
  FileCode,
  GitPullRequest,
  AlertTriangle,
  ShieldCheck,
} from 'lucide-react';

export const OverviewPage: React.FC = () => {
  const { isAuthenticated } = useAuth();
  const { data: repositories } = useQuery({
    queryKey: ['repositories'],
    queryFn: repositoryApi.getRepositories,
    enabled: isAuthenticated,
  });
  const signedIn = (value: number | undefined) => (isAuthenticated && value !== undefined ? String(value) : '—');
  const indexedFiles = repositories?.reduce((sum, r) => sum + r.total_files, 0);

  // Pull request analysis arrives in Phase 5; until then those tiles show "—".
  const stats = [
    { name: 'Connected Repositories', value: signedIn(repositories?.length), icon: GitBranch, color: 'text-blue-400' },
    { name: 'Indexed Source Files', value: signedIn(indexedFiles), icon: FileCode, color: 'text-emerald-400' },
    { name: 'Analyzed Pull Requests', value: '—', icon: GitPullRequest, color: 'text-purple-400' },
    { name: 'Potential Issues Detected', value: '—', icon: AlertTriangle, color: 'text-amber-400' },
  ];

  return (
    <div className="space-y-6">
      {/* Welcome Banner */}
      <div className="p-6 rounded-xl bg-gradient-to-r from-blue-950/60 to-slate-900 border border-blue-900/30 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">CodeLens AI Dashboard</h2>
          <p className="text-sm text-slate-400 mt-1">
            Understand your codebase architecture, retrieve grounded citations, and catch PR regressions before they ship.
          </p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-800/40 text-emerald-400 text-xs">
          <ShieldCheck className="w-4 h-4" />
          <span>Read-Only Engine Active</span>
        </div>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.name}
              className="p-5 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-between"
            >
              <div>
                <p className="text-xs text-slate-400 font-medium">{stat.name}</p>
                <p className="text-2xl font-bold text-white mt-1">{stat.value}</p>
              </div>
              <div className={`p-3 rounded-lg bg-slate-800/80 ${stat.color}`}>
                <Icon className="w-5 h-5" />
              </div>
            </div>
          );
        })}
      </div>

      {/* Live backend dependency status */}
      <SystemStatusPanel />
    </div>
  );
};
