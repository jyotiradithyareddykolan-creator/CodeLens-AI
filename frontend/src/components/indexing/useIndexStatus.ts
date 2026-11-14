import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { indexingApi } from '../../services/api';
import { IndexingStatus } from '../../types';

export const POLL_INTERVAL_MS = 2500;

export function indexStatusKey(repoId: string) {
  return ['index-status', repoId];
}

export function isActive(status?: IndexingStatus): boolean {
  return Boolean(status?.active_job);
}

/** Indexing status for one repository; polls only while a job is queued or running. */
export function useIndexStatus(repoId: string) {
  return useQuery({
    queryKey: indexStatusKey(repoId),
    queryFn: () => indexingApi.status(repoId),
    refetchInterval: (query) => (isActive(query.state.data) ? POLL_INTERVAL_MS : false),
  });
}

/** Start / reindex / retry, refreshing status and the repository list afterwards. */
export function useIndexingActions(repoId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: indexStatusKey(repoId) });
    queryClient.invalidateQueries({ queryKey: ['index-jobs', repoId] });
    queryClient.invalidateQueries({ queryKey: ['repositories'] });
  };
  const start = useMutation({ mutationFn: () => indexingApi.start(repoId), onSuccess: refresh });
  const reindex = useMutation({ mutationFn: () => indexingApi.reindex(repoId), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (jobId: string) => indexingApi.retry(repoId, jobId), onSuccess: refresh });
  return { start, reindex, retry };
}
