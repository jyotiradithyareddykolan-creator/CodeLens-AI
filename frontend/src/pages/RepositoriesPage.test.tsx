import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RepositoriesPage } from './RepositoriesPage';
import { Repository } from '../types';
import { makeJob, makeStatus } from '../components/indexing/IndexStatusBadge.test';

const api = vi.hoisted(() => ({
  token: 'session-token' as string | null,
  getRepositories: vi.fn(),
  getMe: vi.fn(),
  status: vi.fn(),
  start: vi.fn(),
  reindex: vi.fn(),
  retry: vi.fn(),
  files: vi.fn(),
  chunks: vi.fn(),
}));

vi.mock('../services/api', () => ({
  AUTH_CHANGED_EVENT: 'codelens:auth-changed',
  sessionToken: { get: () => api.token, set: vi.fn(), clear: vi.fn() },
  apiErrorMessage: (e: unknown, fallback = 'Something went wrong.') => (e instanceof Error ? e.message : fallback),
  authApi: { getMe: api.getMe, getLoginUrl: vi.fn(), logout: vi.fn() },
  repositoryApi: { getRepositories: api.getRepositories, disconnectRepository: vi.fn(), connectRepository: vi.fn() },
  githubApi: { getInstallations: vi.fn(), getInstallationRepositories: vi.fn() },
  indexingApi: {
    status: api.status, start: api.start, reindex: api.reindex, retry: api.retry, files: api.files,
    chunks: api.chunks, jobs: vi.fn(),
  },
}));

const repo: Repository = {
  id: 'repo-1', name: 'svc', owner: 'acme', full_name: 'acme/svc', default_branch: 'main', is_private: true,
  description: 'Service', is_indexed: false, indexing_status: 'unindexed', total_files: 0, total_chunks: 0,
  created_at: '2026-11-14T10:00:00Z',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RepositoriesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.token = 'session-token';
  vi.clearAllMocks();
  api.getMe.mockResolvedValue({ authenticated: true, user: { username: 'dev', is_active: true } });
});
afterEach(cleanup);

describe('RepositoriesPage indexing integration', () => {
  it('asks signed-out users to sign in', async () => {
    api.token = null;
    renderPage();
    expect(await screen.findByText('Sign in to manage repositories')).toBeTruthy();
    expect(api.getRepositories).not.toHaveBeenCalled();
  });

  it('shows the empty state', async () => {
    api.getRepositories.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No Repositories Connected Yet')).toBeTruthy();
  });

  it('shows API failures', async () => {
    api.getRepositories.mockRejectedValue(new Error('Cannot reach the CodeLens backend.'));
    renderPage();
    expect(await screen.findByText('Cannot reach the CodeLens backend.')).toBeTruthy();
  });

  it('starts indexing an unindexed repository', async () => {
    api.getRepositories.mockResolvedValue([repo]);
    api.status.mockResolvedValue(makeStatus());
    api.start.mockResolvedValue(makeJob({ status: 'queued' }));
    renderPage();
    expect(await screen.findByText('acme/svc')).toBeTruthy();
    expect(await screen.findByText('Not indexed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Index/ }));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith('repo-1'));
  });

  it('shows live progress and disables actions while a job runs', async () => {
    api.getRepositories.mockResolvedValue([repo]);
    api.status.mockResolvedValue(makeStatus({ indexing_status: 'pending', active_job: makeJob({ progress: 61 }) }));
    renderPage();
    expect(await screen.findByText(/Generating embeddings · 61%/)).toBeTruthy();
    expect((screen.getByRole('button', { name: /Index/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers retry after a failure and shows the error in the details panel', async () => {
    const failed = makeJob({ status: 'failed', error_message: 'GitHub request failed: HTTP 403' });
    api.getRepositories.mockResolvedValue([{ ...repo, indexing_status: 'failed' }]);
    api.status.mockResolvedValue(makeStatus({ indexing_status: 'failed', latest_job: failed }));
    api.retry.mockResolvedValue(makeJob({ status: 'queued', trigger: 'retry' }));
    renderPage();
    expect(await screen.findByText('Failed')).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith('repo-1', 'job-1'));

    fireEvent.click(screen.getByRole('button', { name: /Show indexing details/ }));
    expect((await screen.findByRole('alert')).textContent).toContain('GitHub request failed: HTTP 403');
  });

  it('shows counts, update/reindex actions, files and chunks for an indexed repository', async () => {
    const indexed = { ...repo, is_indexed: true, indexing_status: 'indexed' as const, total_files: 2, total_chunks: 5 };
    api.getRepositories.mockResolvedValue([indexed]);
    api.status.mockResolvedValue(makeStatus({
      indexing_status: 'indexed', is_indexed: true, total_files: 2, total_chunks: 5,
      last_indexed_commit: 'abcdef1234567890', embeddings_enabled: false,
      latest_job: makeJob({ status: 'succeeded', progress: 100, files_indexed: 2, chunks_total: 5,
        stats: { embeddings: 'skipped: GEMINI_API_KEY is not configured', skipped_by_reason: { secret_detected: 1 } } }),
    }));
    api.files.mockResolvedValue([
      { id: 'f1', file_path: 'app/main.py', language: 'python', size_bytes: 10, line_count: 3,
        has_parse_errors: false, commit_sha: 'abcdef1234567890', chunk_count: 2 },
    ]);
    api.chunks.mockResolvedValue([
      { id: 'c1', chunk_type: 'function', symbol_name: 'main', qualified_name: 'main', parent_symbol: null,
        start_line: 1, end_line: 3, start_byte: 0, end_byte: 30, token_count: 12, part_index: 0, part_count: 1,
        has_embedding: false, commit_sha: 'abcdef1234567890', content: 'def main():\n    return 1\n' },
    ]);
    api.reindex.mockResolvedValue(makeJob({ status: 'queued', force_full: true }));
    renderPage();

    expect(await screen.findByText('2 files / 5 chunks')).toBeTruthy();
    expect(await screen.findByText('@ abcdef1234')).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: /Reindex/ }));
    await waitFor(() => expect(api.reindex).toHaveBeenCalledWith('repo-1'));

    fireEvent.click(screen.getByRole('button', { name: /Show indexing details/ }));
    expect(await screen.findByText(/GEMINI_API_KEY is not configured on the backend/)).toBeTruthy();
    expect(screen.getByText(/secret detected 1/)).toBeTruthy();
    fireEvent.click(await screen.findByText('app/main.py'));
    expect(await screen.findByText('L1–3 · ~12 tok · no embedding')).toBeTruthy();
    expect(screen.getByText(/def main\(\):/)).toBeTruthy();
    expect(api.chunks).toHaveBeenCalledWith('repo-1', 'f1');
  });
});
