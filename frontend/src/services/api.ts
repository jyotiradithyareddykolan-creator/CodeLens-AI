import axios from 'axios';
import {
  SystemHealth,
  ReadinessCheck,
  Repository,
  PullRequest,
  CurrentUserResponse,
  InstallationList,
  InstallationRepository,
  IndexingJob,
  IndexingStatus,
  IndexedFileInfo,
  ChunkInfo,
} from '../types';

const TOKEN_STORAGE_KEY = 'codelens_session_token';
export const AUTH_CHANGED_EVENT = 'codelens:auth-changed';

// Session token helpers. Storage access is wrapped because it can throw
// (private browsing, blocked site data).
export const sessionToken = {
  get(): string | null {
    try {
      return window.localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  },
  clear(): void {
    try {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  },
};

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use((config) => {
  const token = sessionToken.get();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    // An expired or revoked session token: drop it so the UI shows the sign-in prompt.
    if (error?.response?.status === 401 && sessionToken.get()) {
      sessionToken.clear();
    }
    return Promise.reject(error);
  },
);

/** Human-readable message from an API error, without exposing internals. */
export function apiErrorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (error.response?.status === 401) return 'Please sign in with GitHub.';
    if (!error.response) return 'Cannot reach the CodeLens backend.';
  }
  return fallback;
}

export interface ConnectRepositoryInput {
  installation_id: string;
  github_repo_id: number;
}

export const healthApi = {
  getHealth: async (): Promise<SystemHealth> => {
    const res = await api.get<SystemHealth>('/health');
    return res.data;
  },
  getReadiness: async (): Promise<ReadinessCheck> => {
    const res = await api.get<ReadinessCheck>('/ready');
    return res.data;
  },
};

export const githubApi = {
  getInstallations: async (): Promise<InstallationList> => {
    const res = await api.get<InstallationList>('/api/github/installations');
    return res.data;
  },
  getInstallationRepositories: async (installationId: string): Promise<InstallationRepository[]> => {
    const res = await api.get<InstallationRepository[]>(
      `/api/github/installations/${installationId}/repositories`,
    );
    return res.data;
  },
};

export const repositoryApi = {
  getRepositories: async (): Promise<Repository[]> => {
    const res = await api.get<Repository[]>('/api/repositories');
    return res.data;
  },
  connectRepository: async (data: ConnectRepositoryInput): Promise<Repository> => {
    const res = await api.post<Repository>('/api/repositories/connect', data);
    return res.data;
  },
  disconnectRepository: async (id: string): Promise<{ status: string }> => {
    const res = await api.delete<{ status: string }>(`/api/repositories/${id}`);
    return res.data;
  },
  getRepositoryStatus: async (id: string): Promise<any> => {
    const res = await api.get(`/api/repositories/${id}/status`);
    return res.data;
  },
};

export const indexingApi = {
  start: async (repoId: string, forceFull = false): Promise<IndexingJob> => {
    const res = await api.post<IndexingJob>(`/api/repositories/${repoId}/index`, { force_full: forceFull });
    return res.data;
  },
  reindex: async (repoId: string): Promise<IndexingJob> => {
    const res = await api.post<IndexingJob>(`/api/repositories/${repoId}/reindex`);
    return res.data;
  },
  retry: async (repoId: string, jobId: string): Promise<IndexingJob> => {
    const res = await api.post<IndexingJob>(`/api/repositories/${repoId}/index/jobs/${jobId}/retry`);
    return res.data;
  },
  status: async (repoId: string): Promise<IndexingStatus> => {
    const res = await api.get<IndexingStatus>(`/api/repositories/${repoId}/index/status`);
    return res.data;
  },
  jobs: async (repoId: string, limit = 10, skip = 0): Promise<IndexingJob[]> => {
    const res = await api.get<IndexingJob[]>(`/api/repositories/${repoId}/index/jobs`, { params: { limit, skip } });
    return res.data;
  },
  job: async (repoId: string, jobId: string): Promise<IndexingJob> => {
    const res = await api.get<IndexingJob>(`/api/repositories/${repoId}/index/jobs/${jobId}`);
    return res.data;
  },
  files: async (repoId: string, q?: string): Promise<IndexedFileInfo[]> => {
    const res = await api.get<IndexedFileInfo[]>(`/api/repositories/${repoId}/index/files`, {
      params: q ? { q } : {},
    });
    return res.data;
  },
  chunks: async (repoId: string, fileId: string): Promise<ChunkInfo[]> => {
    const res = await api.get<ChunkInfo[]>(`/api/repositories/${repoId}/index/files/${fileId}/chunks`);
    return res.data;
  },
};

export const pullRequestApi = {
  getPullRequests: async (repoId?: string): Promise<PullRequest[]> => {
    const res = await api.get<PullRequest[]>('/api/pull-requests', {
      params: repoId ? { repository_id: repoId } : {},
    });
    return res.data;
  },
};

export const authApi = {
  getLoginUrl: async (): Promise<{ url: string }> => {
    const res = await api.get<{ url: string }>('/api/auth/github');
    return res.data;
  },
  getMe: async (): Promise<CurrentUserResponse> => {
    const res = await api.get<CurrentUserResponse>('/api/auth/me');
    return res.data;
  },
  logout: async (): Promise<void> => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      sessionToken.clear();
    }
  },
};

export default api;
