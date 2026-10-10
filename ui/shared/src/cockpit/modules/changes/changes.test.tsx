import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ChangeRow } from '../../core';
import { createFixtureProvider } from '../../providers/fixture';
import { renderModule } from '../../testing/harness';
import { baselineText, changesModule, contentState, viewPart } from '.';
import { parseUnified } from './DiffView';

const row = (over: Partial<ChangeRow>): ChangeRow => ({ seq: 1, attempt: 1, path: 'a/B.java', kind: 'MODIFIED', decision: 'STAGED', reasonCode: null, beforeDigest: 'sha256:abcdef0123', afterDigest: 'sha256:1234567890', linesAdded: 1, linesRemoved: 0, kept: ['diff', 'after', 'before'], digestOnly: [], ...over });

describe('Changes: what the user is told about the baseline', () => {
  it('ADDED is new-file semantics; MODIFIED/DELETED/UNCHANGED name the recorded baseline; unknown is unavailable and never the worktree', () => {
    expect(baselineText(row({ kind: 'ADDED' }))).toMatch(/new file: no baseline exists/);
    for (const kind of ['MODIFIED', 'DELETED', 'UNCHANGED'] as const) expect(baselineText(row({ kind }))).toBe('recorded baseline abcdef0123');
    expect(baselineText(row({ kind: 'UNAVAILABLE' }))).toMatch(/never substituted from the current worktree/);
    expect(baselineText(row({ kind: 'UNAVAILABLE', decision: 'REFUSED' }))).toMatch(/no candidate was staged/);
  });
});

describe('Changes: the content state is decided from what is known, with the precise reason', () => {
  it('a staged change shows its diff, a refused one the proposed content', () => {
    expect([viewPart(row({})), viewPart(row({ kept: ['proposed'], decision: 'REFUSED' })), viewPart(row({ kept: [], digestOnly: [] }))]).toEqual(['diff', 'proposed', null]);
  });
  it.each([
    ['authorized and kept', row({}), true, 'full', 'askable'],
    ['not authorized by Kriya', row({}), false, 'full', 'denied'],
    ['digest-only capture', row({ kept: [], digestOnly: ['diff'] }), true, 'digest_only', 'digest_only'],
    ['digest-only even when the capture mode is not reported', row({ kept: [], digestOnly: ['diff', 'after'] }), true, undefined, 'digest_only'],
    ['nothing kept', row({ kept: [], digestOnly: [] }), true, 'full', 'none'],
  ])('%s', (_name, r, authorized, capture, expected) => { expect(contentState(r, authorized, capture).state).toBe(expected); });
  it('an unchanged file says there is no diff, not that content is unavailable', () => {
    expect(contentState(row({ kind: 'UNCHANGED', kept: [], digestOnly: [] }), true, 'full')).toMatchObject({ state: 'none', why: expect.stringMatching(/identical to the baseline/) });
  });
});

