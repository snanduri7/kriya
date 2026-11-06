/** What Verified Project Memory did for one recorded run (the Overview's line): Kriya's own per-run entry, never a presumption. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ProjectMemory } from '../../model/kup';
import { RunMemoryLine } from './RunMemoryLine';

const memory = (over: Partial<ProjectMemory> = {}): ProjectMemory => ({
  schema_version: 1, repository: 'r', store_state: 'OK', store_detail: null, rejected: [], runs: [],
  counts: { current: 1, stale: 0, rejected_total: 0, learned_last_run: 0, reused_last_run: 1, rejected_last_run: 0 },
  facts: [{ id: 'build_system', kind: 'build_system', label: 'Build system', value: 'Maven', status: 'CURRENT', stale_reason: null, evidence: [], learned_run_id: 'run-1', learned_at: 't', confirmed_runs: 1, reused_runs: 1, why: 'w' }], ...over });

describe('what memory did for one recorded run', () => {
  const run = (over: Partial<ProjectMemory['runs'][number]> = {}): ProjectMemory['runs'][number] =>
    ({ run_id: 'run-2', at: 't', outcome: 'learned', learned: [], refreshed: ['build_system'], reused: ['build_system'], rejected: 0, ...over });

  it('names the facts a run reused and confirmed, from the entry Kriya recorded for exactly that run', () => {
    render(<RunMemoryLine memory={memory({ runs: [run({ run_id: 'run-1', reused: [] }), run()] })} runId="run-2" />);
    expect(screen.getByTestId('run-memory').textContent).toContain('Reused 1 verified fact from earlier runs (Build system).');
    expect(screen.getByTestId('run-memory').textContent).toContain('Learned or confirmed 1 (Build system).');
  });

  it('says a failed run learned nothing and why, and counts what was rejected', () => {
    render(<RunMemoryLine memory={memory({ runs: [run({ outcome: 'rejected', refreshed: [], reused: [], rejected: 3 })] })} runId="run-2" />);
    const text = screen.getByTestId('run-memory').textContent ?? '';
    expect(text).toContain('Reused 0 facts.');
    expect(text).toContain('did not end in verified success, so nothing was kept');
    expect(text).toContain('Rejected 3.');
  });

  it('shows nothing for a run Kriya recorded no memory entry for, and nothing when memory is absent', () => {
    const a = render(<RunMemoryLine memory={memory({ runs: [run()] })} runId="some-other-run" />);
    expect(a.container.firstChild).toBeNull();
    a.unmount();
    const b = render(<RunMemoryLine memory={undefined} runId="run-2" />);
    expect(b.container.firstChild).toBeNull();
  });
});
