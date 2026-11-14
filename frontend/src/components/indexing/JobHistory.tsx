import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, ChevronDown, ChevronRight, Loader2, RotateCcw } from 'lucide-react';
import { apiErrorMessage, indexingApi } from '../../services/api';
import { IndexingJob } from '../../types';
import { phaseLabel } from './IndexStatusBadge';
import { useIndexingActions } from './useIndexStatus';

export const JOB_HISTORY_PAGE_SIZE = 10;

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-slate-800 text-slate-300 border-slate-700',
  running: 'bg-blue-950 text-blue-300 border-blue-800/60',
  succeeded: 'bg-emerald-950 text-emerald-400 border-emerald-800/60',
  failed: 'bg-rose-950/60 text-rose-300 border-rose-800/60',
};

export function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** Human-readable duration between start and finish ("8s", "8m 16s", "1h 2m"). */
export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—';
  const ms = Date.parse(end) - Date.parse(start);
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

const shortSha = (sha?: string | null) => (sha ? sha.slice(0, 10) : '—');

const JobDetails: React.FC<{ job: IndexingJob; canRetry: boolean; onRetry: () => void; retrying: boolean }> = ({
  job, canRetry, onRetry, retrying,
}) => {
  const skipped = (job.stats?.skipped_by_reason ?? {}) as Record<string, number>;
  const stats = job.stats ?? {};
  return (
    <div className="px-3 pb-3 pt-1 space-y-1.5 text-[11px] text-slate-400" data-testid={`job-details-${job.id}`}>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>Commit: <code className="font-mono text-slate-300">{job.commit_sha ?? '—'}</code></span>
        {job.base_commit_sha && <span>Previous: <code className="font-mono">{shortSha(job.base_commit_sha)}</code></span>}
        <span>Trigger: {job.trigger}{job.force_full ? ' (full re-index)' : ''}</span>
        <span>Attempts: {job.attempts}</span>
        {(job.status === 'running' || job.status === 'queued') && <span>Phase: {phaseLabel(job.phase)}</span>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>Eligible files: {job.files_total}</span>
        <span>Changed: {job.files_changed}</span>
        <span>Processed: {job.files_processed}</span>
        <span>Indexed: {job.files_indexed}</span>
        <span>Skipped: {job.files_skipped}</span>
        <span>Failed downloads: {job.files_failed}</span>
        <span>Removed: {job.files_deleted}</span>
        <span>Chunks: {job.chunks_total}</span>
      </div>
      {Object.keys(skipped).length > 0 && (
        <div>Skipped by reason: {Object.entries(skipped).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ')}</div>
      )}
      {typeof stats.embedding_model === 'string' && (
        <div>
          Embeddings: {String(stats.embedding_model)} · {String(stats.embedding_dimensions)} dims ·{' '}
          {String(stats.embedding_requests ?? 0)} API requests
          {typeof stats.token_estimate_ratio === 'number' && ` · actual/estimated tokens ${stats.token_estimate_ratio}`}
        </div>
      )}
      {typeof stats.embeddings === 'string' && <div className="text-amber-400">Embeddings {stats.embeddings}</div>}
      {job.error_message && (
        <div className="flex items-start gap-1.5 text-rose-300" role="alert">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> <span>{job.error_message}</span>
        </div>
      )}
      {job.status === 'failed' && (
        <button
          type="button"
          onClick={onRetry}
          disabled={!canRetry || retrying}
          title={canRetry ? 'Start a new job with the same settings' : 'Another indexing job is active'}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-amber-900/40 text-amber-300 hover:bg-amber-900/60 border border-amber-800/60 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {retrying ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />} Retry this job
        </button>
      )}
    </div>
  );
};

/** Previous indexing runs of a repository, newest first, with expandable details. */
export const JobHistory: React.FC<{ repoId: string; hasActiveJob: boolean; refreshKey?: string }> = ({
  repoId, hasActiveJob, refreshKey,
}) => {
  const [page, setPage] = useState(0);
  const [openJob, setOpenJob] = useState<string | null>(null);
  const { retry } = useIndexingActions(repoId);
  const jobs = useQuery({
    queryKey: ['index-jobs', repoId, page, refreshKey],
    // One extra row tells us whether an older page exists.
    queryFn: () => indexingApi.jobs(repoId, JOB_HISTORY_PAGE_SIZE + 1, page * JOB_HISTORY_PAGE_SIZE),
    refetchInterval: hasActiveJob ? 5000 : false,
  });
  const rows = (jobs.data ?? []).slice(0, JOB_HISTORY_PAGE_SIZE);
  const hasOlder = (jobs.data ?? []).length > JOB_HISTORY_PAGE_SIZE;

  return (
    <section className="space-y-2" aria-label="Indexing history">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold text-slate-300">Indexing history</h4>
        <div className="flex items-center gap-2 text-[11px]">
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-2 py-0.5 rounded border border-slate-700 text-slate-300 disabled:opacity-40"
          >
            Newer
          </button>
          <span className="text-slate-500">Page {page + 1}</span>
          <button
            type="button"
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasOlder}
            className="px-2 py-0.5 rounded border border-slate-700 text-slate-300 disabled:opacity-40"
          >
            Older
          </button>
        </div>
      </div>

      {jobs.isLoading ? (
        <div className="p-3 text-xs text-slate-400 flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading history...
        </div>
      ) : jobs.isError ? (
        <p className="p-3 text-xs text-amber-400">{apiErrorMessage(jobs.error, 'Could not load indexing history.')}</p>
      ) : rows.length === 0 ? (
        <p className="p-3 text-xs text-slate-400">No indexing runs yet.</p>
      ) : (
        <div className="rounded-lg border border-slate-800 overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-slate-950/60 text-slate-500">
              <tr>
                <th className="py-1.5 px-3">Status</th>
                <th className="py-1.5 px-3">Commit</th>
                <th className="py-1.5 px-3">Started</th>
                <th className="py-1.5 px-3">Finished</th>
                <th className="py-1.5 px-3">Duration</th>
                <th className="py-1.5 px-3">Files changed / eligible</th>
                <th className="py-1.5 px-3">Embeddings new / reused</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-300">
              {rows.map((job) => {
                const open = openJob === job.id;
                return (
                  <React.Fragment key={job.id}>
                    <tr className="hover:bg-slate-800/40">
                      <td className="py-1.5 px-3">
                        <button
                          type="button"
                          onClick={() => setOpenJob(open ? null : job.id)}
                          aria-expanded={open}
                          aria-label={`${open ? 'Hide' : 'Show'} details of job ${job.id}`}
                          className="inline-flex items-center gap-1"
                        >
                          {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                          <span className={`px-1.5 py-0.5 rounded-full border ${STATUS_STYLES[job.status] ?? STATUS_STYLES.queued}`}>
                            {job.status}
                          </span>
                        </button>
                      </td>
                      <td className="py-1.5 px-3 font-mono">{shortSha(job.commit_sha)}</td>
                      <td className="py-1.5 px-3">{formatDateTime(job.started_at)}</td>
                      <td className="py-1.5 px-3">{formatDateTime(job.finished_at)}</td>
                      <td className="py-1.5 px-3 font-mono">{formatDuration(job.started_at, job.finished_at)}</td>
                      <td className="py-1.5 px-3 font-mono">{job.files_changed} / {job.files_total}</td>
                      <td className="py-1.5 px-3 font-mono">{job.embeddings_created} / {job.embeddings_reused}</td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={7} className="p-0">
                          <JobDetails
                            job={job}
                            canRetry={!hasActiveJob}
                            retrying={retry.isPending}
                            onRetry={() => retry.mutate(job.id)}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {retry.isError && <p className="text-[11px] text-amber-400">{apiErrorMessage(retry.error)}</p>}
    </section>
  );
};
