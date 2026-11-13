import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { healthApi } from '../services/api';
import {
  GitBranch,
  FileCode,
  GitPullRequest,
  AlertTriangle,
  Layers,
  Database,
  Cpu,
  ShieldCheck,
} from 'lucide-react';

export const OverviewPage: React.FC = () => {
  const { data: health, isLoading } = useQuery({
    queryKey: ['system-health'],
    queryFn: healthApi.getHealth,
  });

  const stats = [
    { name: 'Connected Repositories', value: '0', icon: GitBranch, color: 'text-blue-400' },
    { name: 'Indexed Source Files', value: '0', icon: FileCode, color: 'text-emerald-400' },
    { name: 'Analyzed Pull Requests', value: '0', icon: GitPullRequest, color: 'text-purple-400' },
    { name: 'Potential Issues Detected', value: '0', icon: AlertTriangle, color: 'text-amber-400' },
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

      {/* Platform Health & Backing Services */}
      <div className="p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
        <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <Layers className="w-4 h-4 text-blue-400" />
          <span>Core System Architecture Status</span>
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80 flex items-center gap-3">
            <Cpu className="w-5 h-5 text-blue-400" />
            <div>
              <p className="font-medium text-slate-300">FastAPI Gateway</p>
              <p className="text-slate-500 font-mono">
                {isLoading ? 'Checking...' : health ? `v${health.version} (${health.status})` : 'Offline'}
              </p>
            </div>
          </div>

          <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80 flex items-center gap-3">
            <Database className="w-5 h-5 text-emerald-400" />
            <div>
              <p className="font-medium text-slate-300">PostgreSQL + pgvector</p>
              <p className="text-slate-500 font-mono">768-dim Vector Hybrid Engine</p>
            </div>
          </div>

          <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80 flex items-center gap-3">
            <Layers className="w-5 h-5 text-purple-400" />
            <div>
              <p className="font-medium text-slate-300">Redis & Celery Workers</p>
              <p className="text-slate-500 font-mono">Async Task Queue</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
