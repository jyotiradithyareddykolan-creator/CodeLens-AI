import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Cpu, Database, Layers, Loader2, Search, Server } from 'lucide-react';
import { healthApi } from '../../services/api';
import { ReadinessCheck, ServiceStatus, SystemHealth } from '../../types';

export type CardState = 'checking' | 'healthy' | 'degraded' | 'unavailable' | 'planned';

export interface CardModel {
  key: string;
  title: string;
  state: CardState;
  detail: string;
}

const STATE_LABEL: Record<CardState, string> = {
  checking: 'Checking…',
  healthy: 'Healthy',
  degraded: 'Degraded',
  unavailable: 'Unavailable',
  planned: 'Planned',
};

const STATE_STYLE: Record<CardState, string> = {
  checking: 'bg-slate-800 text-slate-300 border-slate-700',
  healthy: 'bg-emerald-950 text-emerald-400 border-emerald-800/60',
  degraded: 'bg-amber-950/60 text-amber-300 border-amber-800/60',
  unavailable: 'bg-rose-950/60 text-rose-300 border-rose-800/60',
  planned: 'bg-slate-800 text-slate-400 border-slate-700',
};

const REASONS: Record<string, string> = {
  timeout: 'did not respond in time',
  connection_failed: 'connection failed',
  no_workers: 'no worker is running; indexing jobs will wait in the queue',
  broker_unavailable: 'cannot be checked while Redis is unavailable',
};

const reason = (s?: ServiceStatus) => (s?.reason && REASONS[s.reason]) || 'not reachable';
const latency = (s?: ServiceStatus) => (typeof s?.latency_ms === 'number' ? ` · ${Math.round(s.latency_ms)} ms` : '');

/** Turn real /health and /ready?details=true results into card states. Pure for testing. */
export function buildCards(input: {
  health?: SystemHealth;
  healthError: boolean;
  readiness?: ReadinessCheck;
  readinessError: boolean;
  loading: boolean;
}): CardModel[] {
  const { health, healthError, readiness, readinessError, loading } = input;
  const unknown = readinessError || (!loading && !readiness);

  const api: CardModel = { key: 'api', title: 'FastAPI backend', state: 'checking', detail: 'Checking /health…' };
  if (health) {
    api.state = health.status === 'healthy' ? 'healthy' : 'degraded';
    api.detail = `v${health.version} · ${health.environment}`;
  } else if (healthError) {
    api.state = 'unavailable';
    api.detail = 'The API is not responding';
  }

  const card = (key: string, title: string): CardModel => ({ key, title, state: 'checking', detail: 'Checking…' });
  const db = card('database', 'PostgreSQL + pgvector');
  const redis = card('redis', 'Redis (task queue)');
  const workers = card('workers', 'Celery workers');
  if (unknown) {
    for (const c of [db, redis, workers]) {
      c.state = 'unavailable';
      c.detail = 'Status unknown: readiness check failed';
    }
  } else if (readiness) {
    const s = readiness.services ?? {};
    if (s.database?.status === 'ok') {
      if (s.database.engine === 'postgresql' && s.database.vector_extension) {
        db.state = 'healthy';
        db.detail = `Connected · pgvector ${s.database.vector_extension}${latency(s.database)}`;
      } else if (s.database.engine === 'postgresql') {
        db.state = 'degraded';
        db.detail = 'Connected, but the pgvector extension is missing';
      } else {
        db.state = 'degraded';
        db.detail = `Connected to ${s.database.engine ?? 'a non-PostgreSQL database'} · no vector index`;
      }
    } else {
      db.state = 'unavailable';
      db.detail = `Database ${reason(s.database)}`;
    }

    if (s.redis?.status === 'ok') {
      redis.state = 'healthy';
      redis.detail = `Connected${latency(s.redis)}`;
    } else {
      redis.state = 'unavailable';
      redis.detail = `Redis ${reason(s.redis)}`;
    }

    if (!s.workers) {
      workers.state = 'unavailable';
      workers.detail = 'Not reported by the backend';
    } else if (s.workers.status === 'ok') {
      workers.state = 'healthy';
      workers.detail = `${s.workers.count} worker${s.workers.count === 1 ? '' : 's'} online`;
    } else {
      workers.state = s.workers.reason === 'no_workers' ? 'degraded' : 'unavailable';
      workers.detail = `Workers: ${reason(s.workers)}`;
    }
  }

  const retrieval = readiness?.features?.retrieval ?? {};
  const hybrid: CardModel = {
    key: 'retrieval',
    title: 'Hybrid retrieval',
    state: retrieval.hybrid_search === 'implemented' ? 'healthy' : 'planned',
    detail:
      retrieval.hybrid_search === 'implemented'
        ? 'Vector + keyword retrieval available'
        : 'Not implemented yet. Vector search exists; keyword and hybrid retrieval arrive in Phase 4.',
  };

  return [api, db, redis, workers, hybrid];
}

const ICONS: Record<string, React.ElementType> = { api: Cpu, database: Database, redis: Server, workers: Layers, retrieval: Search };

/** Live backend dependency status (refreshes every 30 s). */
export const SystemStatusPanel: React.FC = () => {
  const health = useQuery({ queryKey: ['system-health'], queryFn: healthApi.getHealth, refetchInterval: 30_000 });
  const readiness = useQuery({
    queryKey: ['system-readiness'],
    queryFn: () => healthApi.getReadiness(true),
    refetchInterval: 30_000,
  });
  const cards = buildCards({
    health: health.data,
    healthError: health.isError,
    readiness: readiness.data,
    readinessError: readiness.isError,
    loading: readiness.isLoading,
  });

  return (
    <div className="p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
      <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
        <Layers className="w-4 h-4 text-blue-400" />
        <span>System Status</span>
        {(health.isFetching || readiness.isFetching) && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-500" />}
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 text-xs">
        {cards.map((c) => {
          const Icon = ICONS[c.key] ?? Layers;
          return (
            <div key={c.key} data-testid={`status-${c.key}`}
                 className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80 space-y-2">
              <div className="flex items-center gap-2">
                <Icon className="w-4 h-4 text-blue-400 shrink-0" />
                <p className="font-medium text-slate-300">{c.title}</p>
              </div>
              <span className={`inline-flex px-2 py-0.5 rounded-full border text-[11px] font-medium ${STATE_STYLE[c.state]}`}>
                {STATE_LABEL[c.state]}
              </span>
              <p className="text-slate-500 leading-snug">{c.detail}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
