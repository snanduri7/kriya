/** Search/filter of recorded timeline events (matrix-safe batch, 2026-10-04): recorded fields only, recorded order and
 * original selection identity preserved, honest counts and empty states, recorded vs unavailable never blurred. */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../src/App';
import { eventMatchesQuery } from '../src/panels/Timeline';
import { FakeHost, env, withSnapshots } from './fakeHost';
import type { Availability, RunDetail, RunEvent, RunSummary } from '../src/model/kup';
import serializerFixture from '../../fixtures/serializer/run_events.json';

const EVENTS = (serializerFixture as unknown as { run_events: RunEvent[] }).run_events; // kinds: context.known_target_package, developer.prompt_composition, model.transition, model.role_metrics
const run = (id: string): RunSummary => ({ run_id: id, timestamp: '2026-09-01 10:00:00', goal: `goal of ${id}`, duration_sec: 3.5, attempts: 2, status: 'FAILED', failure_category: 'quality_gate_failed', files_modified: 'a.py' });
function detailWith(events: RunEvent[] | null, availability: Availability = 'recorded'): RunDetail {
  const sec = <T,>(data: T) => ({ availability, data: availability === 'recorded' ? data : null, reason: availability === 'recorded' ? null : `fixture ${availability}`, provenance: 'fixture' });
  return { run: run('r1'), fields: {}, run_events: sec(events ?? []), evidence_records: sec([]), gate_outcomes: sec([]), model_hops: sec([]), generation_metrics: sec({}), failure_report: sec([]), context: sec({ items: [], tokens: null }), attribution: sec(null), diagnostics: sec(null), comparisons: sec([]), output: sec(null) } as unknown as RunDetail;
}
async function open(detail: RunDetail) {
  const host = new FakeHost(withSnapshots((req) => {
    switch (req.operation) {
      case 'capabilities': return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
      case 'history.list': return env('history.list', { runs: [run('r1')], next_cursor: null });
      case 'history.detail': return env('history.detail', detail);
      default: return env(req.operation, null);
    }
  }));
  render(<App host={host} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Acquire new snapshot' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
  await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
  fireEvent.click(screen.getByText('goal of r1'));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'goal of r1' })).toBeInTheDocument());
  return host;
}
const list = () => screen.getByRole('listbox', { name: /recorded events/i });
const rows = () => within(list()).queryAllByRole('option');
const shown = () => rows().map((o) => o.textContent ?? '');
const search = (q: string) => fireEvent.change(screen.getByRole('searchbox', { name: 'Search recorded events' }), { target: { value: q } });
const result = () => screen.getByRole('status', { name: 'Event filter result' });

describe('eventMatchesQuery searches only recorded fields', () => {
  const e = EVENTS[0]!; // context.known_target_package with details.omitted[0].reason = budget_exhausted
  it('matches kind, source, message and serialized details; never anything else', () => {
    expect(eventMatchesQuery(e, 'KNOWN_TARGET')).toBe(true);
    expect(eventMatchesQuery(e, 'attempt.run_attempt')).toBe(true);
    expect(eventMatchesQuery(e, 'owner-contract replacement')).toBe(true);
    expect(eventMatchesQuery(e, 'budget_exhausted')).toBe(true); // recorded details only
    expect(eventMatchesQuery(e, '1791097200')).toBe(false); // created_at is not a searched field
    expect(eventMatchesQuery(e, 'advisory')).toBe(false); // authority is a filter, not a search field
    expect(eventMatchesQuery(e, '   ')).toBe(true); // blank query matches everything
    expect(eventMatchesQuery({ kind: 'x', created_at: 1, details: 'plain text detail' } as RunEvent, 'plain text')).toBe(true);
    expect(eventMatchesQuery({ kind: 'x', created_at: 1 } as RunEvent, 'anything')).toBe(false);
  });
});

