/**
 * VERIFIED PROJECT MEMORY on the Project page. The renderer decides nothing: every fact, status, reason, count and provenance sentence is a field of Kriya's `execution.preflight`
 * answer (`memory`). These tests pin that the card shows exactly what Kriya reported (including the failure and stale states), claims nothing it was not given, and is absent
 * when an older Kriya reports no memory. The backend (verification, staleness, poisoning rules) is pinned in tests/test_project_memory.py.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ProjectMemory } from '../../../model/kup';
import { renderWorkspaceShell } from '../../testing/harness';
import { ProjectMemoryCard } from './ProjectMemory';

const WS = '/fixture/empty';
const fact = (over: Partial<ProjectMemory['facts'][number]> = {}): ProjectMemory['facts'][number] => ({
  id: 'build_system', kind: 'build_system', label: 'Build system', value: 'Maven', status: 'CURRENT', stale_reason: null,
  evidence: [{ kind: 'file', path: 'pom.xml', sha256: 'a'.repeat(64) }], learned_run_id: '20261106T101010-aaaaaaaa', learned_at: '2026-11-06T10:10:10Z',
  confirmed_runs: 2, reused_runs: 1, why: 'Read from pom.xml, and verified by run 20261106T101010-aaaaaaaa: it ended in success with a passing compile gate.', ...over,
});
const memory = (over: Partial<ProjectMemory> = {}): ProjectMemory => ({
  schema_version: 1, repository: 'r'.repeat(64), store_state: 'OK', store_detail: null,
  counts: { current: 1, stale: 0, rejected_total: 0, learned_last_run: 0, reused_last_run: 1, rejected_last_run: 0 },
  facts: [fact()], rejected: [], runs: [], ...over,
});

describe('the Verified Project Memory card', () => {
  it('shows each fact with its evidence, the run that taught it, how often it was reused, and the counts Kriya reported', () => {
    render(<ProjectMemoryCard memory={memory()} />);
    const counts = screen.getByTestId('memory-counts');
    expect(counts.textContent).toBe('Reused last run 1 · Learned last run 0 · Stale 0 · Rejected 0');
    const row = document.querySelector('[data-fact="build_system"]') as HTMLElement;
    expect(row.getAttribute('data-status')).toBe('CURRENT');
    expect(within(row).getByText('✓ Current')).toBeTruthy();
    expect(row.textContent).toContain('Build system: Maven');
    expect(row.textContent).toContain('evidence pom.xml');
    expect(row.textContent).toContain('learned in run 20261106T101010-aaaaaaaa');
    expect(row.textContent).toContain('reused in 1 later run');
  });

  it('answers "Why does Kriya know this?" with the provenance sentence Kriya wrote, not one it composes', () => {
    render(<ProjectMemoryCard memory={memory()} />);
    expect(screen.getByText('Why does Kriya know this?')).toBeTruthy();
    expect(screen.getByText(/Read from pom\.xml, and verified by run 20261106T101010-aaaaaaaa/)).toBeTruthy();
  });

  it('shows a stale fact as stale with the reason code, and says it is not reused', () => {
    render(<ProjectMemoryCard memory={memory({
      counts: { current: 0, stale: 1, rejected_total: 0, learned_last_run: 0, reused_last_run: 1, rejected_last_run: 0 },
      facts: [fact({ status: 'STALE', stale_reason: 'EVIDENCE_CHANGED' })] })} />);
    const row = document.querySelector('[data-fact="build_system"]') as HTMLElement;
    expect(row.getAttribute('data-status')).toBe('STALE');
    expect(within(row).getByText('↻ Stale')).toBeTruthy();
    expect(row.textContent).toContain('not reused: the file it was learned from has changed (EVIDENCE_CHANGED)');
    expect(within(row).queryByText('✓ Current')).toBeNull();
    expect(screen.getByTestId('memory-counts').textContent).toContain('Stale 1');
  });

  it('shows an unknown stale code literally instead of inventing a meaning', () => {
    render(<ProjectMemoryCard memory={memory({ facts: [fact({ status: 'STALE', stale_reason: 'SOMETHING_NEW' })] })} />);
    expect(document.body.textContent).toContain('not reused: SOMETHING_NEW (SOMETHING_NEW)');
  });

  it('says nothing is remembered yet for an empty usable store, and claims no fact', () => {
    render(<ProjectMemoryCard memory={memory({ store_state: 'ABSENT', facts: [], counts: { current: 0, stale: 0, rejected_total: 0, learned_last_run: 0, reused_last_run: 0, rejected_last_run: 0 } })} />);
    expect(screen.getByText(/Nothing is remembered yet/)).toBeTruthy();
    expect(document.querySelectorAll('[data-fact]').length).toBe(0);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it.each([
    ['STORE_UNREADABLE', 'could not be read'], ['STORE_TOO_LARGE', 'larger than Kriya will read'],
    ['STORE_SCHEMA_UNSUPPORTED', 'different Kriya version'], ['STORE_FOREIGN_REPOSITORY', 'belongs to another repository'],
  ] as const)('reports an unusable store (%s) as such and does not present it as empty', (state, words) => {
    render(<ProjectMemoryCard memory={memory({ store_state: state, facts: [] })} />);
    expect(screen.getByRole('alert').textContent).toContain(words);
    expect(screen.queryByText(/Nothing is remembered yet/)).toBeNull();
  });

  it('lists what was rejected and why, in plain words, without a value', () => {
    render(<ProjectMemoryCard memory={memory({ rejected: [
      { run_id: 'run-9', kind: 'modules', reason: 'UNSAFE_VALUE', at: 't' }, { run_id: 'run-8', kind: 'language', reason: 'RUN_NOT_VERIFIED', at: 't' }] })} />);
    expect(screen.getByText('Rejected · 2')).toBeTruthy();
    expect(document.body.textContent).toContain('the value looked like it could carry a secret, so it was not stored');
    expect(document.body.textContent).toContain('the run did not end in verified success, so nothing was learned');
  });
});

describe('the Project page', () => {
  it('shows the memory Kriya reported in its preflight', async () => {
    renderWorkspaceShell({ fixture: { workspace: WS, execution: { outcome: 'success', memory: memory() } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    const card = await screen.findByRole('region', { name: 'Verified Project Memory' });
    expect(card.textContent).toContain('Build system: Maven');
  });

  it('shows no memory card when Kriya reports none (an older Kriya)', async () => {
    renderWorkspaceShell({ fixture: { workspace: WS, execution: { outcome: 'success' } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    await screen.findByText('What do you want Kriya to do?');
    expect(screen.queryByRole('region', { name: 'Verified Project Memory' })).toBeNull();
  });
});
