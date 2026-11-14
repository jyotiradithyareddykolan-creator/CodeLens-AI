import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { JOB_HISTORY_PAGE_SIZE, JobHistory, formatDuration } from './JobHistory';
import { makeJob } from './IndexStatusBadge.test';

const api = vi.hoisted(() => ({ jobs: vi.fn(), retry: vi.fn(), start: vi.fn(), reindex: vi.fn() }));

vi.mock('../../services/api', () => ({
  apiErrorMessage: (e: unknown, fallback = 'Something went wrong.') => (e instanceof Error ? e.message : fallback),
  indexingApi: { jobs: api.jobs, retry: api.retry, start: api.start, reindex: api.reindex },
}));

function renderHistory(hasActiveJob = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <JobHistory repoId="repo-1" hasActiveJob={hasActiveJob} />
    </QueryClientProvider>,
  );
}

const succeeded = makeJob({
  id: 'job-ok', status: 'succeeded', commit_sha: '0617321744d604253bd67f42a99c364d96e86b0d', files_changed: 1,
  files_total: 32, embeddings_created: 4, embeddings_reused: 739,
  started_at: '2026-11-14T09:33:05Z', finished_at: '2026-11-14T09:33:13Z',
});
const failed = makeJob({
  id: 'job-bad', status: 'failed', commit_sha: '9ee7e7a0c77d4c8ef2a0336a56069fa9d49a174a',
  error_message: 'GitHub request failed: HTTP 403', started_at: '2026-11-14T09:00:00Z', finished_at: '2026-11-14T09:08:16Z',
  stats: { skipped_by_reason: { unsupported_language: 16 } },
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe('formatDuration', () => {
  it('formats seconds, minutes and hours', () => {
    expect(formatDuration('2026-11-14T09:00:00Z', '2026-11-14T09:00:08Z')).toBe('8s');
    expect(formatDuration('2026-11-14T09:00:00Z', '2026-11-14T09:08:16Z')).toBe('8m 16s');
    expect(formatDuration('2026-11-14T09:00:00Z', '2026-11-14T10:02:00Z')).toBe('1h 2m');
    expect(formatDuration(null, '2026-11-14T10:02:00Z')).toBe('—');
  });
});

describe('JobHistory', () => {
  it('lists runs with commit, duration, files and embeddings', async () => {
    api.jobs.mockResolvedValue([succeeded, failed]);
    renderHistory();
    expect(await screen.findByText('0617321744')).toBeTruthy();
    expect(screen.getByText('8s')).toBeTruthy();
    expect(screen.getByText('8m 16s')).toBeTruthy();
    expect(screen.getByText('1 / 32')).toBeTruthy();
    expect(screen.getByText('4 / 739')).toBeTruthy();
    expect(screen.getByText('succeeded')).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
    expect(api.jobs).toHaveBeenCalledWith('repo-1', JOB_HISTORY_PAGE_SIZE + 1, 0);
  });

  it('shows details and retries a failed job', async () => {
    api.jobs.mockResolvedValue([failed]);
    api.retry.mockResolvedValue(makeJob({ status: 'queued', trigger: 'retry' }));
    renderHistory();
    fireEvent.click(await screen.findByRole('button', { name: /Show details of job job-bad/ }));
    expect(screen.getByRole('alert').textContent).toContain('GitHub request failed: HTTP 403');
    expect(screen.getByText(/unsupported language 16/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Retry this job/ }));
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith('repo-1', 'job-bad'));
  });

  it('disables retry while another job is active', async () => {
    api.jobs.mockResolvedValue([failed]);
    renderHistory(true);
    fireEvent.click(await screen.findByRole('button', { name: /Show details of job job-bad/ }));
    expect((screen.getByRole('button', { name: /Retry this job/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('pages through older runs', async () => {
    const many = Array.from({ length: JOB_HISTORY_PAGE_SIZE + 1 }, (_, i) => makeJob({ id: `j${i}`, status: 'succeeded' }));
    api.jobs.mockResolvedValueOnce(many).mockResolvedValueOnce([failed]);
    renderHistory();
    const older = await screen.findByRole('button', { name: 'Older' });
    await waitFor(() => expect((older as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getAllByText('succeeded')).toHaveLength(JOB_HISTORY_PAGE_SIZE);
    fireEvent.click(older);
    await waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith('repo-1', JOB_HISTORY_PAGE_SIZE + 1, JOB_HISTORY_PAGE_SIZE));
    expect(await screen.findByText('Page 2')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Older' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows empty and error states', async () => {
    api.jobs.mockResolvedValueOnce([]);
    renderHistory();
    expect(await screen.findByText('No indexing runs yet.')).toBeTruthy();
    cleanup();
    api.jobs.mockRejectedValueOnce(new Error('Cannot reach the CodeLens backend.'));
    renderHistory();
    expect(await screen.findByText('Cannot reach the CodeLens backend.')).toBeTruthy();
  });
});
