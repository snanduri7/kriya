/**
 * Deterministic fixture scenarios: TEST INFRASTRUCTURE, not product evidence. Record shapes and payload keys follow what the
 * recorder really writes (scope.py, attempt.py candidate.change, validate.py gate.result) so modules are exercised against the
 * real vocabulary. Greenfield and brownfield differ only in the data (recorded baseline, per-file change flags), never in the code
 * that renders them.
 */
export interface SeedRecord {
  seq: number; prev: string; kind: string; run_id: string; unit_id: string | null; unit_kind: string | null; invocation_seq: number | null;
  phase: string | null; attempt_number: number | null; call_seq: number | null; wire_seq: number | null; role: string | null;
  t_wall: string; t_mono_ms: number; provenance: string; payload: Record<string, unknown>;
  content_digests: Record<string, unknown>; blobs: Record<string, string>; schema: string; phase_provenance: string | null;
}

export type StoreSeed =
  | { store: 'present'; capture: 'full' | 'digest_only'; sealed: boolean; records: SeedRecord[]; manifestExtra: Record<string, unknown> }
  | { store: 'absent'; absence: { reason: string; detail: string; recorded_event: Record<string, unknown> | null } };

export interface Scenario {
  /** The TRACE row id (what history lists). */
  id: string;
  /** The EVIDENCE run id (what the attempt-evidence store is filed under); differs from `id`, as in every real store. null: no store was ever named. */
  evidenceId: string | null;
  title: string;
  goal: string;
  status: string;
  failureCategory: string | null;
  timestamp: string;
  durationSec: number;
  attempts: number;
  milestone: { groupId: string | null; index: number | null; total: number | null };
  seed: StoreSeed;
}

const hex = (n: number) => 'sha256:' + n.toString(16).padStart(2, '0').repeat(32).slice(0, 64);
const T0 = '2026-10-10T09:00:00.000000Z';

class Builder {
  private seq = 0;
  readonly records: SeedRecord[] = [];
  constructor(private readonly runId: string, private readonly capture: 'full' | 'digest_only') {}
  add(kind: string, payload: Record<string, unknown> = {}, who: Partial<Pick<SeedRecord, 'unit_id' | 'unit_kind' | 'invocation_seq' | 'phase' | 'attempt_number' | 'call_seq' | 'role'>> = {}, content: Record<string, string> = {}): this {
    this.seq += 1;
    const names = Object.keys(content);
    this.records.push({
      schema: 'kriya.attempt_evidence/1', seq: this.seq, prev: hex(this.seq - 1), kind, run_id: this.runId,
      unit_id: who.unit_id ?? null, unit_kind: who.unit_kind ?? null, invocation_seq: who.invocation_seq ?? null, phase: who.phase ?? null,
      attempt_number: who.attempt_number ?? null, call_seq: who.call_seq ?? null, wire_seq: null, role: who.role ?? null,
      t_wall: T0, t_mono_ms: this.seq * 40, provenance: 'OBSERVED', payload,
      content_digests: Object.fromEntries(names.map((n, i) => [n, { digest: hex(100 + this.seq + i), bytes: 64 + this.seq }])),
      blobs: this.capture === 'full' ? Object.fromEntries(names.map((n, i) => [n, hex(100 + this.seq + i)])) : {},
      phase_provenance: who.attempt_number != null ? 'derived_from_attempt_scope' : who.role && ['planner', 'architect', 'reviewer'].includes(who.role) ? 'derived_from_role' : null,
    });
    return this;
  }
}

const UNIT = { unit_id: 'unit-1', unit_kind: 'task', invocation_seq: 1 };
const att = (n: number, extra: Record<string, unknown> = {}) => ({ ...UNIT, phase: 'attempt', attempt_number: n, ...extra });

function opening(b: Builder, baseRevision: string | null, treeHash: string | null, identity: string): void {
  b.add('run.opened', { kriya_build: { version: 'fixture' }, workspace_identity: identity, workspace_path: `/fixture/${identity}`, base_revision: baseRevision, base_tree_hash: treeHash });
  b.add('model.result', { finish_reason: 'stop', completion_tokens: 120 }, { phase: 'planning', role: 'planner', call_seq: 1 }, { analysis_model_claimed: 'plan' });
  b.add('unit.opened', { unit_id: 'unit-1' }, UNIT);
}

const gate = (b: Builder, attempt: number, name: string, success: boolean, exit: number, stage = 'validator') =>
  b.add('gate.result', { stage, gate: name, success, exit_code: exit, timed_out: false, duration_seconds: name === 'tests' ? 6.2 : 1.4, error_type: null }, att(attempt), { output: `${name} output` });

const staged = (b: Builder, attempt: number, path: string, o: { before: boolean; deleted?: boolean; unchanged?: boolean; added?: number; removed?: number }) =>
  b.add('candidate.change', {
    decision: 'STAGED', path, before_digest: o.before ? hex(7) : null, after_digest: o.deleted ? null : o.unchanged ? hex(7) : hex(8),
    created: !o.before, deleted: o.deleted === true, unchanged: o.unchanged === true, lines_added: o.added ?? 0, lines_removed: o.removed ?? 0, text_diff: true,
  }, att(attempt), { before: 'old', after: 'new', diff: 'diff' });

