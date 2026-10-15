/**
 * The presentation layer: pure functions from AUTHORITATIVE facts (what Kriya recorded) to user meaning. Components render what is returned here; they do not translate.
 *
 * Rules this file keeps (PRODUCT-UX-COMPLETION):
 *  - It adds no fact. A statement appears only when the recorded fact behind it exists; an unknown gate or event is never mapped by guess - it stays in the technical layer.
 *  - It never reads prompt text. The run title is the backend's `display_goal` (the original request, resolved from structured fields); when that is absent the title is the
 *    neutral "Engineering task", never a substring of a compiled prompt.
 *  - Mechanism names (gate types, record kinds, digests, sequence numbers) are not produced for the primary surface; the technical layer keeps the exact names.
 */
import type { RunEvent, RunSummary } from '../../model/kup';
import type { GateFact } from './runFacts';

// ------------------------------------------------------------------ the run title

/** The title shown for a run: the first line of the ORIGINAL request, bounded; the neutral fallback when Kriya has no authoritative request for the row. */
export const NEUTRAL_TITLE = 'Engineering task';
export function goalTitle(displayGoal: string | null | undefined, max = 90): string {
  const text = (displayGoal ?? '').trim();
  if (text === '') return NEUTRAL_TITLE;
  const firstLine = (text.split('\n').find((l) => l.trim() !== '') ?? text).trim().replace(/\s+/g, ' ');
  return firstLine.length > max ? `${firstLine.slice(0, max - 1).trimEnd()}…` : firstLine;
}

export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return null;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

// ------------------------------------------------------------------ history card

export type CardStatus = 'success' | 'failure' | 'unfinished' | 'unknown';
export interface RunCard {
  title: string;
  /** true when the title is the original request; false for the neutral fallback (the UI may then say where to look). */
  titleIsRequest: boolean;
  status: CardStatus;
  statusLabel: string;
  duration: string | null;
  /** How many files the trace row lists as modified; null when the row lists none or the field is absent. */
  filesChanged: number | null;
  /** "YYYY-MM-DD HH:MM" exactly as stored (no timezone is implied), or null. */
  when: string | null;
}

export function runCard(run: Pick<RunSummary, 'display_goal' | 'status' | 'duration_sec' | 'files_modified' | 'timestamp'>): RunCard {
  const status: CardStatus = run.status === 'success' ? 'success' : run.status === 'failure' ? 'failure' : run.status === 'in_progress' ? 'unfinished' : 'unknown';
  const files = typeof run.files_modified === 'string' ? run.files_modified.split(',').map((f) => f.trim()).filter(Boolean) : [];
  const title = goalTitle(run.display_goal);
  return {
    title, titleIsRequest: title !== NEUTRAL_TITLE, status,
    statusLabel: status === 'success' ? 'Succeeded' : status === 'failure' ? 'Failed' : status === 'unfinished' ? 'Unfinished' : 'Status not recorded',
    duration: formatDuration(run.duration_sec), filesChanged: files.length === 0 ? null : files.length,
    when: typeof run.timestamp === 'string' && run.timestamp.trim() !== '' ? run.timestamp.slice(0, 16).replace('T', ' ') : null,
  };
}

// ------------------------------------------------------------------ verification, in meaning

export type GroupId = 'build' | 'tests' | 'behavior' | 'safety';
export type GroupStatus = 'passed' | 'failed' | 'unknown';
export interface VerificationLine { text: string; status: GroupStatus; /** The exact recorded gate types behind this line (technical layer). */ gates: string[] }
export interface VerificationGroup { id: GroupId; title: string; status: GroupStatus; lines: VerificationLine[] }

