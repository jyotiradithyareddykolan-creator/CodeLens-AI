import axios from 'axios';
import { SystemHealth, ReadinessCheck, Repository, PullRequest } from '../types';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface ConnectRepositoryInput {
  github_repo_id: number;
  name: string;
  owner: string;
  full_name: string;
  default_branch?: string;
  is_private?: boolean;
  description?: string;
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
  getMe: async (): Promise<any> => {
    const res = await api.get('/api/auth/me');
    return res.data;
  },
  logout: async (): Promise<any> => {
    const res = await api.post('/api/auth/logout');
    return res.data;
  },
};

export default api;
