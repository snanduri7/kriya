/** The presentation layer: authoritative facts -> user meaning. Every mapping is pinned, and so is every refusal to map. */
import { describe, expect, it } from 'vitest';
import type { RunSummary } from '../../model/kup';
import { NEUTRAL_TITLE, activityText, changeSummary, formatDuration, goalTitle, runCard, verificationGroups } from './presentation';
import type { GateFact } from './runFacts';

const gate = (type: string, passed: boolean | null, tests: GateFact['tests'] = null): GateFact => ({ type, passed, tests });
const COMPILED = '=== Authoritative Goal ===\nThis is the real, unmediated user request - the source of truth for what is actually required.\n\nFix the bug.\n\n=== Planned Implementation ===\nplanner detail';

describe('goalTitle: the title is the ORIGINAL request, never a prompt', () => {
  it('uses the first line of the request, collapsed and bounded', () => {
    expect(goalTitle('Fix the soft-delete bug\nin DefaultDriverService.')).toBe('Fix the soft-delete bug');
    expect(goalTitle('   Create   a   small   app   ')).toBe('Create a small app');
    const long = goalTitle('x'.repeat(300));
    expect(long).toHaveLength(90);
    expect(long.endsWith('…')).toBe(true);
  });
  it('is the neutral title when the backend has no authoritative request (null/empty) - never a substring of anything else', () => {
    for (const none of [null, undefined, '', '   \n  ']) expect(goalTitle(none)).toBe(NEUTRAL_TITLE);
  });
  it('is a pure function of display_goal: the compiled prompt is only ever shown if someone passes it, and the card does not (it reads display_goal)', () => {
    const card = runCard({ display_goal: null, status: 'success', duration_sec: 3, files_modified: 'A.java', timestamp: '2026-10-14 22:32:46' } as RunSummary);
    expect(card.title).toBe(NEUTRAL_TITLE);
    expect(card.title).not.toContain('Authoritative Goal');
    expect(JSON.stringify(card)).not.toContain(COMPILED.slice(0, 20));
  });
});

describe('runCard / formatDuration', () => {
  it('answers: what did I ask, did it work, how long, what changed, when', () => {
    const card = runCard({ display_goal: 'Fix soft-delete bug in DefaultDriverService', status: 'success', duration_sec: 275, files_modified: 'src/A.java', timestamp: '2026-10-14 22:32:40' } as RunSummary);
    expect(card).toEqual({ title: 'Fix soft-delete bug in DefaultDriverService', titleIsRequest: true, status: 'success', statusLabel: 'Succeeded', duration: '4m 35s', filesChanged: 1, when: '2026-10-14 22:32' });
  });
  it('maps statuses honestly: an unknown status is "not recorded", never success', () => {
    for (const [status, label] of [['failure', 'Failed'], ['in_progress', 'Unfinished'], ['weird', 'Status not recorded'], [null, 'Status not recorded']] as const) expect(runCard({ status } as RunSummary).statusLabel).toBe(label);
  });
  it('counts files only from what the row lists', () => {
    expect(runCard({ files_modified: 'a.py, b.py,' } as RunSummary).filesChanged).toBe(2);
    expect(runCard({ files_modified: '' } as RunSummary).filesChanged).toBeNull();
    expect(runCard({ files_modified: null } as RunSummary).filesChanged).toBeNull();
  });
  it('formats durations', () => {
    expect([0.4, 59.6, 60, 3599, 3600, 7322, null, -1, Number.NaN].map((s) => formatDuration(s as number))).toEqual(['0s', '1m 00s', '1m 00s', '59m 59s', '1h 00m', '2h 02m', null, null, null]);
  });
});

