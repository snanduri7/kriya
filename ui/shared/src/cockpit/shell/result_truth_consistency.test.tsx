/**
 * ONE requirement truth, every surface (owner ruling 2026-11-08). The frozen table - ALL_VERIFIED "Completed — verified"; OPEN_REQUIREMENTS "Changes applied — verification incomplete" (apply truth and
 * verification truth are separate authorities); no persisted truth "Completed" (never inferred "verified") - must be what a person reads on the result card, the Runs list, the Overview of a stored run
 * (the Replay), the Activity closing line, the stored-run timeline and the desktop notification. The truth is the backend's: the launch record's `requirement_truth` for the live surfaces and the
 * list/detail projection of the persisted `requirement.truth` event for the stored ones. Nothing here recomputes it.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { act } from 'react';
import { activityEntries, humanTimeline, notificationFor, runCard, type RunView } from '../core';
import { renderModule, renderWorkspaceShell } from '../testing/harness';
import { overviewModule } from '../modules/overview';
import { runsModule } from '../modules/runs';

const WORDS = { ALL_VERIFIED: 'Completed — verified', OPEN_REQUIREMENTS: 'Changes applied — verification incomplete', NONE: 'Completed' } as const;
type Case = keyof typeof WORDS;
const TRUTH = {
  ALL_VERIFIED: { status: 'ALL_VERIFIED', verified: 3, accepted: 0, total: 3 },
  OPEN_REQUIREMENTS: { status: 'OPEN_REQUIREMENTS', verified: 1, accepted: 0, total: 3 },
  NONE: undefined,
} as const;
const KIND = { ALL_VERIFIED: 'run_completed_verified', OPEN_REQUIREMENTS: 'run_completed_unverified', NONE: 'run_completed' } as const;
const CASES = Object.keys(WORDS) as Case[];

const launchEnded = (c: Case) => ({ launch: { requested_at: '2026-11-08T10:00:00+00:00', ended: { at: '2026-11-08T10:05:00+00:00', exit_code: 0, ...(TRUTH[c] ? { requirement_truth: TRUTH[c] } : {}) } } });

describe.each(CASES)('one stored/live run whose truth is %s reads the same on every surface', (c) => {
  const phrase = WORDS[c];

  it('the Runs-list card (the backend projection on the list row)', () => {
    const card = runCard({ display_goal: 'g', status: 'success', duration_sec: 5, files_modified: null, timestamp: null, requirement_truth: TRUTH[c] });
    expect(card.statusLabel).toBe(phrase);
    expect(card.tone).toBe(c === 'OPEN_REQUIREMENTS' ? 'warn' : 'ok');
    expect(card.statusLabel).not.toMatch(/Succeeded/);
  });

  it('the stored-run timeline closes with the persisted event\'s truth', () => {
    const events = [{ kind: 'run.started', created_at: 1_700_000_000 }, ...(TRUTH[c] ? [{ kind: 'requirement.truth', created_at: 1_700_000_050, details: { ...TRUTH[c], unresolved: [] } }] : [])];
    const timeline = humanTimeline(events as never, 'success');
    expect(timeline[timeline.length - 1]!.text).toBe(phrase);
  });

  it('the Activity closing line (the launch record\'s truth)', () => {
    const entries = activityEntries([], launchEnded(c) as never).map((e) => e.text);
    expect(entries).toContain(phrase);
    expect(entries).not.toContain('Run completed');
  });

  it('the desktop notification kind comes from the same authority', () => {
    const run = { stage: 'finished', launchId: 'L1', review: null, runtimeReview: null, outcome: { kind: 'success' }, status: launchEnded(c) } as unknown as RunView;
    const note = notificationFor(run)!;
    expect(note.kind).toBe(KIND[c]);
    // the kind -> words half of this proof is standalone/test/notification_host.test.ts (the desktop main process owns the text)
  });
});

describe('the result card (live) and the stored run (Runs list + Overview/Replay) of the same truth', () => {
  it.each(CASES)('%s: result card heading', async (c) => {
    const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'success', ...(TRUTH[c] ? { requirementTruth: { ...TRUTH[c], unresolved: [] } } : {}) } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Create hello.py. It prints hello world when executed. Include a pytest test for it.' } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    const bar = await screen.findByRole('region', { name: 'Current run' });
    for (let i = 0; i < 6; i += 1) act(() => mounted.host.exec!.advance());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    const heading = within(bar).getByRole('heading', { level: 2 });
    if (c === 'NONE') expect(heading).toHaveTextContent(/^Completed$/); else expect(heading).toHaveTextContent(WORDS[c]);
  });

  // The fixture models the backend projection: 'fx-brownfield-modify' recorded an OPEN requirement.truth event; 'fx-greenfield-success' recorded none.
  it.each([['fx-brownfield-modify', WORDS.OPEN_REQUIREMENTS], ['fx-greenfield-success', WORDS.NONE]] as const)('stored run %s: Runs list card and Overview say %s', async (runId, phrase) => {
    const list = renderModule(runsModule);
    await screen.findByText('Milestone fx-milestone-1');
    const metas = Array.from(document.querySelectorAll('.ck-runmeta')).map((m) => m.textContent ?? '');
    expect(metas.some((m) => m.includes(phrase))).toBe(true);   // a card with these words is listed
    expect(metas.every((m) => !/Succeeded/.test(m))).toBe(true);
    list.unmount();
    renderModule(overviewModule, { runId });
    const head = await waitFor(() => { const h = document.querySelector('.ck-result-head'); expect(h).not.toBeNull(); return h!; });
    expect(head.textContent).toContain(phrase);
    expect(head.textContent).not.toMatch(/Succeeded|Run completed/);
  });

  // The selected stored run's header badge (MainWorkspace) reads the same projection as its list card and its Overview.
  it.each([['Changes applied — verification incomplete', /Fix whitespace handling in CharacterUtil/], ['Completed', /Create a Spring Boot|greenfield|Create/i]] as const)('the run header badge says %s', async (phrase, goal) => {
    renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    const list = await screen.findAllByRole('button', { name: /Completed|Changes applied/ });
    const target = list.find((b) => goal.test(b.textContent ?? '') && (b.textContent ?? '').includes(phrase)) ?? null;
    expect(target).not.toBeNull();
    fireEvent.click(target!);
    const title = await screen.findByTestId('run-title');
    const badge = title.parentElement!.querySelector('.muted')!;
    expect(badge.textContent).toContain(phrase);
    expect(badge.textContent).not.toMatch(/Succeeded/);
  });
});
