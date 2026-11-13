import React from 'react';
import { ShieldCheck } from 'lucide-react';

export const SettingsPage: React.FC = () => {
  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="text-xl font-bold text-white tracking-tight">Platform Settings</h2>
        <p className="text-sm text-slate-400 mt-1">
          Configure GitHub App authentication, credentials, and indexing parameters.
        </p>
      </div>

      <div className="p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
        <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <span>Security & Permissions Guard</span>
        </h3>
        <p className="text-xs text-slate-400">
          CodeLens AI operates in strict <strong>read-only</strong> mode on connected repositories. Automated commits, branch changes, and automated PR merging are strictly disabled.
        </p>
      </div>
    </div>
  );
};