describe('verificationGroups: mechanism -> meaning, and nothing unmapped is dressed up', () => {
  it('groups compile, tests, behavior and safety with counts only when recorded', () => {
    const { groups } = verificationGroups([gate('compile', true), gate('regression_test', true, { cases: 53, passed: 53 }), gate('goal_spec_compliance', true), gate('static_analysis', true)]);
    expect(groups.map((g) => g.title)).toEqual(['Build', 'Tests', 'Requested behavior', 'Regression and safety']);
    expect(groups.flatMap((g) => g.lines.map((l) => l.text))).toEqual(['Build passed', '53 / 53 tests passed', 'Requested behavior verified', 'Safety checks passed']);
    expect(groups.every((g) => g.status === 'passed')).toBe(true);
  });
  it('a failure is stated as a failure; a test count below the total is shown as such', () => {
    const { groups } = verificationGroups([gate('compile', false), gate('tests', false, { cases: 10, passed: 7 })]);
    expect(groups.flatMap((g) => g.lines.map((l) => l.text))).toEqual(['Build failed', 'Tests failed (7 / 10 tests passed)']);
    expect(groups.map((g) => g.status)).toEqual(['failed', 'failed']);
  });
  it('a missing result is "no result recorded", never passed; a mixed group is failed, a group with an unrecorded member is unknown', () => {
    expect(verificationGroups([gate('compile', null)]).groups[0]!.lines[0]!.text).toBe('Build: no result recorded');
    expect(verificationGroups([gate('test', true), gate('regression_test', false)]).groups[0]!.status).toBe('failed');
    expect(verificationGroups([gate('test', true), gate('regression_test', null)]).groups[0]!.status).toBe('unknown');
  });
  it('shows the tests line without inventing a count when the recorder kept none', () => {
    expect(verificationGroups([gate('tests', true)]).groups[0]!.lines[0]!.text).toBe('Tests passed');
    expect(verificationGroups([gate('tests', true, { cases: 4, passed: null })]).groups[0]!.lines[0]!.text).toBe('Tests passed (4 tests)');
  });
  it('a gate type with no honest mapping appears ONLY in the technical list (never in a group)', () => {
    const out = verificationGroups([gate('compile', true), gate('artifact_registry', true), gate('migration', true)]);
    expect(out.groups.flatMap((g) => g.lines.flatMap((l) => l.gates))).toEqual(['compile']);
    expect(out.unmapped.map((g) => g.type)).toEqual(['artifact_registry', 'migration']);
    expect(out.technical.map((g) => g.type)).toEqual(['compile', 'artifact_registry', 'migration']); // nothing is dropped
  });
  it('no gates, no groups', () => {
    expect(verificationGroups([])).toEqual({ groups: [], technical: [], unmapped: [] });
  });
});

describe('activityText and changeSummary', () => {
  it('describes the state in plain words without a mechanism term', () => {
    for (const s of ['STARTING', 'PLANNING', 'EDITING', 'REPAIRING', 'VALIDATING', 'WAITING_FOR_APPROVAL', 'RUNNING']) expect(activityText(s, false)).not.toMatch(/candidate|attempt|gate|digest|seq/i);
    expect(activityText('EDITING', false)).toBe('Creating or changing files…');
    expect(activityText('VALIDATING', true)).toBe('Running final regression and safety checks…');
  });
  it('says created / removed / changed from the recorded change types', () => {
    const f = (changeType: 'added' | 'deleted' | 'modified' | null) => ({ path: 'x', additions: 1, deletions: 0, changeType });
    expect(changeSummary([f('added'), f('added'), f('added')])).toBe('3 files created');
    expect(changeSummary([f('deleted')])).toBe('1 file removed');
    expect(changeSummary([f('modified'), f('added')])).toBe('2 files changed');
    expect(changeSummary([f(null)])).toBe('1 file changed');
    expect(changeSummary([])).toBe('No file change is recorded');
  });
});

import { eventClock, humanTimeline, userEvidence } from './presentation';
import type { RunEvent } from '../../model/kup';

const ev = (kind: string, created_at: number, extra: Partial<RunEvent> = {}): RunEvent => ({ kind, created_at, ...extra }) as RunEvent;
const T0 = 1_791_996_485; // the measured Scenario A run (22:28:05 local on the author's machine); only RELATIVE order and the shared formatter matter here