describe('timeline search and filters', () => {
  it('searching narrows to matching events in recorded order with original numbering and honest counts', async () => {
    await open(detailWith(EVENTS));
    await waitFor(() => expect(result()).toHaveTextContent('4 of 4 recorded events shown'));
    search('model');
    expect(shown().map((t) => t.slice(0, 2))).toEqual(['#3', '#4']); // original indices, recorded order
    expect(shown()[0]).toContain('model.transition'); expect(shown()[1]).toContain('model.role_metrics');
    await waitFor(() => expect(result()).toHaveTextContent('2 of 4 recorded events shown (filtered; recorded order kept)'));
    search('budget_exhausted'); // present only inside recorded details of the context package
    expect(shown()).toHaveLength(1); expect(shown()[0]).toContain('#1'); expect(shown()[0]).toContain('context.known_target_package');
    search('metrics of this run'); // message text
    expect(shown()).toHaveLength(1); expect(shown()[0]).toContain('model.role_metrics');
  });
  it('authority and attempt filters compose with the search; the empty state names the filter, not a missing record', async () => {
    await open(detailWith(EVENTS));
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by authority' }), { target: { value: 'auxiliary' } });
    expect(shown()).toHaveLength(1); expect(shown()[0]).toContain('model.role_metrics');
    await waitFor(() => expect(result()).toHaveTextContent('1 of 4 recorded events shown'));
    search('context');
    expect(rows()).toHaveLength(0);
    expect(list()).toHaveTextContent('no recorded events match the filter (4 recorded, all hidden by the filter)');
    expect(screen.getByRole('button', { name: /^all events \(4\)$/ })).toHaveAttribute('aria-pressed', 'true'); // totals untouched
    fireEvent.click(screen.getByRole('button', { name: 'Clear filter' }));
    expect(shown()).toHaveLength(4);
    await waitFor(() => expect(result()).toHaveTextContent('4 of 4 recorded events shown'));
    // attempt chip + search
    fireEvent.click(screen.getByRole('button', { name: /^attempt 2/ }));
    search('transition');
    expect(shown()).toHaveLength(1); expect(shown()[0]).toContain('#3');
    fireEvent.click(screen.getByRole('button', { name: /^all events/ }));
    expect(shown()).toHaveLength(1); // the search stays; only the attempt chip changed
  });
  it('a selected event that the filter hides stays selected, keeps its evidence and is announced; clearing restores the row selected', async () => {
    await open(detailWith(EVENTS));
    fireEvent.click(rows()[3]!); // #4 model.role_metrics
    await waitFor(() => expect(screen.getByText(/"runtime_digest"/)).toBeInTheDocument()); // evidence tab shows its payload
    search('context');
    expect(shown()).toEqual(expect.arrayContaining([expect.stringContaining('#1')]));
    expect(screen.getByText(/selected event #4 \(model\.role_metrics\) is hidden by the current filter; it stays selected/)).toBeInTheDocument();
    expect(screen.getByText(/"runtime_digest"/)).toBeInTheDocument(); // evidence unchanged
    fireEvent.click(screen.getByRole('button', { name: 'Clear filter to show the selected event' }));
    const selected = rows().filter((o) => o.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1); expect(selected[0]).toHaveTextContent('#4'); expect(selected[0]).toHaveTextContent('model.role_metrics');
    // an attempt chip that hides the selected event also announces it; "all events" brings the row back, still selected
    fireEvent.click(screen.getByRole('button', { name: /^attempt 1/ }));
    expect(screen.queryByText(/selected event #4/)).not.toBeInTheDocument(); // narrowing to an attempt drops the selection (existing rule)
    fireEvent.click(rows()[0]!); // select #1 within attempt 1
    fireEvent.click(screen.getByRole('button', { name: /^all events/ }));
    expect(rows().filter((o) => o.getAttribute('aria-selected') === 'true')[0]).toHaveTextContent('#1');
  });
  it('unavailable events show the availability badge and no filter controls; an empty recorded list says so', async () => {
    await open(detailWith(null, 'unreadable'));
    expect(screen.queryByRole('searchbox', { name: 'Search recorded events' })).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Attempts' })).toHaveTextContent('unreadable');
    expect(screen.queryByText(/no recorded events match/)).not.toBeInTheDocument();
  });
  it('an empty recorded list is "no events recorded", never "hidden by the filter"', async () => {
    await open(detailWith([]));
    await waitFor(() => expect(result()).toHaveTextContent('0 of 0 recorded events shown'));
    expect(list()).toHaveTextContent('no events recorded for this selection');
    expect(screen.getByRole('button', { name: 'Clear filter' })).toBeDisabled();
  });
});
