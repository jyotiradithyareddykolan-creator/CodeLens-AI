import React, { useState } from 'react';
import { ChevronDown, ChevronRight, GitBranch, Globe, Lock, Trash2 } from 'lucide-react';
import { Repository } from '../../types';
import { IndexStatusBadge } from './IndexStatusBadge';
import { IndexingControls } from './IndexingControls';
import { RepositoryIndexPanel } from './RepositoryIndexPanel';
import { useIndexStatus } from './useIndexStatus';

interface Props {
  repo: Repository;
  onDisconnect: (repo: Repository) => void;
  disconnecting: boolean;
}

/** One connected repository with live indexing status, controls and details. */
export const RepositoryRow: React.FC<Props> = ({ repo, onDisconnect, disconnecting }) => {
  const [expanded, setExpanded] = useState(false);
  const { data: status, isError } = useIndexStatus(repo.id);
  const files = status?.total_files ?? repo.total_files;
  const chunks = status?.total_chunks ?? repo.total_chunks;
  const commit = status?.last_indexed_commit ?? repo.last_indexed_commit;

  return (
    <>
      <tr className="hover:bg-slate-800/40 transition" data-testid={`repo-row-${repo.id}`}>
        <td className="py-3 px-4">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Hide' : 'Show'} indexing details for ${repo.full_name}`}
            className="font-semibold text-slate-100 flex items-center gap-2 hover:text-blue-300"
          >
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            <GitBranch className="w-4 h-4 text-blue-400" />
            <span>{repo.full_name}</span>
          </button>
          {repo.description && <p className="text-[11px] text-slate-400 truncate max-w-sm mt-0.5 ml-6">{repo.description}</p>}
        </td>
        <td className="py-3 px-4">
          <span className="font-mono bg-slate-800 px-2 py-0.5 rounded text-[11px] text-slate-300 border border-slate-700">
            {repo.default_branch}
          </span>
        </td>
        <td className="py-3 px-4">
          {repo.is_private ? (
            <span className="flex items-center gap-1 text-amber-400"><Lock className="w-3 h-3" /> Private</span>
          ) : (
            <span className="flex items-center gap-1 text-slate-400"><Globe className="w-3 h-3" /> Public</span>
          )}
        </td>
        <td className="py-3 px-4">
          <IndexStatusBadge status={status} fallbackStatus={repo.indexing_status} />
          {isError && <p className="text-[11px] text-amber-400 mt-1">Status unavailable</p>}
        </td>
        <td className="py-3 px-4 font-mono text-slate-400">
          <div>{files} files / {chunks} chunks</div>
          {commit && <div className="text-[10px] text-slate-500">@ {commit.slice(0, 10)}</div>}
        </td>
        <td className="py-3 px-4">
          <div className="flex items-start justify-end gap-2">
            <IndexingControls repoId={repo.id} status={status} />
            <button
              type="button"
              onClick={() => onDisconnect(repo)}
              disabled={disconnecting}
              title="Disconnect repository"
              className="p-1.5 rounded-md hover:bg-rose-950/40 text-slate-400 hover:text-rose-400 transition"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="p-0">
            <RepositoryIndexPanel repoId={repo.id} status={status} />
          </td>
        </tr>
      )}
    </>
  );
};
