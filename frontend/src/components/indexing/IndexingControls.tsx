import React from 'react';
import { Loader2, Play, RefreshCw, RotateCcw } from 'lucide-react';
import { apiErrorMessage } from '../../services/api';
import { IndexingStatus } from '../../types';
import { isActive, useIndexingActions } from './useIndexStatus';

interface Props {
  repoId: string;
  status?: IndexingStatus;
}

const button =
  'inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium transition disabled:opacity-50 disabled:cursor-not-allowed';

/** Must match RESEND_AFTER_SECONDS in backend/app/indexing/jobs.py. */
export const RESEND_AFTER_MS = 30_000;

/**
 * A queued job whose task message may have been lost: not waiting for a scheduled
 * retry and not picked up by a worker for RESEND_AFTER_MS. Re-sending it is safe
 * (workers claim jobs atomically, the backend never creates a second active job).
 */
export function isResendable(status?: IndexingStatus, now: number = Date.now()): boolean {
  const job = status?.active_job;
  if (!job || job.status !== 'queued' || job.next_retry_at) return false;
  const lastChange = Date.parse(job.updated_at || job.created_at);
  return Number.isFinite(lastChange) && now - lastChange >= RESEND_AFTER_MS;
}

/** Start indexing, re-index, or retry the last failed job. */
export const IndexingControls: React.FC<Props> = ({ repoId, status }) => {
  const { start, reindex, retry } = useIndexingActions(repoId);
  const resendable = isResendable(status);
  const pending = start.isPending || reindex.isPending || retry.isPending;
  // Only the start/resend action is ever enabled while a job is active.
  const busy = (isActive(status) && !resendable) || pending;
  const reindexBusy = isActive(status) || pending;
  const startLabel = resendable ? 'Resend' : status?.is_indexed ? 'Update' : 'Index';
  const failedJob = status?.latest_job?.status === 'failed' && !isActive(status) ? status.latest_job : null;
  const error = start.error || reindex.error || retry.error;

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1.5">
        {failedJob && (
          <button
            type="button"
            onClick={() => retry.mutate(failedJob.id)}
            disabled={busy}
            className={`${button} bg-amber-900/40 text-amber-300 hover:bg-amber-900/60 border border-amber-800/60`}
          >
            {retry.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
            Retry
          </button>
        )}
        {status?.is_indexed ? (
          <>
            <button
              type="button"
              onClick={() => start.mutate()}
              disabled={busy}
              title="Index only files changed since the last indexed commit"
              className={`${button} bg-blue-600 hover:bg-blue-500 text-white`}
            >
              {start.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
              {startLabel}
            </button>
            <button
              type="button"
              onClick={() => reindex.mutate()}
              disabled={reindexBusy}
              title="Re-process every file"
              className={`${button} bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700`}
            >
              {reindex.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              Reindex
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={busy}
            className={`${button} bg-blue-600 hover:bg-blue-500 text-white`}
          >
            {start.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {startLabel}
          </button>
        )}
      </div>
      {error && <p className="text-[11px] text-amber-400 max-w-xs text-right">{apiErrorMessage(error)}</p>}
    </div>
  );
};
