import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { healthApi } from '../../services/api';
import { Activity, Github, CheckCircle2, AlertCircle } from 'lucide-react';

export const Header: React.FC = () => {
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

        {/* GitHub App Account Pill */}
        <div className="flex items-center gap-2 pl-2 border-l border-slate-800">
          <button className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition">
            <Github className="w-3.5 h-3.5" />
            <span>GitHub Connect</span>
          </button>
        </div>
      </div>
    </header>
  );
};
