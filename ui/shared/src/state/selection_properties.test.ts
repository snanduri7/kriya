/** GUI v1 hardening (Phase F/M): property tests of the ONE shared selection. Seeded, bounded and local: 4000 random action sequences, every step checked against the invariants
 * the cross-module consistency depends on (a new run or unit invalidates everything finer; a live selection is always the selected run; nothing points outside the selected run). */
import { describe, expect, it } from 'vitest';
import { initialSelection, selectionReducer, type Selection, type SelectionAction } from './selection';

function lcg(seed: number) { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 2 ** 32; }; }
const RUNS = ['run-a', 'run-b', 'run-c', '20261011T094647-bc1ca4c4', null] as const;
const TEXT = ['1', '2', '3', 'u1', 'u2', 'x', '', 'a'.repeat(300), '‮', null];

function action(rand: () => number): SelectionAction {
  const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(rand() * xs.length)]!;
  switch (Math.floor(rand() * 15)) {
    case 0: return { type: 'selectRun', runId: pick(RUNS) };
    case 1: return { type: 'selectLiveRun', launchId: pick(['20261011T094647-bc1ca4c4', 'run-a']) };
    case 2: return { type: 'selectAttempt', attempt: pick(TEXT) };
    case 3: return { type: 'selectEvent', eventIndex: pick([0, 1, 5, null]) };
    case 4: return { type: 'selectUnit', unitId: pick(TEXT) };
    case 5: return { type: 'selectEventSeq', eventSeq: pick([1, 2, 99, null]) };
    case 6: return { type: 'selectGate', gateId: pick(['1:compile', '2:test', null]) };
    case 7: return { type: 'selectFile', filePath: pick(['src/a.py', 'b/ü.java', null]) };
    case 8: return { type: 'centralTab', tab: pick(['overview', 'changes', 'validation', 'evidence']) };
    case 9: return { type: 'phaseFilter', phase: pick(['planning', null]) };
    case 10: return { type: 'compareAttempt', attempt: pick(TEXT) };
    case 11: return { type: 'toggleDrawer' };
    case 12: return { type: 'bottomTab', tab: pick(['why', 'diff']) };
    case 13: return { type: 'selectTimelineNode', node: { unitId: pick(TEXT), attempt: pick(TEXT), eventSeq: pick([1, 2, null]), gateId: pick(['1:compile', null]) } };
    default: return { type: 'reset' };
  }
}
const FINER: Array<keyof Selection> = ['attempt', 'eventIndex', 'comparisonPath', 'unitId', 'eventSeq', 'gateId', 'filePath', 'phaseFilter', 'compareAttempt'];

describe('the shared selection invariants hold across 4000 random sequences', () => {
  it('holds every invariant after every step', () => {
    for (let seed = 1; seed <= 4000; seed += 1) {
      const rand = lcg(seed);
      let state: Selection = { ...initialSelection };
      for (let step = 0; step < 25; step += 1) {
        const a = action(rand);
        const before = state;
        state = selectionReducer(before, a);
        const where = `seed ${seed} step ${step} ${JSON.stringify(a)}`;
        // a live selection is always the selected run
        expect(state.liveLaunchId === null || state.liveLaunchId === state.runId, where).toBe(true);
        if (state.runId === null) expect(state.liveLaunchId, where).toBeNull();
        if (a.type === 'selectRun' && (a.runId !== before.runId || before.liveLaunchId !== null)) {
          expect(state.liveLaunchId, where).toBeNull();
          for (const key of FINER) expect(state[key], `${where} ${key}`).toBeNull(); // nothing from the previous run survives
        }
        if (a.type === 'selectLiveRun') {
          expect(state.runId, where).toBe(a.launchId);
          expect(state.liveLaunchId, where).toBe(a.launchId);
          if (before.runId !== a.launchId || before.liveLaunchId !== a.launchId) for (const key of FINER) expect(state[key], `${where} ${key}`).toBeNull();
        }
        if (a.type === 'selectUnit' && a.unitId !== before.unitId) {
          for (const key of ['attempt', 'eventIndex', 'eventSeq', 'gateId', 'filePath', 'compareAttempt'] as const) expect(state[key], `${where} ${key}`).toBeNull();
        }
        if (a.type === 'selectFile') expect(state.drawerOpen, where).toBe(before.drawerOpen); // selecting a file never opens the drawer
        if (a.type === 'phaseFilter') { for (const key of ['runId', 'attempt', 'unitId', 'gateId', 'filePath', 'eventSeq'] as const) expect(state[key], `${where} ${key}`).toBe(before[key]); } // a filter never changes a selection
        if (a.type === 'centralTab') expect({ ...state, centralTab: before.centralTab }, where).toEqual(before); // switching tabs changes nothing else
        if (a.type === 'reset') expect(state, where).toEqual(initialSelection);
        if (a.type === 'selectTimelineNode') { expect(state.unitId, where).toBe(a.node.unitId); expect(state.filePath, where).toBeNull(); expect(state.runId, where).toBe(before.runId); }
      }
    }
  });

  it('a compared attempt cannot survive a change of run or unit (it cannot point outside the selected run)', () => {
    let s = selectionReducer(initialSelection, { type: 'selectRun', runId: 'run-a' });
    s = selectionReducer(s, { type: 'selectAttempt', attempt: '1' });
    s = selectionReducer(s, { type: 'compareAttempt', attempt: '2' });
    expect(s.compareAttempt).toBe('2');
    expect(selectionReducer(s, { type: 'selectRun', runId: 'run-b' }).compareAttempt).toBeNull();
    expect(selectionReducer(s, { type: 'selectLiveRun', launchId: 'run-b' }).compareAttempt).toBeNull();
    expect(selectionReducer(s, { type: 'selectUnit', unitId: 'other' }).compareAttempt).toBeNull();
  });
});
