import React from 'react';
import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle, CircleDashed } from 'lucide-react';
import { IndexingJob, IndexingStatus } from '../../types';

const PHASE_LABELS: Record<string, string> = {
  starting: 'Starting',
  resolving: 'Resolving commit',
  discovering: 'Discovering files',
  processing: 'Parsing & chunking',
  embedding: 'Generating embeddings',
  saving: 'Saving',
  waiting_retry: 'Waiting to retry',
};

export function phaseLabel(phase?: string | null): string {
  return (phase && PHASE_LABELS[phase]) || 'Working';
}

export function formatTime(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

interface Props {
  status?: IndexingStatus;
  fallbackStatus?: string;
}

/** Repository indexing state, with live progress while a job is active. */
export const IndexStatusBadge: React.FC<Props> = ({ status, fallbackStatus = 'unindexed' }) => {
  const job: IndexingJob | null | undefined = status?.active_job;
  const base = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border';

  if (job?.status === 'running') {
    const pct = Math.max(0, Math.min(100, Math.round(job.progress)));
    return (
      <div className="space-y-1 min-w-[160px]" data-testid="index-status">
        <span className={`${base} bg-blue-950 text-blue-300 border-blue-800/60`}>
          <Loader2 className="w-3 h-3 animate-spin" /> {phaseLabel(job.phase)} · {pct}%
        </span>
        <div className="h-1.5 w-full rounded bg-slate-800 overflow-hidden" role="progressbar"
             aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (job?.status === 'queued') {
    const retrying = Boolean(job.next_retry_at);
    return (
      <span className={`${base} bg-slate-800 text-slate-300 border-slate-700`} data-testid="index-status"
            title={job.error_message ?? undefined}>
        <Clock className="w-3 h-3" />
        {retrying ? `Retrying at ${formatTime(job.next_retry_at)}` : 'Queued'}
      </span>
    );
  }

  const value = status?.indexing_status ?? fallbackStatus;
  const lastFailed = status?.latest_job?.status === 'failed';
  if (value === 'indexed') {
    return (
      <span className="inline-flex items-center gap-1.5" data-testid="index-status">
        <span className={`${base} bg-emerald-950 text-emerald-400 border-emerald-800/60`}>
          <CheckCircle2 className="w-3 h-3" /> Indexed
        </span>
        {lastFailed && (
          <span title={status?.latest_job?.error_message ?? 'Last run failed'} className="text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5" aria-label="Last indexing run failed" />
          </span>
        )}
      </span>
    );
  }
  if (value === 'failed') {
    return (
      <span className={`${base} bg-rose-950/60 text-rose-300 border-rose-800/60`} data-testid="index-status">
        <XCircle className="w-3 h-3" /> Failed
      </span>
    );
  }
  if (value === 'pending' || value === 'indexing') {
    return (
      <span className={`${base} bg-slate-800 text-slate-300 border-slate-700`} data-testid="index-status">
        <Clock className="w-3 h-3" /> Pending
      </span>
    );
  }
  return (
    <span className={`${base} bg-slate-800 text-slate-400 border-slate-700`} data-testid="index-status">
      <CircleDashed className="w-3 h-3" /> Not indexed
    </span>
  );
};