describe('Changes: Mode B (metadata) is complete without content', () => {
  it('content not authorized (the production default): the list, baseline and digests are shown and no content is requested', async () => {
    const provider = createFixtureProvider();
    const calls: string[] = [];
    const real = provider.evidenceChangeContent.bind(provider);
    provider.evidenceChangeContent = (...a) => { calls.push('content'); return real(...a); };
    const { store } = renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1' }, provider });
    const list = await screen.findByRole('list', { name: 'Changed files' });
    expect(within(list).getAllByText('MODIFIED')).toHaveLength(2);
    fireEvent.click(within(list).getAllByRole('button')[0]!);
    expect(await screen.findByText('recorded baseline 0707070707')).toBeInTheDocument();
    expect(screen.getByText(/Kriya's content visibility policy does not authorize/)).toBeInTheDocument();
    expect(store.getState().filePath).toBe('src/main/CharacterUtil.java');
    expect(store.getState().drawerOpen).toBe(false);
    expect(calls).toEqual([]);
  });
  it('digest-only evidence says the run kept digests, not content', async () => {
    renderModule(changesModule, { runId: 'fx-digest-only', selection: { attempt: '1', filePath: 'docs/guide.md' }, fixture: { evidenceContent: true } });
    expect(await screen.findByText(/kept digests of file content, not the content itself/)).toBeInTheDocument();
  });
  it('pruned, pre-recorder and capture-off runs are their own states', async () => {
    for (const [id, title] of [['fx-pruned', 'Evidence pruned by retention'], ['fx-pre-recorder', 'Recorded before the recorder'], ['fx-capture-off', 'Capture was off']] as const) {
      const { unmount } = renderModule(changesModule, { runId: id });
      expect(await screen.findByText(title)).toBeInTheDocument();
      unmount();
    }
  });
  it('a no-change outcome is UNCHANGED with no diff offered', async () => {
    renderModule(changesModule, { runId: 'fx-no-change', selection: { attempt: '1', filePath: 'src/Util.java' }, fixture: { evidenceContent: true } });
    expect(await screen.findByText(/identical to the baseline: there is no diff/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show diff' })).toBeNull();
  });
});

describe('Changes: Mode A (authorized content), lazy and per file', () => {
  it('nothing is requested until the user asks; then the recorded diff is shown with additions, deletions and provenance', async () => {
    const provider = createFixtureProvider({ evidenceContent: true });
    const asked: Array<[number, string]> = [];
    const real = provider.evidenceChangeContent.bind(provider);
    provider.evidenceChangeContent = (id, q, s) => { asked.push([q.seq, q.part]); return real(id, q, s); };
    renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1', filePath: 'src/main/CharacterUtil.java' }, provider });
    const show = await screen.findByRole('button', { name: 'Show diff' });
    expect(asked).toEqual([]);
    fireEvent.click(show);
    const diff = await screen.findByLabelText('Unified diff');
    expect(asked).toHaveLength(1);
    expect(asked[0]![1]).toBe('diff');
    expect(diff.querySelectorAll('[data-diff-row="add"]').length).toBeGreaterThan(0);
    expect(diff.querySelectorAll('[data-diff-row="del"]').length).toBeGreaterThan(0);
    expect(diff.querySelector('[data-token="number"]')).not.toBeNull();
    const provenance = screen.getByLabelText('Content provenance');
    expect(provenance).toHaveTextContent(/diff · \d+ bytes · utf-8 · digest verified/);
    expect(provenance).toHaveTextContent('src/main/CharacterUtil.java');
  });
  it('a refused read from Kriya (policy) is shown as denied, whatever the capability said', async () => {
    const provider = createFixtureProvider({ evidenceContent: true });
    provider.evidenceChangeContent = async () => ({ ok: false, error: { code: 'EVIDENCE_CONTENT_POLICY', message: 'policy is off', extra: { reason: 'content_visibility_off' } } });
    renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1', filePath: 'src/main/CharacterUtil.java' }, provider });
    fireEvent.click(await screen.findByRole('button', { name: 'Show diff' }));
    expect(await screen.findByText('Content not authorized')).toBeInTheDocument();
    expect(screen.queryByLabelText('Unified diff')).toBeNull();
  });
  it('too large, digest-only (as Kriya reports it) and store-does-not-verify each have their own text', async () => {
    const cases: Array<[string, Record<string, unknown>, RegExp]> = [
      ['EVIDENCE_BLOB_TOO_LARGE', { bytes_total: 2000000 }, /2000000 bytes; the display limit is 1 MiB/],
      ['EVIDENCE_CONTENT_UNAVAILABLE', { reason: 'digest_only' }, /kept a digest of this content, not the content itself/],
      ['EVIDENCE_VERIFY_FAILED', { status: 'BLOB_CORRUPT' }, /does not verify \(BLOB_CORRUPT\)/],
    ];
    for (const [code, extra, text] of cases) {
      const provider = createFixtureProvider({ evidenceContent: true });
      provider.evidenceChangeContent = async () => ({ ok: false, error: { code, message: 'm', extra } });
      const { unmount } = renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1', filePath: 'src/main/CharacterUtil.java' }, provider });
      fireEvent.click(await screen.findByRole('button', { name: 'Show diff' }));
      expect(await screen.findByText(text)).toBeInTheDocument();
      unmount();
    }
  });
  it('content is rendered as text: markup and control characters in a diff cannot become elements', async () => {
    const provider = createFixtureProvider({ evidenceContent: true });
    const real = provider.evidenceChangeContent.bind(provider);
    provider.evidenceChangeContent = async (id, q, s) => { const r = await real(id, q, s); return r.ok ? { ...r, data: { ...r.data, content: '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-ok\n+<img src=x onerror=alert(1)>\u0007\u001b[31m\n' } } : r; };
    renderModule(changesModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1', filePath: 'src/main/CharacterUtil.java' }, provider });
    fireEvent.click(await screen.findByRole('button', { name: 'Show diff' }));
    const diff = await screen.findByLabelText('Unified diff');
    expect(diff.querySelector('img')).toBeNull();
    expect(diff.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(diff.textContent).toContain('\\u0007');
  });
  it('follows the selected attempt', async () => {
    const { store } = renderModule(changesModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' }, fixture: { evidenceContent: true } });
    await screen.findByText(/Changes · attempt 1/);
    store.dispatch({ type: 'selectTimelineNode', node: { unitId: 'unit-1', attempt: '2', eventSeq: null, gateId: null } });
    await waitFor(() => expect(screen.getByText(/Changes · attempt 2/)).toBeInTheDocument());
  });
});

describe('unified diff parsing', () => {
  it('headers, hunks, additions, deletions and context, as recorded', () => {
    expect(parseUnified('--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n\\ No newline at end of file\n').map((r) => r.kind)).toEqual(['file', 'file', 'hunk', 'context', 'del', 'add', 'note']);
    expect(parseUnified('+a\n-b\n').map((r) => r.text)).toEqual(['a', 'b']);
  });
});

it('says so when no run is selected', async () => {
  renderModule(changesModule);
  expect(await screen.findByText('No run selected')).toBeInTheDocument();
});
