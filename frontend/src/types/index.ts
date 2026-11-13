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
