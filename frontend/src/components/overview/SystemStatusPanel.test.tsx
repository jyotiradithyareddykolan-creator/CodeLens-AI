import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SystemStatusPanel, buildCards } from './SystemStatusPanel';
import { ReadinessCheck, SystemHealth } from '../../types';

const api = vi.hoisted(() => ({ getHealth: vi.fn(), getReadiness: vi.fn() }));
vi.mock('../../services/api', () => ({ healthApi: { getHealth: api.getHealth, getReadiness: api.getReadiness } }));

const health: SystemHealth = {
  status: 'healthy', app: 'CodeLens AI', version: '0.1.0', timestamp: '2026-11-14T10:00:00Z',
  environment: 'development', services: {},
};
const planned = { retrieval: { vector_search: 'implemented', keyword_search: 'planned', hybrid_search: 'planned' } };
const allUp: ReadinessCheck = {
  ready: true,
  checks: { database: true, redis: true },
  services: {
    database: { status: 'ok', latency_ms: 4.2, engine: 'postgresql', vector_extension: '0.8.7' },
    redis: { status: 'ok', latency_ms: 1.1 },
    workers: { status: 'ok', count: 1 },
  },
  features: planned,
};

const byKey = (cards: ReturnType<typeof buildCards>) => Object.fromEntries(cards.map((c) => [c.key, c]));

describe('buildCards', () => {
  it('reports healthy services from real readiness data', () => {
    const c = byKey(buildCards({ health, healthError: false, readiness: allUp, readinessError: false, loading: false }));
    expect(c.api.state).toBe('healthy');
    expect(c.database).toMatchObject({ state: 'healthy', detail: 'Connected · pgvector 0.8.7 · 4 ms' });
    expect(c.redis.state).toBe('healthy');
    expect(c.workers).toMatchObject({ state: 'healthy', detail: '1 worker online' });
  });

  it('never claims hybrid retrieval exists', () => {
    const c = byKey(buildCards({ health, healthError: false, readiness: allUp, readinessError: false, loading: false }));
    expect(c.retrieval.state).toBe('planned');
    expect(c.retrieval.detail).toMatch(/Not implemented yet.*Phase 4/);
    // Even with no readiness data at all.
    expect(byKey(buildCards({ healthError: false, readinessError: true, loading: false })).retrieval.state).toBe('planned');
  });

  it('a healthy API does not imply healthy dependencies', () => {
    const down: ReadinessCheck = {
      ready: false, checks: { database: false, redis: false }, features: planned,
      services: {
        database: { status: 'unavailable', reason: 'timeout' },
        redis: { status: 'unavailable', reason: 'connection_failed' },
        workers: { status: 'unavailable', reason: 'broker_unavailable' },
      },
    };
    const c = byKey(buildCards({ health, healthError: false, readiness: down, readinessError: false, loading: false }));
    expect(c.api.state).toBe('healthy');
    expect(c.database).toMatchObject({ state: 'unavailable', detail: 'Database did not respond in time' });
    expect(c.redis).toMatchObject({ state: 'unavailable', detail: 'Redis connection failed' });
    expect(c.workers.state).toBe('unavailable');
  });

  it('marks degraded states', () => {
    const partial: ReadinessCheck = {
      ...allUp,
      services: {
        database: { status: 'ok', engine: 'postgresql', vector_extension: null },
        redis: { status: 'ok' },
        workers: { status: 'unavailable', reason: 'no_workers', count: 0 },
      },
    };
    const c = byKey(buildCards({ health, healthError: false, readiness: partial, readinessError: false, loading: false }));
    expect(c.database).toMatchObject({ state: 'degraded', detail: 'Connected, but the pgvector extension is missing' });
    expect(c.workers.state).toBe('degraded');
    const sqlite = byKey(buildCards({
      health, healthError: false, loading: false, readinessError: false,
      readiness: { ...allUp, services: { ...allUp.services, database: { status: 'ok', engine: 'sqlite', vector_extension: null } } },
    }));
    expect(sqlite.database.state).toBe('degraded');
  });

  it('shows checking while loading and unavailable when checks cannot run', () => {
    const loading = byKey(buildCards({ healthError: false, readinessError: false, loading: true }));
    expect([loading.api.state, loading.database.state, loading.redis.state]).toEqual(['checking', 'checking', 'checking']);
    const failed = byKey(buildCards({ healthError: true, readinessError: true, loading: false }));
    expect(failed.api.state).toBe('unavailable');
    expect(failed.database).toMatchObject({ state: 'unavailable', detail: 'Status unknown: readiness check failed' });
  });
});

describe('SystemStatusPanel', () => {
  afterEach(cleanup);

  it('renders live states from the backend', async () => {
    api.getHealth.mockResolvedValue(health);
    api.getReadiness.mockResolvedValue({
      ...allUp, ready: false, checks: { database: true, redis: false },
      services: { ...allUp.services, redis: { status: 'unavailable', reason: 'connection_failed' } },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><SystemStatusPanel /></QueryClientProvider>);
    expect(await within(await screen.findByTestId('status-redis')).findByText('Unavailable')).toBeTruthy();
    expect(within(screen.getByTestId('status-database')).getByText('Healthy')).toBeTruthy();
    expect(within(screen.getByTestId('status-retrieval')).getByText('Planned')).toBeTruthy();
    expect(screen.queryByText(/Hybrid Engine/)).toBeNull();
    expect(api.getReadiness).toHaveBeenCalledWith(true);
  });
});
