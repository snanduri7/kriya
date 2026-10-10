import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { EvidenceRecord } from '../../core';
import { renderModule } from '../../testing/harness';
import { changeOf, changesModule } from '.';

const rec = (payload: Record<string, unknown>): EvidenceRecord => ({ schema: 's', seq: 1, prev: 'p', kind: 'candidate.change', run_id: 'r', provenance: 'OBSERVED', payload, content_digests: {}, blobs: {}, phase_provenance: null }) as EvidenceRecord;
const staged = { decision: 'STAGED', path: 'a/B.java', lines_added: 3, lines_removed: 1 };

describe('Changes: generic change semantics from the recorded payload', () => {
  it('ADDED: the baseline is explicitly new-file semantics, not a fabricated historical file', () => {
    expect(changeOf(rec({ ...staged, created: true, before_digest: null, after_digest: 'sha256:abcdef0123456789' }))).toMatchObject({ kind: 'ADDED', baseline: expect.stringContaining('no baseline exists') });
  });
  it('MODIFIED needs a recorded before digest; DELETED and UNCHANGED likewise name the recorded baseline', () => {
    expect(changeOf(rec({ ...staged, before_digest: 'sha256:11', after_digest: 'sha256:22' })).kind).toBe('MODIFIED');
    expect(changeOf(rec({ ...staged, before_digest: 'sha256:11', after_digest: null, deleted: true })).kind).toBe('DELETED');
    expect(changeOf(rec({ ...staged, before_digest: 'sha256:11', after_digest: 'sha256:11', unchanged: true })).kind).toBe('UNCHANGED');
  });
  it('unknown baseline: UNAVAILABLE, never substituted from the worktree', () => {
    const c = changeOf(rec({ ...staged, before_digest: null, after_digest: 'sha256:22', created: false }));
    expect(c.kind).toBe('UNAVAILABLE');
    expect(c.baseline).toMatch(/never substituted from the current worktree/);
  });
  it('a refused candidate is UNAVAILABLE with the recorded reason and no invented counts', () => {
    expect(changeOf(rec({ decision: 'REFUSED', path: 'x', reason_code: 'PROTOCOL_INVALID', lines_added: 9 }))).toMatchObject({ kind: 'UNAVAILABLE', note: 'refused: PROTOCOL_INVALID', added: null });
  });
  it('RENAMED is never produced: the recorder does not record renames', () => {
    for (const p of [{ created: true }, { deleted: true }, { unchanged: true }, { before_digest: 'sha256:1', after_digest: 'sha256:2' }]) expect(changeOf(rec({ ...staged, ...p })).kind).not.toBe('RENAMED');
  });
  it('rendered alone over fixtures: selecting a file writes only filePath', async () => {
    const { store } = renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1' } });
    const list = await screen.findByRole('list', { name: 'Changed files' });
    within(list).getAllByRole('button')[0]!.click();
    await screen.findByText(/Changes · attempt 1/);
    expect(store.getState().drawerOpen).toBe(false);
  });
  it('says so when no run is selected', async () => {
    renderModule(changesModule);
    expect(await screen.findByText('No run selected')).toBeInTheDocument();
  });
});
