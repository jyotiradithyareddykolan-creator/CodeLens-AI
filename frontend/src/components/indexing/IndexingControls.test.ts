import { describe, expect, it } from 'vitest';
import { RESEND_AFTER_MS, isResendable } from './IndexingControls';
import { makeJob, makeStatus } from './IndexStatusBadge.test';

const NOW = Date.parse('2026-11-14T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe('isResendable', () => {
  it('requires an active queued job', () => {
    expect(isResendable(undefined, NOW)).toBe(false);
    expect(isResendable(makeStatus(), NOW)).toBe(false);
    expect(isResendable(makeStatus({ active_job: makeJob({ status: 'running', updated_at: ago(600_000) }) }), NOW)).toBe(false);
  });

  it('waits for the resend window', () => {
    const queued = (updated_at: string) => makeStatus({ active_job: makeJob({ status: 'queued', updated_at }) });
    expect(isResendable(queued(ago(RESEND_AFTER_MS - 1)), NOW)).toBe(false);
    expect(isResendable(queued(ago(RESEND_AFTER_MS)), NOW)).toBe(true);
  });

  it('never resends a job waiting for a scheduled retry', () => {
    const status = makeStatus({
      active_job: makeJob({ status: 'queued', updated_at: ago(600_000), next_retry_at: ago(-60_000) }),
    });
    expect(isResendable(status, NOW)).toBe(false);
  });
});
