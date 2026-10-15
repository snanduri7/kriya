/** Activity: every line is backed by a record; nothing is invented, and the subject of a review is named correctly. */
import { describe, expect, it } from 'vitest';
import type { LaunchStatus } from '../../model/kup';
import { activityEntries, activityFromLaunch, activityFromRecords, resultsFromRecords } from './activity';
import type { EvidenceRecord } from './records';

const rec = (seq: number, kind: string, extra: Record<string, unknown> = {}): EvidenceRecord => ({ seq, kind, attempt_number: 1, t_wall: `2026-10-15T10:00:${String(seq).padStart(2, '0')}Z`, payload: {}, blobs: {}, ...extra }) as unknown as EvidenceRecord;
const gate = (seq: number, g: string, success: boolean | null, extra: Record<string, unknown> = {}) => rec(seq, 'gate.result', { payload: { gate: g, success, stage: 'attempt', ...extra } });
const launch = (over: Record<string, unknown> = {}) => ({ launch: { requested_at: '2026-10-15T09:59:59Z', approval_history: [], pending_approval: null, ended: null, ...over } }) as unknown as LaunchStatus;
const runtimeReview = (status: string, decided?: string) => ({ review_id: 'ra-1', kind: 'runtime_command', status, requested_at: '2026-10-15T10:00:30Z', decision: decided ? { decision: status === 'APPROVED' ? 'approve' : 'reject', decided_at: decided } : null });
const changeReview = (status: string, decided?: string) => ({ review_id: 'ra-2', kind: 'change_approval', status, requested_at: '2026-10-15T10:00:10Z', decision: decided ? { decision: 'approve', decided_at: decided } : null });
const texts = (e: ReturnType<typeof activityEntries>) => e.map((x) => x.text);

describe('activity from recorded facts only', () => {
  it('maps the records that exist to plain words, in record order', () => {
    const records = [rec(1, 'run.opened'), rec(2, 'unit.opened'), rec(3, 'attempt.opened'), rec(4, 'candidate.change'), rec(5, 'candidate.change'), gate(6, 'compile', true), gate(7, 'test', true, { test_execution: { cases: 3 } })];
    expect(texts(activityEntries(records, null))).toEqual(['Run started', 'Working on the change', 'Creating project files', 'Build passed', 'Tests passed (3 tests)']);
  });
  it('invents nothing: no Planning, no Preparing runtime verification, no Running application without a record behind it', () => {
    const all = texts(activityEntries([rec(1, 'run.opened'), rec(2, 'unit.opened'), rec(3, 'model.request'), rec(4, 'model.response'), rec(5, 'attempt.closed')], launch()));
    expect(all).toEqual(['Run started']);
    for (const claim of [/Planning/, /Preparing runtime/, /Running application/, /Run completed/]) expect(all.join('|')).not.toMatch(claim);
  });
  it('a failed gate is a failure; zero test cases is "No tests ran", never a pass', () => {
    expect(texts(activityFromRecords([gate(1, 'compile', false)]))).toEqual(['Build failed']);
    const none = activityFromRecords([gate(1, 'regression_test', true, { test_execution: { cases: 0 } })]);
    expect(none.map((e) => [e.text, e.tone])).toEqual([['No tests ran', 'info']]);
  });
  it('the runtime verification result is a recorded gate; its absence is absence', () => {
    expect(texts(activityFromRecords([gate(1, 'run_verification', true)]))).toEqual(['Runtime verification passed']);
    expect(texts(activityFromRecords([gate(1, 'run_verification', false)]))).toEqual(['Runtime verification failed']);
    expect(texts(activityFromRecords([gate(1, 'compile', true)]))).not.toContain('Runtime verification passed');
  });
  it('terminal gates are not attempt activity; a second attempt says so', () => {
    expect(texts(activityFromRecords([gate(1, 'original_requirements', true, { stage: 'terminal' })]))).toEqual([]);
    expect(texts(activityFromRecords([rec(1, 'attempt.opened', { attempt_number: 2 })]))).toEqual(['Trying again']);
  });
});

describe('review activity names the right subject', () => {
  it('a pending runtime review is "Waiting for permission to run commands", a change review is "Waiting for code approval"', () => {
    expect(texts(activityFromLaunch(launch({ pending_approval: runtimeReview('PENDING') })))).toEqual(['Run started', 'Waiting for permission to run commands']);
    expect(texts(activityFromLaunch(launch({ pending_approval: changeReview('PENDING') })))).toEqual(['Run started', 'Waiting for code approval']);
  });
  it('decisions: Code approved / Commands allowed / Commands not allowed / expiry, each from the launch record\'s own decision time', () => {
    const history = [changeReview('APPROVED', '2026-10-15T10:00:20Z')];
    const e = activityFromLaunch(launch({ approval_history: history, pending_approval: runtimeReview('APPROVED', '2026-10-15T10:00:40Z') }));
    expect(texts(e)).toEqual(['Run started', 'Waiting for code approval', 'Code approved', 'Waiting for permission to run commands', 'Commands allowed']);
    expect(texts(activityFromLaunch(launch({ pending_approval: runtimeReview('REJECTED', '2026-10-15T10:00:40Z') })))).toContain('Commands not allowed');
    expect(texts(activityFromLaunch(launch({ pending_approval: runtimeReview('EXPIRED') })))).toContain('The request to run commands expired');
  });
  it('merges by recorded time, drops the launch\'s duplicate start, and ends only when the launch ended', () => {
    const merged = activityEntries([rec(1, 'run.opened'), rec(3, 'attempt.opened')], launch({ pending_approval: runtimeReview('PENDING') }));
    expect(texts(merged)).toEqual(['Run started', 'Working on the change', 'Waiting for permission to run commands']);
    const done = activityEntries([rec(1, 'run.opened')], launch({ ended: { at: '2026-10-15T10:05:00Z', exit_code: 0 } }));
    expect(texts(done)).toEqual(['Run started', 'Run completed']);
    expect(texts(activityEntries([], launch({ ended: { at: '2026-10-15T10:05:00Z', exit_code: 1 } })))).toEqual(['Run started', 'Run stopped']);
  });
});

describe('Build and Tests tabs', () => {
  it('list only recorded gate results of their family', () => {
    const records = [gate(1, 'compile', true), gate(2, 'compile', false, {}), gate(3, 'test', true, { test_execution: { cases: 5 } }), gate(4, 'static_analysis', true)];
    expect(resultsFromRecords(records, 'build').map((r) => r.text)).toEqual(['Build passed', 'Build failed']);
    expect(resultsFromRecords(records, 'tests').map((r) => r.text)).toEqual(['Tests passed (5 tests)']);
    expect(resultsFromRecords([], 'tests')).toEqual([]);
  });
});
