/**
 * The presentation layer: pure functions from AUTHORITATIVE facts (what Kriya recorded) to user meaning. Components render what is returned here; they do not translate.
 *
 * Rules this file keeps (PRODUCT-UX-COMPLETION):
 *  - It adds no fact. A statement appears only when the recorded fact behind it exists; an unknown gate or event is never mapped by guess - it stays in the technical layer.
 *  - It never reads prompt text. The run title is the backend's `display_goal` (the original request, resolved from structured fields); when that is absent the title is the
 *    neutral "Engineering task", never a substring of a compiled prompt.
 *  - Mechanism names (gate types, record kinds, digests, sequence numbers) are not produced for the primary surface; the technical layer keeps the exact names.
 */
import type { RunSummary } from '../../model/kup';
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
  goal_spec_compliance: { group: 'behavior', line: 'behavior' }, original_requirements: { group: 'behavior', line: 'behavior' },
  preserved_references: { group: 'safety', line: 'preserved' },
  static_analysis: { group: 'safety', line: 'safety' }, semantic_safety: { group: 'safety', line: 'safety' }, stack_contract: { group: 'safety', line: 'safety' },
};
const GROUP_ORDER: GroupId[] = ['build', 'tests', 'behavior', 'safety'];
const GROUP_TITLE: Record<GroupId, string> = { build: 'Build', tests: 'Tests', behavior: 'Requested behavior', safety: 'Regression and safety' };
const LINE_ORDER = ['build', 'tests', 'behavior', 'preserved', 'safety'];

function status(gates: readonly GateFact[]): GroupStatus {
  if (gates.some((g) => g.passed === false)) return 'failed';
  return gates.length > 0 && gates.every((g) => g.passed === true) ? 'passed' : 'unknown';
}

function lineText(line: string, s: GroupStatus, gates: readonly GateFact[]): string {
  if (line === 'build') return s === 'passed' ? 'Build passed' : s === 'failed' ? 'Build failed' : 'Build: no result recorded';
  if (line === 'tests') {
    const counted = gates.find((g) => g.tests !== null)?.tests ?? null;
    const detail = counted === null ? '' : counted.passed !== null ? `${counted.passed} / ${counted.cases} tests` : `${counted.cases} test${counted.cases === 1 ? '' : 's'}`;
    if (s === 'passed') return counted === null ? 'Tests passed' : counted.passed !== null ? `${detail} passed` : `Tests passed (${detail})`;
    return s === 'failed' ? (counted !== null && counted.passed !== null ? `Tests failed (${detail} passed)` : 'Tests failed') : 'Tests: no result recorded';
  }
  if (line === 'behavior') return s === 'passed' ? 'Requested behavior verified' : s === 'failed' ? 'Requested behavior not verified' : 'Requested behavior: no result recorded';
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
      const s = status(entry.gates);
      return { text: lineText(l, s, entry.gates), status: s, gates: entry.gates.map((g) => g.type) };
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
