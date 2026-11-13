import React, { useState } from 'react';
import { MessageSquareCode, Send, Sparkles } from 'lucide-react';

export const ChatPage: React.FC = () => {
  const [input, setInput] = useState('');

  return (
    <div className="h-[calc(100vh-8rem)] flex flex-col rounded-xl bg-slate-900 border border-slate-800 overflow-hidden">
      {/* Chat Header */}
      <div className="p-4 border-b border-slate-800 bg-slate-950/40 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MessageSquareCode className="w-5 h-5 text-blue-400" />
          <h2 className="text-sm font-semibold text-white">Codebase Intelligence RAG</h2>
        </div>
        <div className="text-xs text-slate-400 font-mono">
          Select a repository to query
        </div>
      </div>

      {/* Messages Canvas */}
      <div className="flex-1 p-6 flex flex-col items-center justify-center text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
          <Sparkles className="w-6 h-6" />
        </div>
        <div className="max-w-md space-y-1">
          <h3 className="text-base font-semibold text-slate-200">Ask Any Codebase Question</h3>
          <p className="text-xs text-slate-400">
            Answers are grounded in syntax-aware AST chunks and verified against your codebase with file and line citations.
          </p>
        </div>
      </div>

      {/* Input Box */}
      <div className="p-4 border-t border-slate-800 bg-slate-950/60">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. Where is authentication handled, and which functions call verify_password?"
            className="flex-1 bg-slate-900 border border-slate-800 rounded-lg px-4 py-2.5 text-xs text-slate-200 focus:outline-none focus:border-blue-500 transition"
          />
          <button className="px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium flex items-center gap-2 transition">
            <Send className="w-3.5 h-3.5" />
            <span>Ask</span>
          </button>
        </div>
      </div>
    </div>
  );
};
