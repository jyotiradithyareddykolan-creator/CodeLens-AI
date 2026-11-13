import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  GitBranch,
  MessageSquareCode,
  GitPullRequest,
  Network,
  History,
  Settings,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';

const navigationItems = [
  { name: 'Overview', href: '/', icon: LayoutDashboard },
  { name: 'Repositories', href: '/repositories', icon: GitBranch },
  { name: 'Codebase Chat', href: '/chat', icon: MessageSquareCode },
  { name: 'Pull Requests', href: '/pull-requests', icon: GitPullRequest },
  { name: 'Impact Analysis', href: '/impact', icon: Network },
  { name: 'Review History', href: '/history', icon: History },
  { name: 'Settings', href: '/settings', icon: Settings },
];

export const Sidebar: React.FC = () => {
  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col justify-between h-screen sticky top-0">
      <div>
        {/* Brand Header */}
        <div className="h-16 flex items-center px-6 border-b border-slate-800 gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white shadow-lg shadow-blue-500/20 font-bold">
            <Sparkles className="w-4 h-4 text-blue-200" />
          </div>
          <div>
            <h1 className="font-bold text-slate-100 text-sm tracking-tight">CodeLens AI</h1>
            <p className="text-[10px] text-slate-400 font-mono">INTELLIGENCE PLATFORM</p>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="p-4 space-y-1">
          {navigationItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.name}
                to={item.href}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                    isActive
                      ? 'bg-blue-600/15 text-blue-400 border border-blue-500/30'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`
                }
              >
                <Icon className="w-4 h-4" />
                {item.name}
              </NavLink>
            );
          })}
        </nav>
      </div>

      {/* Safety Notice Footer */}
      <div className="p-4 border-t border-slate-800 bg-slate-950/40">
        <div className="flex items-start gap-2.5 text-xs text-slate-400">
          <ShieldAlert className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-slate-300">Read-Only Safe</p>
            <p className="text-[11px] text-slate-500 leading-snug">Zero repository mutation or auto-commit behavior.</p>
          </div>
        </div>
      </div>
    </aside>
  );
};
