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

export interface ServiceStatus {
  status: 'ok' | 'unavailable';
  /** Present when unavailable: timeout | connection_failed | no_workers | broker_unavailable */
  reason?: string;
  latency_ms?: number;
  engine?: string;
  vector_extension?: string | null;
  count?: number;
}

export interface ReadinessCheck {
  ready: boolean;
  checks: {
    database?: boolean;
    redis?: boolean;
    [key: string]: boolean | undefined;
  };
  services?: {
    database?: ServiceStatus;
    redis?: ServiceStatus;
    workers?: ServiceStatus;
  };
  features?: {
    retrieval?: Record<string, 'implemented' | 'planned' | string>;
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

export type IndexingJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface IndexingJob {
  id: string;
  repository_id: string;
  status: IndexingJobStatus;
  trigger: string;
  phase?: string | null;
  progress: number;
  commit_sha?: string | null;
  base_commit_sha?: string | null;
  force_full: boolean;
  attempts: number;
  files_total: number;
  files_changed: number;
  files_processed: number;
  files_indexed: number;
  files_skipped: number;
  files_failed: number;
  files_deleted: number;
  chunks_total: number;
  embeddings_created: number;
  embeddings_reused: number;
  stats?: Record<string, unknown> | null;
  error_message?: string | null;
  next_retry_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface IndexingStatus {
  repository_id: string;
  indexing_status: Repository['indexing_status'];
  is_indexed: boolean;
  last_indexed_commit?: string | null;
  last_indexed_at?: string | null;
  total_files: number;
  total_chunks: number;
  embeddings_enabled: boolean;
  active_job?: IndexingJob | null;
  latest_job?: IndexingJob | null;
}

export interface IndexedFileInfo {
  id: string;
  file_path: string;
  language: string;
  size_bytes: number;
  line_count: number;
  has_parse_errors: boolean;
  commit_sha?: string | null;
  chunk_count: number;
}

export interface ChunkInfo {
  id: string;
  chunk_type: string;
  symbol_name?: string | null;
  qualified_name?: string | null;
  parent_symbol?: string | null;
  start_line: number;
  end_line: number;
  start_byte: number;
  end_byte: number;
  token_count: number;
  part_index: number;
  part_count: number;
  has_embedding: boolean;
  commit_sha: string;
  content: string;
}