/** Recorded gate type -> the meaning group and the line it supports. A gate type NOT listed here is never shown in the groups: it stays under Technical verification. */
const MAPPING: Record<string, { group: GroupId; line: string }> = {
  compile: { group: 'build', line: 'build' },
  test: { group: 'tests', line: 'tests' }, tests: { group: 'tests', line: 'tests' }, targeted_test: { group: 'tests', line: 'tests' }, regression_test: { group: 'tests', line: 'tests' },
  // `goal_spec_compliance` is a PER-UNIT model judgment about one planned step's files; it is never the whole goal's verdict. Only `original_requirements` - recorded by the terminal gate over the
  // ORIGINAL request, after every unit - may say the requested behavior was verified.
  goal_spec_compliance: { group: 'behavior', line: 'planned' }, original_requirements: { group: 'behavior', line: 'behavior' },
  preserved_references: { group: 'safety', line: 'preserved' },
  static_analysis: { group: 'safety', line: 'safety' }, semantic_safety: { group: 'safety', line: 'safety' }, stack_contract: { group: 'safety', line: 'safety' },
};
const GROUP_ORDER: GroupId[] = ['build', 'tests', 'behavior', 'safety'];
const GROUP_TITLE: Record<GroupId, string> = { build: 'Build', tests: 'Tests', behavior: 'Requested behavior', safety: 'Regression and safety' };
const LINE_ORDER = ['build', 'tests', 'planned', 'behavior', 'preserved', 'safety'];

/** The recorded test count behind a tests line, when the recorder persisted one. Zero cases is NOT a pass: nothing was verified by tests. */
const countedTests = (gates: readonly GateFact[]) => gates.find((g) => g.tests !== null)?.tests ?? null;

function status(gates: readonly GateFact[]): GroupStatus {
  if (gates.some((g) => g.passed === false)) return 'failed';
  return gates.length > 0 && gates.every((g) => g.passed === true) ? 'passed' : 'unknown';
}

function lineText(line: string, s: GroupStatus, gates: readonly GateFact[]): string {
  if (line === 'build') return s === 'passed' ? 'Build passed' : s === 'failed' ? 'Build failed' : 'Build: no result recorded';
  if (line === 'tests') {
    const counted = countedTests(gates);
    if (s !== 'failed' && counted !== null && counted.cases === 0) return 'No tests ran for this change';
    const detail = counted === null ? '' : counted.passed !== null ? `${counted.passed} / ${counted.cases} tests` : `${counted.cases} test${counted.cases === 1 ? '' : 's'}`;
    if (s === 'passed') return counted === null ? 'Tests passed' : counted.passed !== null ? `${detail} passed` : `Tests passed (${detail})`;
    return s === 'failed' ? (counted !== null && counted.passed !== null ? `Tests failed (${detail} passed)` : 'Tests failed') : 'Tests: no result recorded';
  }
  if (line === 'planned') return s === 'passed' ? 'Matches the planned change' : s === 'failed' ? 'Does not match the planned change' : 'Planned change: no result recorded';
  if (line === 'behavior') return s === 'passed' ? (gates.some((g) => g.type === 'original_requirements' && g.requirementsVerified === true) ? 'Requested behavior verified' : 'No unmet requirement recorded (not every requirement was independently verified)') : s === 'failed' ? 'Requested behavior not verified' : 'Requested behavior: no result recorded';
  if (line === 'preserved') return s === 'passed' ? 'Existing behavior preserved' : s === 'failed' ? 'Existing behavior not preserved' : 'Existing behavior: no result recorded';
  return s === 'passed' ? 'Safety checks passed' : s === 'failed' ? 'Safety checks failed' : 'Safety checks: no result recorded';
}

