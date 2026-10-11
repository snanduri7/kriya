import { describe, expect, it } from 'vitest';
import type { RunSummary } from '../../../model/kup';
import { NO_FILTER, filterRuns, isFiltering, statusOf, timeOf } from './filter';

const run = (over: Partial<RunSummary>): RunSummary => ({ run_id: 'r', timestamp: '2026-10-10 09:00:00', goal: 'g', status: 'success', ...over }) as RunSummary;
const NOW = Date.parse('2026-10-11T12:00:00');

describe('statusOf / timeOf', () => {
  it('maps Kriya\'s statuses and treats everything else as not recorded', () => {
    expect([ 'success', 'failure', 'in_progress', null, undefined, 'weird', '' ].map((status) => statusOf({ status } as RunSummary))).toEqual(['success', 'failure', 'unfinished', 'not_recorded', 'not_recorded', 'not_recorded', 'not_recorded']);
  });
  it('parses stored and ISO timestamps and refuses the rest', () => {
    expect(timeOf({ timestamp: '2026-10-10 09:00:00' })).toBe(Date.parse('2026-10-10T09:00:00'));
    expect(timeOf({ timestamp: '2026-10-10T09:00:00Z' })).toBe(Date.parse('2026-10-10T09:00:00Z'));
    for (const bad of [null, undefined, '', '   ', 'yesterday', '2026-13-45 99:99:99']) expect(timeOf({ timestamp: bad } as RunSummary), String(bad)).toBeNull();
  });
});

describe('filterRuns', () => {
  const rows = [run({ run_id: 'a', goal: 'Add clamp', status: 'success', timestamp: '2026-10-11 08:00:00' }), run({ run_id: 'b', goal: 'Fix PARSER', status: 'failure', timestamp: '2026-10-08 08:00:00' }),
    run({ run_id: 'c', goal: 'Docs', status: 'in_progress', timestamp: '2026-09-01 08:00:00' }), run({ run_id: 'd', goal: 'No time', status: null, timestamp: null }), run({ run_id: 'e', goal: 'Odd', status: 'weird', timestamp: 'garbage' })];
  const ids = (f: Partial<typeof NO_FILTER>) => filterRuns(rows, { ...NO_FILTER, ...f }, NOW).runs.map((r) => r.run_id);
  it('no filter returns every row, in order, and reports nothing untimed', () => {
    expect(filterRuns(rows, NO_FILTER, NOW)).toEqual({ runs: rows, untimed: 0 });
    expect(isFiltering(NO_FILTER)).toBe(false);
  });
  it('text matches goal, status and id, case-insensitively, and trims', () => {
    expect(ids({ query: ' parser ' })).toEqual(['b']);
    expect(ids({ query: 'FAILURE' })).toEqual(['b']);
    expect(ids({ query: 'e' }).sort()).toEqual(['a', 'b', 'c', 'd', 'e']); // ids a..e and words contain e
    expect(ids({ query: 'zzz' })).toEqual([]);
  });
  it('status filters by Kriya\'s status, including "not recorded"', () => {
    expect(ids({ status: 'success' })).toEqual(['a']);
    expect(ids({ status: 'unfinished' })).toEqual(['c']);
    expect(ids({ status: 'not_recorded' })).toEqual(['d', 'e']);
  });
  it('time ranges are relative to now; rows without a recorded time are COUNTED, not matched', () => {
    expect(filterRuns(rows, { ...NO_FILTER, range: 'today' }, NOW)).toEqual({ runs: [rows[0]], untimed: 2 });
    expect(ids({ range: '7d' })).toEqual(['a', 'b']);
    expect(ids({ range: '30d' })).toEqual(['a', 'b']);
    expect(filterRuns(rows, { ...NO_FILTER, range: '7d' }, NOW).untimed).toBe(2);
  });
  it('filters combine (AND)', () => {
    expect(ids({ status: 'failure', range: '7d', query: 'fix' })).toEqual(['b']);
    expect(ids({ status: 'success', range: '7d', query: 'fix' })).toEqual([]);
  });
  it('a timestamp far in the future is not "recent"', () => {
    expect(filterRuns([run({ timestamp: '2099-01-01 00:00:00' })], { ...NO_FILTER, range: '30d' }, NOW).runs).toEqual([]);
  });
  it('property: a filter never invents or duplicates a row, and filtering by nothing is the identity (seeded fuzz)', () => {
    let seed = 7;
    const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
    const statuses = ['success', 'failure', 'in_progress', null, 'x'];
    for (let round = 0; round < 300; round += 1) {
      const data = Array.from({ length: Math.floor(rand() * 20) }, (_, i) => run({ run_id: `r${i}`, goal: ['alpha', 'beta', 'γ', '', null][Math.floor(rand() * 5)] as string, status: statuses[Math.floor(rand() * 5)] as string,
        timestamp: rand() < 0.2 ? null : `2026-${String(1 + Math.floor(rand() * 12)).padStart(2, '0')}-10 08:00:00` }));
      const f = { query: ['', 'a', 'γ', 'zzz'][Math.floor(rand() * 4)]!, status: (['all', 'success', 'failure', 'unfinished', 'not_recorded'] as const)[Math.floor(rand() * 5)]!, range: (['any', 'today', '7d', '30d'] as const)[Math.floor(rand() * 4)]! };
      const out = filterRuns(data, f, NOW);
      expect(out.runs.every((r) => data.includes(r))).toBe(true);
      expect(new Set(out.runs).size).toBe(out.runs.length);
      expect(out.runs.length).toBeLessThanOrEqual(data.length);
      if (!isFiltering(f)) expect(out.runs).toEqual(data);
      if (f.range === 'any') expect(out.untimed).toBe(0);
    }
  });
});
