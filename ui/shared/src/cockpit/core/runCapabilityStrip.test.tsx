/** Per-run capability states: each cell is a statement about what the run's own evidence records, names that evidence, and is "not recorded" - never USED / AVAILABLE by default - when the evidence is silent. */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RunCapabilityStrip, runCapabilityCells, type RunCapabilityInput } from './RunCapabilityStrip';

const base = (over: Partial<RunCapabilityInput> = {}): RunCapabilityInput => ({ modelCalls: 4, gates: { total: 4, passed: 4 }, attempts: { opened: 1, concluded: [true] }, authorityRecorded: true, skills: [], knownTargetFiles: 2,
  memoryEntry: null, health: [{ id: 'lsp', label: 'Language server', state: 'UNAVAILABLE', reason_code: 'JDTLS_NOT_CONTAINABLE', detail: 'd', evidence: ['e'] }, { id: 'lookup', label: 'Outward lookup', state: 'NOT_REQUIRED', reason_code: 'LOOKUP_DISABLED', detail: 'd', evidence: ['e'] }], ...over });
const byId = (input: RunCapabilityInput) => Object.fromEntries(runCapabilityCells(input).map((c) => [c.id, c]));

describe('runCapabilityCells', () => {
  it('states what the evidence records, with its evidence named', () => {
    const c = byId(base({ skills: ['Java', 'Spring'], memoryEntry: { run_id: 'r', at: 't', outcome: 'learned', learned: ['a'], refreshed: ['b'], reused: ['a', 'b', 'c'], rejected: 1 } }));
    expect(c.model).toMatchObject({ state: 'USED', text: '4 requests answered', evidence: 'model.response records' });
    expect(c.code_intelligence).toMatchObject({ state: 'USED', text: '2 files located by the structural index' });
    expect(c.skills).toMatchObject({ state: 'USED', text: 'Java, Spring' });
    expect(c.security).toMatchObject({ state: 'ACTIVE', evidence: 'authority.snapshot record' });
    expect(c.verify).toMatchObject({ state: 'USED', text: '4 of 4 recorded checks passed' });
    expect(c.recovery).toMatchObject({ state: 'NOT_REQUIRED', text: 'the first attempt was the only attempt' });
    expect(c.memory).toMatchObject({ state: 'USED', text: 'reused 3 · learned 2 · rejected 1' });
  });
  it('carries the language server and lookup as Kriya\'s project health states, and never as USED', () => {
    const c = byId(base());
    expect(c.lsp).toMatchObject({ state: 'UNAVAILABLE', text: 'JDTLS_NOT_CONTAINABLE' });
    expect(c.lookup).toMatchObject({ state: 'NOT_REQUIRED', text: 'LOOKUP_DISABLED' });
    expect(byId(base({ health: [{ id: 'lookup', label: 'Outward lookup', state: 'AVAILABLE', reason_code: 'LOOKUP_ENABLED_LOCAL_ONLY', detail: 'd', evidence: ['e'] }] })).lookup).toMatchObject({ state: 'UNKNOWN', text: 'enabled; no lookup is recorded in run evidence' });
  });
  it('shows recovery as used with each attempt\'s outcome when the run needed more than one attempt', () => {
    expect(byId(base({ attempts: { opened: 2, concluded: [false, true] } })).recovery).toMatchObject({ state: 'USED', text: '2 attempts (#1 failed, #2 succeeded)' });
  });
  it('says "not recorded" for anything the evidence is silent about instead of claiming it', () => {
    const c = byId(base({ modelCalls: null, gates: null, attempts: null, authorityRecorded: null, skills: null, knownTargetFiles: null, health: [] }));
    for (const id of ['model', 'code_intelligence', 'skills', 'security', 'verify', 'recovery']) expect(c[id]?.state, id).toBe('UNKNOWN');
    expect(c['lsp']).toBeUndefined();
    expect(c['memory']).toBeUndefined();
  });
  it('a single failed attempt is not described as "not required"', () => {
    expect(byId(base({ attempts: { opened: 1, concluded: [false] } }))['recovery']?.text).toBe('one attempt, which failed (no second attempt recorded)');
  });
  it('renders each cell with its state word and a title naming the evidence', () => {
    render(<RunCapabilityStrip cells={runCapabilityCells(base())} />);
    const model = document.querySelector('[data-capability="model"]') as HTMLElement;
    expect(model.textContent).toContain('Used');
    expect(model.title).toBe('Local model: 4 requests answered (evidence: model.response records)');
    expect(document.querySelector('[data-capability="lsp"]')!.textContent).toContain('Unavailable');
  });
  it('renders nothing for no cells', () => {
    expect(render(<RunCapabilityStrip cells={[]} />).container.firstChild).toBeNull();
  });
});