/** The recorded gates grouped by meaning. `technical` lists EVERY recorded gate with its exact type (nothing is dropped); `unmapped` are the ones no group covers. */
export function verificationGroups(gates: readonly GateFact[]): { groups: VerificationGroup[]; technical: GateFact[]; unmapped: GateFact[] } {
  const byLine = new Map<string, { group: GroupId; gates: GateFact[] }>();
  const unmapped: GateFact[] = [];
  for (const gate of gates) {
    const m = MAPPING[gate.type];
    if (!m) { unmapped.push(gate); continue; }
    const entry = byLine.get(m.line) ?? { group: m.group, gates: [] };
    entry.gates.push(gate);
    byLine.set(m.line, entry);
  }
  const groups: VerificationGroup[] = [];
  for (const id of GROUP_ORDER) {
    const lines: VerificationLine[] = LINE_ORDER.filter((l) => byLine.get(l)?.group === id).map((l) => {
      const entry = byLine.get(l)!;
      const noTestsRan = l === 'tests' && countedTests(entry.gates)?.cases === 0;
      // a passed run-level requirements gate that did not positively verify every requirement is not a "verified" claim (independent review B4)
      const notFullyVerified = l === 'behavior' && status(entry.gates) === 'passed' && !entry.gates.some((g) => g.type === 'original_requirements' && g.requirementsVerified === true);
      const s = (noTestsRan || notFullyVerified) && status(entry.gates) !== 'failed' ? 'unknown' : status(entry.gates);
      return { text: lineText(l, status(entry.gates), entry.gates), status: s, gates: entry.gates.map((g) => g.type) };
    });
    if (lines.length === 0) continue;
    groups.push({ id, title: GROUP_TITLE[id], status: lines.some((l) => l.status === 'failed') ? 'failed' : lines.every((l) => l.status === 'passed') ? 'passed' : 'unknown', lines });
  }
  return { groups, technical: [...gates], unmapped };
}

/** The recorded checks in one sentence for a compact place (the approval screen, history): only groups with a recorded result, nothing counted that was not recorded. */
export function verificationSummary(gates: readonly GateFact[]): VerificationLine[] {
  return verificationGroups(gates).groups.flatMap((g) => g.lines);
}

/** What a run waiting on a person's decision can honestly say about the WHOLE goal: nothing is verified yet - the run is mid-flight and continues after the decision. Never derived from a
 * per-unit judgment, and shown on every approval screen (an approval is by definition not the end of the run). */
export const OVERALL_GOAL_PENDING: VerificationLine = { text: 'Overall goal: not verified yet - Kriya continues after your decision', status: 'unknown', gates: [] };

/** The verification lines for a decision taken MID-RUN: the checks of the current change, then the explicit overall-goal line. */
export function currentChangeVerification(gates: readonly GateFact[]): VerificationLine[] {
  return [...verificationSummary(gates), OVERALL_GOAL_PENDING];
}

// ------------------------------------------------------------------ the current run, in words

/** "Current activity": one plain sentence per user-facing state. It restates the STATE the records support; it adds no detail the records do not carry. */
export function activityText(state: string, finishingAfterApproval: boolean): string {
  if (finishingAfterApproval) return 'Running final regression and safety checks…';
  switch (state) {
    case 'STARTING': return 'Starting the run…';
    case 'PLANNING': return 'Planning the change…';
    case 'EDITING': return 'Creating or changing files…';
    case 'REPAIRING': return 'Trying again after the last version did not pass its checks…';
    case 'VALIDATING': return 'Running the build, tests and checks…';
    case 'WAITING_FOR_APPROVAL': return 'Waiting for your decision…';
    default: return 'Working…';
  }
}

// ------------------------------------------------------------------ what changed, in words

export interface ChangedFile { path: string; additions: number | null; deletions: number | null; changeType: 'added' | 'deleted' | 'modified' | null }
/** "3 files created" / "1 file changed" - derived only from the recorded change types; when they are not recorded the neutral "changed" is used. */
export function changeSummary(files: readonly ChangedFile[]): string {
  const n = files.length;
  const noun = `${n} file${n === 1 ? '' : 's'}`;
  if (n === 0) return 'No file change is recorded';
  if (files.every((f) => f.changeType === 'added')) return `${noun} created`;
  if (files.every((f) => f.changeType === 'deleted')) return `${noun} removed`;
  return `${noun} changed`;
}

