/**
 * Activity: what happened in a run, in plain words, from RECORDED facts only (Demo V2). A deterministic mapping from the evidence records Kriya wrote and from the launch record's own
 * review/decision timestamps. Every line is backed by a record; there is no line for "Planning", "Preparing runtime verification" or any milestone no record carries - a gap in the list is a
 * gap in the records, never filled in. Pure: the drawer reads, this maps.
 */
import { gateRows } from './selectors';
import { payloadOf, type EvidenceRecord } from './records';
import { attemptRejections } from './retryFacts';
import type { LaunchStatus } from '../../model/kup';

export type ActivityTone = 'info' | 'ok' | 'bad' | 'wait';
export interface ActivityEntry { key: string; text: string; tone: ActivityTone; /** The record's own wall time (ISO), when it carries one. */ at: string | null; /** The record kind or launch field this line is backed by (shown only under technical details). */ source: string }

const TEST_GATES = new Set(['test', 'tests', 'targeted_test', 'regression_test']);
const wall = (r: EvidenceRecord): string | null => { const t = (r as { t_wall?: unknown }).t_wall; return typeof t === 'string' ? t : null; };

/** The evidence records as activity lines, in record order. Repeated consecutive "creating files" records are one line. */
export function activityFromRecords(records: readonly EvidenceRecord[]): ActivityEntry[] {
  const out: ActivityEntry[] = [];
  let lastKind = '';
  const push = (r: EvidenceRecord, text: string, tone: ActivityTone, suffix = '') => out.push({ key: `r${r.seq}${suffix}`, text, tone, at: wall(r), source: r.kind });
  // The first failed gate of each attempt is the attempt's rejection (Demo V2): what it was rejected for, from the recorded gate type only; a delivery failure also says which earlier finding is still open.
  const rejectionAt = new Map(attemptRejections(records).map((x) => [x.seq, x]));
  for (const r of [...records].sort((a, b) => a.seq - b.seq)) {
    const p = payloadOf(r);
    switch (r.kind) {
      case 'run.opened': push(r, 'Run started', 'info'); break;
      case 'attempt.opened': push(r, (r.attempt_number ?? 1) > 1 ? `Trying repair ${r.attempt_number}` : 'Working on the change', 'info'); break;
      case 'candidate.change': if (lastKind !== 'candidate.change') push(r, 'Creating project files', 'info'); break;
      case 'gate.result':
      case 'mirror.gate_outcome': {
        const row = gateRows([r])[0];
        if (!row || row.origin === 'terminal') break;
        const ok = row.success;
        if (row.type === 'compile') push(r, ok === true ? 'Build passed' : ok === false ? 'Build failed' : 'Building', ok === false ? 'bad' : ok === true ? 'ok' : 'info');
        else if (TEST_GATES.has(row.type)) {
          const cases = row.testSummary && typeof row.testSummary.cases === 'number' ? row.testSummary.cases : null;
          push(r, ok === true ? (cases === 0 ? 'No tests ran' : cases !== null ? `Tests passed (${cases} test${cases === 1 ? '' : 's'})` : 'Tests passed') : ok === false ? 'Tests failed' : 'Running tests', ok === false ? 'bad' : ok === true && cases !== 0 ? 'ok' : 'info');
        } else if (row.type === 'run_verification' || row.type === 'run_verification_hung') push(r, ok === true ? 'Runtime verification passed' : 'Runtime verification failed', ok === true ? 'ok' : 'bad');
        const rejection = rejectionAt.get(r.seq);
        if (rejection) {
          push(r, `Attempt ${rejection.attempt} rejected`, 'bad', ':rejected');
          if (rejection.words) push(r, rejection.words, 'info', ':why');
          if (rejection.unresolved) push(r, `The earlier issue is still unresolved: ${rejection.unresolved}`, 'info', ':open');
        }
        break;
      }
      case 'run.closed': push(r, p.status === 'success' || p.terminal_status === 'SUCCESS' ? 'Run completed' : 'Run ended', 'info'); break;
      default: break;
    }
    lastKind = r.kind;
  }
  return out;
}

