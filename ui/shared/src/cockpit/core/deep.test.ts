import { describe, expect, it } from 'vitest';
import { createFixtureProvider, FIXTURE_SNAPSHOT_ID } from '../providers/fixture';
import { attemptRows, callRows, compareAttempts, planUnits, retryDeltas, requirementsFacts } from './deep';
import { loadRunEvidence } from './evidenceClient';
import type { EvidenceRecord } from './index';

const records = async (id: string): Promise<EvidenceRecord[]> => {
  const a = await loadRunEvidence(createFixtureProvider(), id, FIXTURE_SNAPSHOT_ID);
  if (a.state !== 'ready' && a.state !== 'degraded') throw new Error('no evidence');
  return [...a.data.records];
};

describe('deep selectors read only recorded records', () => {
  it('attempt rows carry per-attempt gates, calls and outcome', async () => {
    const rows = attemptRows(await records('fx-greenfield-compile-retry'));
    expect(rows.map((r) => r.attempt)).toEqual([1, 2]);
    expect(rows[0]!.concluded).toBe(false);
    expect(rows[1]!.concluded).toBe(true);
    expect(rows[0]!.gates.failed).toBeGreaterThan(0);
  });

  it('the retry delta links exactly its two attempts, in either order', async () => {
    const r = await records('fx-greenfield-compile-retry');
    expect(retryDeltas(r)).toHaveLength(1);
    expect(compareAttempts(r, 1, 2).delta?.informationGain).toBe('PRESENT');
    expect(compareAttempts(r, 2, 1).delta).not.toBeNull();
    expect(compareAttempts(r, 1, 1).delta).toBeNull();
  });

  it('an unrecorded dimension is null with same=null, never true', async () => {
    const r = (await records('fx-greenfield-compile-retry')).filter((x) => !(x.kind === 'prompt.sections' && x.attempt_number === 2));
    const dim = compareAttempts(r, 1, 2).dimensions.find((d) => d.id === 'prompt_sections')!;
    expect(dim.b).toBeNull();
    expect(dim.same).toBeNull();
  });

  it('calls come only from model.result records; a plan exists only when the unit record carries one', async () => {
    const r = await records('fx-greenfield-compile-retry');
    expect(callRows(r).length).toBe(r.filter((x) => x.kind === 'model.result').length);
    expect(planUnits(r)[0]!.planId).toBe('plan-1');
    expect(planUnits(await records('fx-greenfield-success'))[0]!.planId).toBeNull();
  });

  it('no run events means nothing is derived', () => {
    const f = requirementsFacts(null);
    expect(f.derived).toBeNull();
    expect(f.planEvents).toEqual([]);
  });
});
