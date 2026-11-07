/**
 * B1 (owner walkthrough 2026-11-07): the Project folder row used to say "Needs recovery - the last run left the folder mid-change" for EVERY status other than CLEAN, including RUN_ACTIVE
 * (a run in progress). Every status Kriya reports now has its own row, and a status the table does not know is never presented as a recovery. That the table covers every backend status is pinned
 * on the Python side (tests/test_ui_folder_facts_parity.py): ui/shared may not read the repository.
 */
import { describe, expect, it } from 'vitest';
import { FOLDER_FACTS, folderFact } from './Readiness';

describe('folder facts', () => {
  it('CLEAN is ready and RUN_ACTIVE is a run in progress, never a recovery', () => {
    expect(folderFact('CLEAN')).toMatchObject({ status: 'Ready', tone: 'ok' });
    const active = folderFact('RUN_ACTIVE');
    expect(active.status).toBe('A run is in progress');
    expect(active.tone).toBe('info');
    expect(JSON.stringify(active)).not.toMatch(/recover|mid-change/i);
  });
  it('only a status that really means a recovery says "Needs recovery"', () => {
    const recoveries = Object.entries(FOLDER_FACTS).filter(([, v]) => v.status === 'Needs recovery').map(([k]) => k).sort();
    expect(recoveries).toEqual(['COMPLETE_PARTIAL_REQUIRED', 'RECOVERY_AVAILABLE']);
    expect(folderFact('MANUAL_ACTION_REQUIRED').status).toBe('Needs your attention');
  });
  it('an unknown status is shown as unrecognised (neutral), with the raw word - it does not inherit a recovery message', () => {
    const unknown = folderFact('SOMETHING_NEW');
    expect(unknown).toMatchObject({ status: 'State not recognised', tone: 'neutral' });
    expect(unknown.note).toContain('SOMETHING_NEW');
    expect(unknown.status).not.toBe('Needs recovery');
  });
});