describe('humanTimeline: a deterministic mapping from recorded run events; unknown kinds never appear', () => {
  const events = [
    ev('egress.authority', T0), ev('model.runtime', T0 + 1), ev('validation_baseline.policy', T0 + 2),
    ev('attempt.started', T0 + 10, { attempt: 1 }), ev('developer.prompt_composition', T0 + 20),
    ev('generation.completed', T0 + 26, { details: { file_count: 1 } }),
    ev('candidate_gates.passed', T0 + 38, { details: { passed: true, terminal: false } }), ev('static_analysis.result', T0 + 38),
    ev('approval.decision', T0 + 267, { details: { outcome: 'approved' } }),
    ev('terminal_regression.passed', T0 + 275, { details: { passed: true, terminal: true } }),
    ev('attempt.passed', T0 + 275, { details: { passed: true, applied: true } }), ev('model.role_metrics', T0 + 275),
  ];
  it('tells the run in a handful of plain sentences, in time order', () => {
    expect(humanTimeline(events, 'success').map((e) => e.text)).toEqual([
      'Run started', 'Started working on the change', 'Change prepared (1 file)', 'Checks passed on the proposed change', 'Change approved', 'Final verification passed', 'Change applied to your folder', 'Run completed',
    ]);
  });
  it('shows no mechanism names and invents no "plan created" line', () => {
    const text = humanTimeline(events, 'success').map((e) => e.text).join('\n');
    expect(text).not.toMatch(/egress|model\.|baseline|prompt|static_analysis|role_metrics|plan/i);
  });
  it('uses the same clock formatter for every entry (presentation only) and marks outcomes', () => {
    const tl = humanTimeline(events, 'success');
    expect(tl[0]!.time).toBe(eventClock(T0));
    expect(tl.find((e) => e.text === 'Change approved')!.kind).toBe('ok');
  });
  it('failed gates, a rejection and a failed run are stated as such; a retry is "another try"', () => {
    const failed = humanTimeline([ev('attempt.started', T0, { attempt: 2 }), ev('candidate_gates.passed', T0 + 1, { details: { passed: false } }), ev('approval.decision', T0 + 2, { details: { outcome: 'rejected' } }), ev('terminal_regression.passed', T0 + 3, { details: { passed: false } })], 'failure');
    expect(failed.map((e) => [e.text, e.kind])).toEqual([['Run started', 'info'], ['Started another try', 'info'], ['Checks failed on the proposed change', 'bad'], ['Change rejected', 'bad'], ['Final verification failed', 'bad'], ['Run failed', 'bad']]);
  });
  it('"applied" is only said when the record says applied; no end line for an unknown status; nothing for no events', () => {
    expect(humanTimeline([ev('attempt.passed', T0, { details: { passed: true, applied: false } })], null).map((e) => e.text)).toEqual(['Run started']);
    expect(humanTimeline([], 'success')).toEqual([]);
  });
});

describe('userEvidence: why should I trust this result? Only recorded facts tick a box', () => {
  const base = { displayGoal: 'Fix the bug', filesChanged: 1, gates: [gate('compile', true), gate('regression_test', true, { cases: 53, passed: 53 }), gate('goal_spec_compliance', true), gate('static_analysis', true)], approval: 'approved' as const, evidence: { status: 'VERIFIED', sealed: true } };
  it('lists goal, change, build, tests, behavior, safety, approval and final result', () => {
    expect(userEvidence(base).map((i) => `${i.category}: ${i.text} [${i.state}]`)).toEqual([
      'Goal: Original request recorded [ok]', 'Change: 1 file changed [ok]', 'Build: Build passed [ok]', 'Tests: 53 / 53 tests passed [ok]', 'Behavior: Requested behavior verified [ok]',
      'Safety: Safety checks passed [ok]', 'Approval: You approved the proposed change [ok]', 'Final result: Evidence integrity verified [ok]',
    ]);
  });
  it('an unrecorded fact is never ticked', () => {
    const items = userEvidence({ displayGoal: null, filesChanged: null, gates: [], approval: null, evidence: null });
    expect(items.every((i) => i.state === 'unknown')).toBe(true);
    expect(items.map((i) => i.category)).toEqual(['Goal', 'Change', 'Final result']);
  });
  it('a failed gate, a rejection and an unverified or unsealed store are shown as problems, not ticks', () => {
    const items = userEvidence({ ...base, gates: [gate('compile', false)], approval: 'rejected', evidence: { status: 'CHAIN_BROKEN', sealed: false } });
    expect(items.find((i) => i.category === 'Build')!.state).toBe('bad');
    expect(items.find((i) => i.category === 'Approval')!.state).toBe('bad');
    expect(items.find((i) => i.category === 'Final result')).toMatchObject({ state: 'bad' });
    expect(userEvidence({ ...base, evidence: { status: 'VERIFIED', sealed: false } }).at(-1)!.state).toBe('unknown'); // verified chain but not sealed: not claimed as final
  });
});
