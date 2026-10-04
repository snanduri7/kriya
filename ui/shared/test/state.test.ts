import { describe, expect, it } from 'vitest';
import { initialSelection, selectionReducer } from '../src/state/selection';
import { GenerationCounter, applyFailure, applyResponse, emptySlot, startRequest } from '../src/state/requests';

describe('selection reducer (P-23)', () => {
  it('a new run clears attempt, event and comparison but keeps the chosen tab', () => {
    let s = selectionReducer(initialSelection, { type: 'inspectorTab', tab: 'gates' });
    s = selectionReducer(s, { type: 'selectRun', runId: 'r1' });
    s = selectionReducer(s, { type: 'selectAttempt', attempt: 'attempt 1' });
    s = selectionReducer(s, { type: 'selectEvent', eventIndex: 3 });
    s = selectionReducer(s, { type: 'selectComparison', path: 'a.py' });
    expect(s.drawerOpen).toBe(true); expect(s.drawerTab).toBe('diff');
    s = selectionReducer(s, { type: 'selectRun', runId: 'r2' });
    expect(s).toMatchObject({ runId: 'r2', attempt: null, eventIndex: null, comparisonPath: null, inspectorTab: 'gates' });
  });
  it('narrowing to an attempt drops the event selection; widening back to all events keeps it', () => {
    const picked = selectionReducer({ ...initialSelection, runId: 'r1', eventIndex: 3 }, { type: 'selectAttempt', attempt: 'attempt 2' });
    expect(picked).toMatchObject({ attempt: 'attempt 2', eventIndex: null });
    const widened = selectionReducer({ ...picked, eventIndex: 5 }, { type: 'selectAttempt', attempt: null });
    expect(widened).toMatchObject({ attempt: null, eventIndex: 5 });
  });
  it('selecting the same run is a no-op (keeps the event selection)', () => {
    const s1 = selectionReducer({ ...initialSelection, runId: 'r1', eventIndex: 2 }, { type: 'selectRun', runId: 'r1' });
    expect(s1.eventIndex).toBe(2);
  });
});

describe('request generations (P-32)', () => {
  it('discards a stale response and clears currentness on failure', () => {
    const c = new GenerationCounter();
    let slot = emptySlot<string>();
    const g1 = c.next('detail'); slot = startRequest(slot, g1);
    const g2 = c.next('detail'); slot = startRequest(slot, g2);
    slot = applyResponse(slot, g1, c, 'detail', 'old', 't1');
    expect(slot.value).toBeNull(); // stale g1 discarded
    slot = applyResponse(slot, g2, c, 'detail', 'new', 't2');
    expect(slot).toMatchObject({ value: 'new', current: true, observedAt: 't2' });
    const g3 = c.next('detail'); slot = startRequest(slot, g3);
    slot = applyFailure(slot, g3, c, 'detail', { code: 'STORE_BUSY', message: 'wal' });
    expect(slot.current).toBe(false); expect(slot.value).toBe('new'); expect(slot.error?.code).toBe('STORE_BUSY');
  });
});
