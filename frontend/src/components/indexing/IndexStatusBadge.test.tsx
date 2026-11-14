import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { IndexStatusBadge } from './IndexStatusBadge';
import { IndexingJob, IndexingStatus } from '../../types';

afterEach(cleanup);

export function makeJob(overrides: Partial<IndexingJob> = {}): IndexingJob {
  return {
    id: 'job-1', repository_id: 'repo-1', status: 'running', trigger: 'manual', phase: 'embedding',
    progress: 42.4, commit_sha: 'abcdef1234567890', base_commit_sha: null, force_full: false, attempts: 1,
    files_total: 10, files_changed: 10, files_processed: 5, files_indexed: 0, files_skipped: 2, files_failed: 0,
    files_deleted: 0, chunks_total: 0, embeddings_created: 3, embeddings_reused: 0, stats: null, error_message: null,
    next_retry_at: null, started_at: null, finished_at: null, created_at: '2026-11-14T10:00:00Z',
    updated_at: '2026-11-14T10:00:00Z', ...overrides,
  };
}

export function makeStatus(overrides: Partial<IndexingStatus> = {}): IndexingStatus {
  return {
    repository_id: 'repo-1', indexing_status: 'unindexed', is_indexed: false, last_indexed_commit: null,
    last_indexed_at: null, total_files: 0, total_chunks: 0, embeddings_enabled: true, active_job: null,
    latest_job: null, ...overrides,
  };
}

describe('IndexStatusBadge', () => {
  it('shows phase and progress while running', () => {
    render(<IndexStatusBadge status={makeStatus({ active_job: makeJob() })} />);
    expect(screen.getByText(/Generating embeddings · 42%/)).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('42');
  });

  it('shows queued and retrying states', () => {
    const { rerender } = render(<IndexStatusBadge status={makeStatus({ active_job: makeJob({ status: 'queued' }) })} />);
    expect(screen.getByText('Queued')).toBeTruthy();
    rerender(
      <IndexStatusBadge
        status={makeStatus({
          active_job: makeJob({ status: 'queued', next_retry_at: '2026-11-14T10:05:00Z', error_message: 'Retrying: rate limited' }),
        })}
      />,
    );
    expect(screen.getByText(/Retrying at/)).toBeTruthy();
    expect(screen.getByTestId('index-status').getAttribute('title')).toBe('Retrying: rate limited');
  });

  it('shows indexed, with a warning when the last run failed', () => {
    const { rerender } = render(<IndexStatusBadge status={makeStatus({ indexing_status: 'indexed', is_indexed: true })} />);
    expect(screen.getByText('Indexed')).toBeTruthy();
    expect(screen.queryByLabelText('Last indexing run failed')).toBeNull();
    rerender(
      <IndexStatusBadge
        status={makeStatus({ indexing_status: 'indexed', is_indexed: true, latest_job: makeJob({ status: 'failed', error_message: 'boom' }) })}
      />,
    );
    expect(screen.getByLabelText('Last indexing run failed')).toBeTruthy();
  });

  it('shows failed, pending and not indexed', () => {
    const { rerender } = render(<IndexStatusBadge status={makeStatus({ indexing_status: 'failed' })} />);
    expect(screen.getByText('Failed')).toBeTruthy();
    rerender(<IndexStatusBadge status={makeStatus({ indexing_status: 'pending' })} />);
    expect(screen.getByText('Pending')).toBeTruthy();
    rerender(<IndexStatusBadge fallbackStatus="unindexed" />);
    expect(screen.getByText('Not indexed')).toBeTruthy();
  });
});