function finish(b: Builder, outcome: string): void {
  b.add('unit.closed', { outcome }, UNIT);
  b.add('run.closed', { terminal_status: outcome, lifecycle_state: 'closed' });
}

function present(b: Builder, capture: 'full' | 'digest_only', sealed: boolean, base: { revision: string | null; tree: string | null; identity: string }): StoreSeed {
  return { store: 'present', capture, sealed, records: b.records, manifestExtra: { workspace_identity: base.identity, workspace_path: `/fixture/${base.identity}`, base_revision: base.revision, base_tree_hash: base.tree } };
}

function greenfieldSuccess(): Scenario {
  const b = new Builder('ev-fx-greenfield-success', 'full'); opening(b, null, null, 'greenfield-app');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/App.java', { before: false, added: 42 }); staged(b, 1, 'src/AppTest.java', { before: false, added: 18 });
  gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', true, 0); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-greenfield-success', evidenceId: 'ev-fx-greenfield-success', title: 'Greenfield: new files, success', goal: 'Create a small CLI app with tests', status: 'success', failureCategory: null, timestamp: '2026-10-10 09:00:00', durationSec: 71.2, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: null, tree: null, identity: 'greenfield-app' }) };
}

function greenfieldRetry(): Scenario {
  const b = new Builder('ev-fx-greenfield-compile-retry', 'full'); opening(b, null, null, 'greenfield-lib');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/Lib.java', { before: false, added: 30 }); gate(b, 1, 'compile', false, 1);
  b.add('diagnosis', { type: 'compile', reason_code: 'COMPILE_FAILED', likely_files: ['src/Lib.java'] }, att(1)); b.add('attempt.concluded', { succeeded: false }, att(1));
  b.add('retry.delta', { changed: ['retry_evidence'] }, att(2)); b.add('attempt.opened', {}, att(2)); staged(b, 2, 'src/Lib.java', { before: false, added: 31 });
  gate(b, 2, 'compile', true, 0); gate(b, 2, 'tests', true, 0); b.add('attempt.concluded', { succeeded: true }, att(2)); finish(b, 'SUCCESS');
  return { id: 'fx-greenfield-compile-retry', evidenceId: 'ev-fx-greenfield-compile-retry', title: 'Greenfield: compile failure, then retry', goal: 'Create a utility library', status: 'success', failureCategory: null, timestamp: '2026-10-10 09:10:00', durationSec: 140.5, attempts: 2, milestone: { groupId: 'fx-milestone-1', index: 0, total: 2 }, seed: present(b, 'full', true, { revision: null, tree: null, identity: 'greenfield-lib' }) };
}

function brownfieldModify(): Scenario {
  const b = new Builder('ev-fx-brownfield-modify', 'full'); const rev = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'; opening(b, rev, hex(9), 'brownfield-svc');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/main/CharacterUtil.java', { before: true, added: 9, removed: 2 }); staged(b, 1, 'src/test/CharacterUtilTest.java', { before: true, added: 4 });
  gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', true, 0); b.add('mirror.gate_outcome', { attempt: 1, index: 0, success: true, type: 'compile' }, att(1), { outcome: 'mirrored' }); b.add('mirror.event', { kind: 'attempt.started', attempt: 1, source: 'workflow' }, att(1), { event: 'e' }); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-brownfield-modify', evidenceId: 'ev-fx-brownfield-modify', title: 'Brownfield: modification with a recorded baseline', goal: 'Fix whitespace handling in CharacterUtil', status: 'success', failureCategory: null, timestamp: '2026-10-10 10:00:00', durationSec: 98.1, attempts: 1, milestone: { groupId: 'fx-milestone-1', index: 1, total: 2 }, seed: present(b, 'full', true, { revision: rev, tree: hex(9), identity: 'brownfield-svc' }) };
}

function brownfieldRegression(): Scenario {
  const b = new Builder('ev-fx-brownfield-regression', 'full'); const rev = '0f1e2d3c4b5a69788796a5b4c3d2e1f001234567'; opening(b, rev, hex(10), 'brownfield-svc');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/main/Matcher.java', { before: true, added: 12, removed: 6 }); gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', false, 1);
  b.add('regression.decision', { classification: 'NEW_FAILURE', reason_code: 'REGRESSION_UNATTRIBUTED' }, att(1)); b.add('attempt.concluded', { succeeded: false }, att(1)); finish(b, 'FAILED');
  return { id: 'fx-brownfield-regression', evidenceId: 'ev-fx-brownfield-regression', title: 'Brownfield: regression failure', goal: 'Speed up Matcher without changing behaviour', status: 'failure', failureCategory: 'quality_gate_failed', timestamp: '2026-10-10 10:30:00', durationSec: 211.4, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: rev, tree: hex(10), identity: 'brownfield-svc' }) };
}

