import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, FileCode, Loader2, Search } from 'lucide-react';
import { apiErrorMessage, indexingApi } from '../../services/api';
import { IndexingJob, IndexingStatus } from '../../types';
import { formatTime, phaseLabel } from './IndexStatusBadge';

const shortSha = (sha?: string | null) => (sha ? sha.slice(0, 10) : '—');

const Stat: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="rounded-lg bg-slate-950/60 border border-slate-800 px-3 py-2">
    <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
    <div className="text-sm font-mono text-slate-200">{value}</div>
  </div>
);

const JobSummary: React.FC<{ job: IndexingJob }> = ({ job }) => {
  const skipped = (job.stats?.skipped_by_reason ?? {}) as Record<string, number>;
  const embeddingsNote = typeof job.stats?.embeddings === 'string' ? (job.stats.embeddings as string) : null;
  return (
    <div className="space-y-2 text-xs text-slate-300">
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <span>Status: <strong className="text-slate-100">{job.status}</strong></span>
        {job.status === 'running' && <span>{phaseLabel(job.phase)} · {Math.round(job.progress)}%</span>}
        <span>Commit: <code className="font-mono">{shortSha(job.commit_sha)}</code></span>
        <span>Trigger: {job.trigger}{job.force_full ? ' (full)' : ''}</span>
        <span>Attempts: {job.attempts}</span>
        {job.finished_at && <span>Finished: {formatTime(job.finished_at)}</span>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-400">
        <span>Files: {job.files_indexed} indexed / {job.files_changed} changed / {job.files_total} eligible</span>
        <span>Skipped: {job.files_skipped}</span>
        <span>Failed: {job.files_failed}</span>
        <span>Removed: {job.files_deleted}</span>
        <span>Chunks: {job.chunks_total}</span>
        <span>Embeddings: {job.embeddings_created} new / {job.embeddings_reused} reused</span>
      </div>
      {Object.keys(skipped).length > 0 && (
        <div className="text-slate-500">
          Skipped by reason: {Object.entries(skipped).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ')}
        </div>
      )}
      {embeddingsNote && <div className="text-amber-400">Embeddings {embeddingsNote}</div>}
      {job.error_message && (
        <div className="flex items-start gap-1.5 text-rose-300" role="alert">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> <span>{job.error_message}</span>
        </div>
      )}
    </div>
  );
};

/** Indexing details for one repository: last snapshot, latest job, files and their chunks. */
export const RepositoryIndexPanel: React.FC<{ repoId: string; status?: IndexingStatus }> = ({ repoId, status }) => {
  const [filter, setFilter] = useState('');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const files = useQuery({
    queryKey: ['index-files', repoId, filter, status?.last_indexed_commit],
    queryFn: () => indexingApi.files(repoId, filter || undefined),
    enabled: Boolean(status?.is_indexed),
  });
  const chunks = useQuery({
    queryKey: ['index-chunks', repoId, selectedFile],
    queryFn: () => indexingApi.chunks(repoId, selectedFile as string),
    enabled: Boolean(selectedFile),
  });
  const job = status?.active_job ?? status?.latest_job;

  return (
    <div className="p-4 space-y-4 bg-slate-950/40 border-t border-slate-800" data-testid="index-panel">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Stat label="Last indexed commit" value={shortSha(status?.last_indexed_commit)} />
        <Stat label="Indexed files" value={status?.total_files ?? 0} />
        <Stat label="Chunks" value={status?.total_chunks ?? 0} />
        <Stat label="Embeddings" value={status?.embeddings_enabled ? 'enabled' : 'disabled'} />
      </div>
      {status && !status.embeddings_enabled && (
        <p className="text-xs text-amber-400">
          GEMINI_API_KEY is not configured on the backend: files are parsed and chunked, but no embeddings are
          generated. Run indexing again after adding the key to fill them in.
        </p>
      )}
      {job ? <JobSummary job={job} /> : <p className="text-xs text-slate-400">This repository has not been indexed yet.</p>}

      {status?.is_indexed && (
        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <label className="flex items-center gap-2 rounded-lg bg-slate-950 border border-slate-800 px-2 py-1.5">
              <Search className="w-3.5 h-3.5 text-slate-500" />
              <input
                aria-label="Filter indexed files"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter files by path"
                className="bg-transparent text-xs text-slate-200 w-full focus:outline-none"
              />
            </label>
            <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-800 divide-y divide-slate-800/60">
              {files.isLoading ? (
                <div className="p-3 text-xs text-slate-400 flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading files...
                </div>
              ) : files.isError ? (
                <p className="p-3 text-xs text-amber-400">{apiErrorMessage(files.error)}</p>
              ) : (files.data ?? []).length === 0 ? (
                <p className="p-3 text-xs text-slate-400">No indexed files match.</p>
              ) : (
                (files.data ?? []).map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    onClick={() => setSelectedFile(f.id)}
                    className={`w-full text-left px-3 py-1.5 text-xs flex justify-between gap-2 hover:bg-slate-800/50 ${
                      selectedFile === f.id ? 'bg-slate-800/70' : ''
                    }`}
                  >
                    <span className="flex items-center gap-1.5 truncate text-slate-200">
                      <FileCode className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                      <span className="truncate">{f.file_path}</span>
                    </span>
                    <span className="shrink-0 font-mono text-slate-500">
                      {f.chunk_count} chunks{f.has_parse_errors ? ' · parse errors' : ''}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
          <div className="max-h-80 overflow-y-auto rounded-lg border border-slate-800">
            {!selectedFile ? (
              <p className="p-3 text-xs text-slate-400">Select a file to see its chunks.</p>
            ) : chunks.isLoading ? (
              <div className="p-3 text-xs text-slate-400 flex items-center gap-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading chunks...
              </div>
            ) : chunks.isError ? (
              <p className="p-3 text-xs text-amber-400">{apiErrorMessage(chunks.error)}</p>
            ) : (
              <ul className="divide-y divide-slate-800/60">
                {(chunks.data ?? []).map((c) => (
                  <li key={c.id} className="p-2 space-y-1">
                    <div className="flex justify-between text-[11px] text-slate-400">
                      <span>
                        <span className="text-slate-200">{c.qualified_name ?? c.chunk_type}</span> · {c.chunk_type}
                        {c.part_count > 1 ? ` (part ${c.part_index + 1}/${c.part_count})` : ''}
                      </span>
                      <span className="font-mono">
                        L{c.start_line}–{c.end_line} · ~{c.token_count} tok{c.has_embedding ? '' : ' · no embedding'}
                      </span>
                    </div>
                    <pre className="text-[11px] leading-snug text-slate-300 bg-slate-950 rounded p-2 overflow-x-auto max-h-40">
                      {c.content}
                    </pre>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
