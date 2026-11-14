export interface SystemHealth {
  status: string;
  app: string;
  version: string;
  timestamp: string;
  environment: string;
  services: {
    [key: string]: {
      status: string;
      details?: string;
    };
  };
}

export interface ReadinessCheck {
  ready: boolean;
  checks: {
    database?: boolean;
    redis?: boolean;
    [key: string]: boolean | undefined;
  };
}

export interface Repository {
  id: string;
  name: string;
  owner: string;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  description?: string;
  is_indexed: boolean;
  indexing_status: 'unindexed' | 'pending' | 'indexing' | 'indexed' | 'failed';
  last_indexed_at?: string;
  last_indexed_commit?: string;
  total_files: number;
  total_chunks: number;
  created_at: string;
}

export interface PullRequest {
  id: string;
  repository_id: string;
  pr_number: number;
  title: string;
  author_username: string;
  state: 'open' | 'closed' | 'merged';
  base_branch: string;
  head_branch: string;
  base_sha: string;
  head_sha: string;
  created_at: string;
  total_findings?: number;
}

export interface CurrentUser {
  id?: string;
  username: string;
  email?: string | null;
  avatar_url?: string | null;
  is_active: boolean;
}

export interface CurrentUserResponse {
  authenticated: boolean;
  user: CurrentUser;
}

export interface Installation {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: 'User' | 'Organization';
  created_at: string;
}

export interface InstallationList {
  installations: Installation[];
  install_url?: string | null;
}

export interface InstallationRepository {
  github_repo_id: number;
  name: string;
  owner: string;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  description?: string | null;
  html_url?: string | null;
  connected_repository_id?: string | null;
}
