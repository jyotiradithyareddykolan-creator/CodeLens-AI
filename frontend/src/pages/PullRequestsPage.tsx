import React from 'react';
import { GitPullRequest } from 'lucide-react';

export const PullRequestsPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">Pull Request Intelligence</h2>
          <p className="text-sm text-slate-400 mt-1">
            Automated LangGraph PR analysis reports, bug detection, breaking changes, and cross-file ripple impact.
          </p>
        </div>
      </div>

      <div className="p-12 text-center rounded-xl bg-slate-900 border border-slate-800 space-y-4">
        <div className="w-12 h-12 mx-auto rounded-full bg-slate-800 flex items-center justify-center text-purple-400">
          <GitPullRequest className="w-6 h-6" />
        </div>
        <div className="max-w-md mx-auto space-y-1">
          <h3 className="text-base font-semibold text-slate-200">No Pull Requests Analyzed</h3>
          <p className="text-xs text-slate-400">
            Open or update a Pull Request on a connected repository to automatically trigger LangGraph analysis.
          </p>
        </div>
      </div>
    </div>
  );
};