/** The launch record's own review facts as activity lines: waiting for a person, and what the person decided. Subject-aware (a code change vs. running commands). */
export function activityFromLaunch(status: LaunchStatus | null): ActivityEntry[] {
  const out: ActivityEntry[] = [];
  const launch = status?.launch;
  if (!launch) return out;
  out.push({ key: 'launch.requested', text: 'Run started', tone: 'info', at: launch.requested_at ?? null, source: 'launch.requested_at' });
  const reviews = [...(launch.approval_history ?? []), ...(launch.pending_approval ? [launch.pending_approval] : [])];
  for (const review of reviews) {
    const runtime = review.kind === 'runtime_command';
    out.push({ key: `${review.review_id}:asked`, text: runtime ? 'Waiting for permission to run commands' : 'Waiting for code approval', tone: 'wait', at: review.requested_at ?? null, source: 'launch.pending_approval.requested_at' });
    const decided = review.decision;
    if (review.status === 'APPROVED' && decided) out.push({ key: `${review.review_id}:decided`, text: runtime ? 'Commands allowed' : 'Code approved', tone: 'ok', at: decided.decided_at ?? null, source: 'launch.pending_approval.decision' });
    else if (review.status === 'REJECTED' && decided) out.push({ key: `${review.review_id}:decided`, text: runtime ? 'Commands not allowed' : 'Code rejected', tone: 'bad', at: decided.decided_at ?? null, source: 'launch.pending_approval.decision' });
    else if (review.status === 'EXPIRED') out.push({ key: `${review.review_id}:expired`, text: runtime ? 'The request to run commands expired' : 'The review expired', tone: 'bad', at: decided?.decided_at ?? null, source: 'launch.pending_approval.status' });
  }
  const ended = launch.ended;
  if (ended) out.push({ key: 'launch.ended', text: ended.exit_code === 0 ? 'Run completed' : 'Run stopped', tone: ended.exit_code === 0 ? 'ok' : 'bad', at: ended.at ?? null, source: 'launch.ended' });
  return out;
}

/** The merged Activity list: the records and the launch record's review lines, ordered by their own recorded time (a line with no time keeps the order it was produced in), the launch's
 * duplicate "Run started"/"Run completed" dropped when the evidence already has them. */
export function activityEntries(records: readonly EvidenceRecord[], status: LaunchStatus | null): ActivityEntry[] {
  const fromRecords = activityFromRecords(records);
  const hasStart = fromRecords.some((e) => e.text === 'Run started');
  const hasEnd = fromRecords.some((e) => e.text === 'Run completed' || e.text === 'Run ended');
  const fromLaunch = activityFromLaunch(status).filter((e) => !(e.key === 'launch.requested' && hasStart) && !(e.key === 'launch.ended' && hasEnd));
  const all = [...fromLaunch.filter((e) => e.key === 'launch.requested'), ...fromRecords, ...fromLaunch.filter((e) => e.key !== 'launch.requested')];
  const stamp = (e: ActivityEntry) => (e.at === null ? Number.NaN : Date.parse(e.at));
  const timed = all.map((e, i) => ({ e, i, t: stamp(e) }));
  return timed.sort((a, b) => (Number.isNaN(a.t) || Number.isNaN(b.t) ? a.i - b.i : a.t - b.t || a.i - b.i)).map((x) => x.e);
}

export interface RecordedResult { key: string; text: string; ok: boolean | null; attempt: number | null }
/** The recorded results of one gate family, newest last: what the Build and Tests tabs list. */
export function resultsFromRecords(records: readonly EvidenceRecord[], family: 'build' | 'tests'): RecordedResult[] {
  return gateRows(records).filter((g) => g.origin !== 'terminal' && (family === 'build' ? g.type === 'compile' : TEST_GATES.has(g.type))).map((g) => {
    const cases = g.testSummary && typeof g.testSummary.cases === 'number' ? g.testSummary.cases : null;
    const word = family === 'build' ? (g.success === true ? 'Build passed' : g.success === false ? 'Build failed' : 'Build: no result recorded')
      : g.success === true ? (cases === 0 ? 'No tests ran' : cases !== null ? `Tests passed (${cases} test${cases === 1 ? '' : 's'})` : 'Tests passed') : g.success === false ? 'Tests failed' : 'Tests: no result recorded';
    return { key: `g${g.seq}`, text: word, ok: cases === 0 ? null : g.success, attempt: g.attempt };
  });
}
