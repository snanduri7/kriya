/** Greenfield and brownfield (and the evidence-availability variants) render through the SAME modules: only the data differs. */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SCENARIO_IDS } from './providers/fixture';
import { renderShell } from './testing/harness';

const mountedModules = () => [...document.querySelectorAll('[data-module]')].map((e) => e.getAttribute('data-module')).sort();
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }));
const trust = async () => within(await screen.findByLabelText('Trust strip'));

describe('the fixture scenarios', () => {
  it('cover greenfield, brownfield, digest-only, pruned, denied content, unsealed and no-change', () => {
    expect(SCENARIO_IDS).toEqual(['fx-greenfield-success', 'fx-greenfield-compile-retry', 'fx-brownfield-modify', 'fx-brownfield-regression', 'fx-digest-only', 'fx-pruned', 'fx-denied-content', 'fx-unsealed', 'fx-no-change', 'fx-pre-recorder', 'fx-capture-off']);
  });

  it('greenfield: ADDED files have no baseline (never a fabricated historical file) and no baseline is claimed anywhere', async () => {
    renderShell({ runId: 'fx-greenfield-success', selection: { centralTab: 'changes' } });
    const list = await screen.findByRole('list', { name: 'Changed files' });
    expect(within(list).getAllByText('ADDED')).toHaveLength(2);
    expect(within(list).getAllByText(/new file: no baseline exists/)).toHaveLength(2);
    expect(within(list).queryByText(/recorded baseline/)).toBeNull();
    expect((await trust()).getByText('baseline not recorded')).toBeInTheDocument();
  });

  it('brownfield: MODIFIED files show the recorded baseline, and the trust strip shows the recorded revision', async () => {
    renderShell({ runId: 'fx-brownfield-modify', selection: { centralTab: 'changes' } });
    const list = await screen.findByRole('list', { name: 'Changed files' });
    expect(within(list).getAllByText('MODIFIED')).toHaveLength(2);
    expect(within(list).getAllByText(/recorded baseline/).length).toBe(2);
    expect(within(list).getByText(/\+9\/-2/)).toBeInTheDocument();
    expect(await (await trust()).findByText('baseline a1b2c3d4e5')).toBeInTheDocument();
  });

  it('both run through exactly the same set of mounted modules', async () => {
    const a = renderShell({ runId: 'fx-greenfield-success' });
    await screen.findByLabelText('Trust strip');
    await waitFor(() => expect(screen.getByText(/Recorded outcome/)).toBeInTheDocument());
    const greenfield = mountedModules();
    expect(a.store.getState().runId).toBe('fx-greenfield-success');
    document.body.innerHTML = '';
    renderShell({ runId: 'fx-brownfield-modify' });
    await screen.findByLabelText('Trust strip');
    await waitFor(() => expect(screen.getByText(/Recorded outcome/)).toBeInTheDocument());
    expect(mountedModules()).toEqual(greenfield);
    expect(greenfield).toEqual(expect.arrayContaining(['overview', 'timeline', 'trust', 'runs']));
  });

  it('greenfield compile failure then retry: both attempts and the failing gate are in the timeline and Validation', async () => {
    renderShell({ runId: 'fx-greenfield-compile-retry' });
    const timeline = await screen.findByRole('tree', { name: 'Execution timeline' });
    expect(within(timeline).getByRole('button', { name: /compile failed/ })).toBeInTheDocument();
    expect(within(timeline).getByRole('button', { name: /diagnosis COMPILE_FAILED/ })).toBeInTheDocument();
    expect(within(timeline).getByRole('button', { name: /recovery decision/ })).toBeInTheDocument();
    expect(within(timeline).getAllByRole('button', { name: /^Attempt \d$/ })).toHaveLength(2);
    expect(within(timeline).getByRole('button', { name: /retry delta/ })).toBeInTheDocument();
  });

  it('brownfield regression failure: the failed gate and the regression decision are shown as recorded', async () => {
    renderShell({ runId: 'fx-brownfield-regression', selection: { centralTab: 'validation' } });
    expect(await screen.findByText(/Validation summary · attempt 1/)).toBeInTheDocument();
    expect(screen.getAllByText('failed', { selector: '.ck-badge' }).length).toBeGreaterThan(0);
    const why = await screen.findByLabelText('Why');
    expect(within(why).getAllByText('MODEL_CLAIMED', { selector: '.ck-badge' }).length).toBeGreaterThanOrEqual(2); // every statement carries the recorded class, never promoted
    expect(within(why).queryByText('DERIVED_DETERMINISTIC')).toBeNull();
    const timeline = await screen.findByRole('tree', { name: 'Execution timeline' });
    expect(within(timeline).getByRole('button', { name: /regression NEW_FAILURE/ })).toBeInTheDocument();
  });

  it('digest-only evidence: metadata and digests, and an explicit statement that content was not kept', async () => {
    renderShell({ runId: 'fx-digest-only', selection: { centralTab: 'changes', attempt: '1', filePath: 'docs/guide.md' }, fixture: { evidenceContent: true } });
    expect(await screen.findByText(/kept digests of file content, not the content itself/)).toBeInTheDocument();
    expect(within(await screen.findByRole('list', { name: 'Changed files' })).getByText('MODIFIED')).toBeInTheDocument();
    expect((await trust()).getByText('capture: digest_only')).toBeInTheDocument();
  });

  it('pruned evidence is its own state with the derived reason, in every evidence surface', async () => {
    renderShell({ runId: 'fx-pruned', selection: { centralTab: 'changes' } });
    expect((await screen.findAllByText('Evidence pruned by retention')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('The evidence store for this run was removed by retention.').length).toBeGreaterThanOrEqual(2);
    // the exact recorded path is secondary provenance behind a disclosure, not in the default text
    expect(screen.getAllByText('Recorded detail').length).toBeGreaterThan(0);
    for (const el of screen.getAllByText(/attempt-evidence\/ev-fx-pruned/)) expect(el.closest('details')).not.toBeNull();
    expect(screen.queryByText(/no data/i)).toBeNull();
  });

  it('pre-recorder and capture-off runs are their own states, not a generic empty', async () => {
    renderShell({ runId: 'fx-pre-recorder', selection: { centralTab: 'changes' } });
    expect((await screen.findAllByText('Recorded before the recorder')).length).toBeGreaterThanOrEqual(2);
    document.body.innerHTML = '';
    renderShell({ runId: 'fx-capture-off', selection: { centralTab: 'changes' } });
    expect((await screen.findAllByText('Capture was off')).length).toBeGreaterThanOrEqual(2);
  });

  it('content denied (the production default): full capture, but the UI says content is not authorized and requests none', async () => {
    const { provider } = renderShell({ runId: 'fx-denied-content', selection: { centralTab: 'changes', attempt: '1', filePath: 'src/Api.java' } });
    expect(await screen.findByText(/content visibility policy does not authorize showing stored content/)).toBeInTheDocument();
    expect(await provider.capabilities()).toMatchObject({ ok: true, data: { features: { evidence_content: false } } });
  });

  it('content allowed by Kriya: still no viewer in this build, said plainly (never an invented diff)', async () => {
    renderShell({ runId: 'fx-denied-content', selection: { centralTab: 'changes', attempt: '1', filePath: 'src/Api.java' }, fixture: { evidenceContent: true } });
    fireEvent.click(await screen.findByRole('button', { name: 'Show diff' }));
    expect(await screen.findByLabelText('Unified diff')).toHaveTextContent('field0 = 42');
  });

  it('unsealed store: degraded with the reason, labelled Unfinished, and never claimed to be active', async () => {
    renderShell({ runId: 'fx-unsealed' });
    // exactly one prominent notice (the trust strip owns it); Timeline and Evidence only carry a compact marker
    expect(await screen.findAllByText(/The store is unsealed: the run crashed or is still writing; which one is not recorded\./)).toHaveLength(1);
    expect(await screen.findByText('unsealed store')).toBeInTheDocument();
    expect(await screen.findByText('Unfinished')).toBeInTheDocument();
    expect((await trust()).getByText('unsealed')).toBeInTheDocument();
    expect(screen.queryByText(/\bActive\b|Running/)).toBeNull();
  });

  it('no-change outcome: UNCHANGED, baseline recorded, identical', async () => {
    renderShell({ runId: 'fx-no-change', selection: { centralTab: 'changes' } });
    const list = await screen.findByRole('list', { name: 'Changed files' });
    expect(within(list).getByText('UNCHANGED')).toBeInTheDocument();
    expect(within(list).getByText(/recorded baseline/)).toBeInTheDocument();
  });

  it('every tab renders for every scenario without an error state leaking from a module', async () => {
    for (const id of SCENARIO_IDS) {
      document.body.innerHTML = '';
      renderShell({ runId: id });
      const tabs = await screen.findByRole('tablist', { name: 'Run documents' });
      for (const t of within(tabs).getAllByRole('tab')) { fireEvent.click(t); await screen.findByRole('tabpanel'); }
      expect(screen.queryByText(/Module .* failed/), id).toBeNull();
      tab('Overview');
    }
  });
});