function digestOnly(): Scenario {
  const b = new Builder('ev-fx-digest-only', 'digest_only'); opening(b, 'cafe0000cafe0000cafe0000cafe0000cafe0000', hex(11), 'brownfield-docs');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'docs/guide.md', { before: true, added: 5 }); gate(b, 1, 'compile', true, 0); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-digest-only', evidenceId: 'ev-fx-digest-only', title: 'Digest-only evidence', goal: 'Update the guide', status: 'success', failureCategory: null, timestamp: '2026-10-09 15:00:00', durationSec: 40, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'digest_only', true, { revision: 'cafe0000cafe0000cafe0000cafe0000cafe0000', tree: hex(11), identity: 'brownfield-docs' }) };
}

function pruned(): Scenario {
  return { id: 'fx-pruned', evidenceId: 'ev-fx-pruned', title: 'Pruned evidence', goal: 'An older run whose store retention removed', status: 'success', failureCategory: null, timestamp: '2026-09-01 08:00:00', durationSec: 60, attempts: 1, milestone: { groupId: null, index: null, total: null },
    seed: { store: 'absent', absence: { reason: 'pruned', detail: 'the run recorded a store at /fixture/state/attempt-evidence/ev-fx-pruned (status CLOSED) and the store is not on disk now', recorded_event: { run_id: 'ev-fx-pruned', status: 'CLOSED', capture: 'full', seq_at_row: 9, path: '/fixture/state/attempt-evidence/ev-fx-pruned' } } } };
}

function deniedContent(): Scenario {
  const b = new Builder('ev-fx-denied-content', 'full'); opening(b, 'beef0000beef0000beef0000beef0000beef0000', hex(12), 'brownfield-api');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/Api.java', { before: true, added: 3, removed: 1 }); gate(b, 1, 'compile', true, 0); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-denied-content', evidenceId: 'ev-fx-denied-content', title: 'Full capture, content not authorized', goal: 'Adjust the API signature', status: 'success', failureCategory: null, timestamp: '2026-10-09 16:00:00', durationSec: 52, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: 'beef0000beef0000beef0000beef0000beef0000', tree: hex(12), identity: 'brownfield-api' }) };
}

function unsealed(): Scenario {
  const b = new Builder('ev-fx-unsealed', 'full'); opening(b, null, null, 'greenfield-crash');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/Main.java', { before: false, added: 20 }); gate(b, 1, 'compile', true, 0);
  return { id: 'fx-unsealed', evidenceId: 'ev-fx-unsealed', title: 'Unsealed store (crashed or still writing)', goal: 'A run that never closed its store', status: 'in_progress', failureCategory: null, timestamp: '2026-10-10 11:00:00', durationSec: 0, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', false, { revision: null, tree: null, identity: 'greenfield-crash' }) };
}

function noChange(): Scenario {
  const b = new Builder('ev-fx-no-change', 'full'); opening(b, 'f00d0000f00d0000f00d0000f00d0000f00d0000', hex(13), 'brownfield-noop');
  b.add('attempt.opened', {}, att(1)); staged(b, 1, 'src/Util.java', { before: true, unchanged: true }); gate(b, 1, 'compile', true, 0); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-no-change', evidenceId: 'ev-fx-no-change', title: 'No-change outcome', goal: 'Ensure Util already handles null input', status: 'success', failureCategory: null, timestamp: '2026-10-08 12:00:00', durationSec: 33, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: 'f00d0000f00d0000f00d0000f00d0000f00d0000', tree: hex(13), identity: 'brownfield-noop' }) };
}

function preRecorder(): Scenario {
  return { id: 'fx-pre-recorder', evidenceId: null, title: 'Run recorded before the evidence recorder', goal: 'An old run with no evidence.attempt_store event', status: 'success', failureCategory: null, timestamp: '2026-08-01 08:00:00', durationSec: 30, attempts: 1, milestone: { groupId: null, index: null, total: null },
    seed: { store: 'absent', absence: { reason: 'pre_recorder', detail: 'the run recorded no evidence.attempt_store event (written before the attempt-evidence recorder)', recorded_event: null } } };
}

function captureOff(): Scenario {
  return { id: 'fx-capture-off', evidenceId: null, title: 'Capture was off', goal: 'A run with evidence capture disabled', status: 'success', failureCategory: null, timestamp: '2026-08-15 08:00:00', durationSec: 31, attempts: 1, milestone: { groupId: null, index: null, total: null },
    seed: { store: 'absent', absence: { reason: 'capture_off', detail: 'the recorded event reports status DISABLED (capture off or no evidence configuration)', recorded_event: { status: 'DISABLED', capture: 'off', reason: 'capture off' } } } };
}

export const SCENARIOS: readonly Scenario[] = [greenfieldSuccess(), greenfieldRetry(), brownfieldModify(), brownfieldRegression(), digestOnly(), pruned(), deniedContent(), unsealed(), noChange(), preRecorder(), captureOff()];
export const SCENARIO_IDS = SCENARIOS.map((s) => s.id);