// ------------------------------------------------------------------ the human timeline

export interface TimelineEntry { at: number; time: string; text: string; kind: 'info' | 'ok' | 'bad' }
const details = (e: RunEvent): Record<string, unknown> => (e.details !== null && typeof e.details === 'object' && !Array.isArray(e.details) ? (e.details as Record<string, unknown>) : {});
/** Local "HH:MM" of a recorded epoch-seconds timestamp (presentation only). */
export const eventClock = (epochSeconds: number): string => new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

/**
 * The normal-layer timeline: a deterministic mapping from Kriya's recorded RUN EVENTS to a few plain sentences. Only the kinds listed here appear; every other kind (egress,
 * model identity, prompt composition, validation baselines, ...) stays in the technical timeline. Nothing is invented: no "plan created" line exists because no run event
 * records one; the end line comes from the trace row's own status and is placed at the time of the last recorded event.
 */
export function humanTimeline(events: readonly RunEvent[], status: string | null | undefined): TimelineEntry[] {
  const ordered = events.filter((e) => typeof e.created_at === 'number' && Number.isFinite(e.created_at)).sort((a, b) => a.created_at - b.created_at);
  if (ordered.length === 0) return [];
  const out: TimelineEntry[] = [];
  const add = (e: RunEvent, text: string, kind: TimelineEntry['kind'] = 'info') => out.push({ at: e.created_at, time: eventClock(e.created_at), text, kind });
  add(ordered[0]!, 'Run started');
  for (const e of ordered) {
    const d = details(e);
    switch (e.kind) {
      case 'attempt.started': add(e, (e.attempt ?? 1) > 1 ? 'Started another try' : 'Started working on the change'); break;
      case 'generation.completed': { const n = typeof d.file_count === 'number' ? d.file_count : null; add(e, n === null ? 'Change prepared' : `Change prepared (${n} file${n === 1 ? '' : 's'})`); break; }
      case 'candidate_gates.passed': add(e, d.passed === true ? 'Checks passed on the proposed change' : 'Checks failed on the proposed change', d.passed === true ? 'ok' : 'bad'); break;
      case 'approval.decision': add(e, d.outcome === 'approved' ? 'Change approved' : d.outcome === 'rejected' ? 'Change rejected' : 'Approval decision recorded', d.outcome === 'approved' ? 'ok' : d.outcome === 'rejected' ? 'bad' : 'info'); break;
      case 'terminal_regression.passed': add(e, d.passed === true ? 'Final verification passed' : 'Final verification failed', d.passed === true ? 'ok' : 'bad'); break;
      case 'attempt.passed': if (d.applied === true) add(e, 'Change applied to your folder', 'ok'); break;
      default: break;
    }
  }
  const last = ordered[ordered.length - 1]!;
  if (status === 'success') add(last, 'Run completed', 'ok');
  else if (status === 'failure') add(last, 'Run failed', 'bad');
  return out;
}

// ------------------------------------------------------------------ "why should I trust this result?"

export type TrustState = 'ok' | 'bad' | 'unknown';
export interface TrustItem { category: 'Goal' | 'Change' | 'Build' | 'Tests' | 'Behavior' | 'Safety' | 'Approval' | 'Final result'; text: string; state: TrustState }
export interface TrustInput {
  displayGoal: string | null;
  filesChanged: number | null;
  gates: readonly GateFact[];
  /** The recorded `approval.decision` outcome of the run, when there was one. */
  approval: 'approved' | 'rejected' | null;
  /** The evidence store's verification result (VERIFIED, ...) and whether it is sealed; null when no store was read. */
  evidence: { status: string | null; sealed: boolean | null } | null;
}

