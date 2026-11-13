import React from 'react';
import { Network } from 'lucide-react';

export const ImpactPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white tracking-tight">Cross-File Impact Analysis</h2>
        <p className="text-sm text-slate-400 mt-1">
          Interactive AST dependency graph mapping CALLS, IMPORTS, DEFINES, and INHERITS relationships.
        </p>
      </div>

      <div className="h-[calc(100vh-14rem)] rounded-xl bg-slate-900 border border-slate-800 flex flex-col items-center justify-center text-center p-6 space-y-4">
        <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center text-blue-400">
          <Network className="w-6 h-6" />
        </div>
        <div className="max-w-md space-y-1">
          <h3 className="text-base font-semibold text-slate-200">Interactive Dependency Canvas</h3>
          <p className="text-xs text-slate-400">
            Select an indexed repository to inspect symbol call trees, callers, and potential ripple regressions.
          </p>
        </div>
      </div>
    </div>
  );
};