/** The user layer of evidence: eight categories, each a statement a recorded fact supports. A category with no recorded fact says so (unknown); it is never ticked. */
export function userEvidence(input: TrustInput): TrustItem[] {
  const items: TrustItem[] = [];
  items.push(input.displayGoal !== null && input.displayGoal.trim() !== '' ? { category: 'Goal', text: 'Original request recorded', state: 'ok' } : { category: 'Goal', text: 'The original request is not recorded for this run', state: 'unknown' });
  items.push(input.filesChanged !== null && input.filesChanged > 0 ? { category: 'Change', text: `${input.filesChanged} file${input.filesChanged === 1 ? '' : 's'} changed`, state: 'ok' } : { category: 'Change', text: 'No file change is recorded', state: 'unknown' });
  const { groups } = verificationGroups(input.gates);
  const category = { build: 'Build', tests: 'Tests', behavior: 'Behavior', safety: 'Safety' } as const;
  for (const group of groups) for (const line of group.lines) items.push({ category: category[group.id], text: line.text, state: line.status === 'passed' ? 'ok' : line.status === 'failed' ? 'bad' : 'unknown' });
  if (input.approval === 'approved') items.push({ category: 'Approval', text: 'You approved the proposed change', state: 'ok' });
  else if (input.approval === 'rejected') items.push({ category: 'Approval', text: 'You rejected the proposed change', state: 'bad' });
  const e = input.evidence;
  items.push(e && e.status === 'VERIFIED' && e.sealed === true ? { category: 'Final result', text: 'Evidence integrity verified', state: 'ok' }
    : e ? { category: 'Final result', text: `Evidence integrity: ${e.status ?? 'not verified'}${e.sealed === false ? ' (not sealed)' : ''}`, state: e.status === 'VERIFIED' ? 'unknown' : 'bad' }
    : { category: 'Final result', text: 'No evidence store was read for this run', state: 'unknown' });
  return items;
}

// ------------------------------------------------------------------ a run that did not complete, in words

/** Plain-language words for the reasons runtime verification could not even start (Kriya's own closed table: kriya/kup/application_outcome.py BLOCKED_REASON_CODES). The raw code is shown only
 * under the technical reason. A code this table does not know gets NO sentence here - never a guess. */
export interface RuntimeStopWords { title: string; body: string; note: string }
const RUNTIME_STOP_WORDS: Record<string, RuntimeStopWords> = {
  MANAGED_SERVICE_APPROVAL_UNSUPPORTED: {
    title: 'Runtime verification could not continue',
    body: 'This goal requires Kriya to start a long-running service. This desktop version does not yet support approval for managed-service execution.',
    note: 'No service was started.',
  },
  REQUIRED_RUNTIME_VERIFICATION_DECLINED: {
    title: 'Runtime verification was not allowed',
    body: 'This goal needs Kriya to run the application to verify it, and the commands were not allowed.',
    note: 'Nothing was run.',
  },
  REQUIRED_RUNTIME_VERIFICATION_DISABLED: {
    title: 'Runtime verification is turned off',
    body: 'This goal needs Kriya to run the application to verify it, but running verification commands is turned off for this project.',
    note: 'Nothing was run.',
  },
};
export const runtimeStopWords = (code: string | null | undefined): RuntimeStopWords | null => (code && Object.prototype.hasOwnProperty.call(RUNTIME_STOP_WORDS, code) ? RUNTIME_STOP_WORDS[code]! : null);

/**
 * What happened to a run that failed, as a sentence a person can act on. Only the recorded failure category and commit result select the words; the raw record
 * (category, exit code, commit result) stays in the technical reason. Categories this table does not know get the neutral sentence, never a guess about the cause.
 */
export function failureSummary(category: string | null | undefined, commitResult: string | null | undefined): string {
  const nothingApplied = commitResult !== null && commitResult !== undefined && commitResult !== 'COMMITTED' ? ' Nothing was applied to your folder.' : '';
  if (category === 'quality_gate_failed') return `The change did not pass Kriya's checks (build, tests or verification).${nothingApplied}`;
  return `Kriya could not complete the requested change.${nothingApplied}`;
}
