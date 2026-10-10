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
  | { store: 'present'; capture: 'full' | 'digest_only'; sealed: boolean; records: SeedRecord[]; manifestExtra: Record<string, unknown>; texts: Record<string, string> }
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
  /** The trace row's run_events (S1), as the real workflow serializes them: context, requirement and plan events with their details. */
  s1Events?: S1Event[];
  /** The trace row's evidence_records (S1). */
  s1Evidence?: Array<Record<string, unknown>>;
}

export interface S1Event { kind: string; attempt: number; source: string; authority: string; message: string; failure_type: null; operation: null; details: Record<string, unknown>; created_at: number }

/** A blob reference unique per (record, part): two records never share one, as in a real content-addressed store of distinct bytes. */
const refOf = (seq: number, part: number) => hex(1000 + seq * 8 + part);
const hex = (n: number) => 'sha256:' + n.toString(16).padStart(2, '0').repeat(32).slice(0, 64);
const T0 = '2026-10-10T09:00:00.000000Z';

class Builder {
  private seq = 0;
  readonly records: SeedRecord[] = [];
  /** Content bytes by blob reference (what a real store keeps under blobs/); only kept when the capture is `full`. */
  readonly texts: Record<string, string> = {};
  constructor(private readonly runId: string, private readonly capture: 'full' | 'digest_only') {}
  add(kind: string, payload: Record<string, unknown> = {}, who: Partial<Pick<SeedRecord, 'unit_id' | 'unit_kind' | 'invocation_seq' | 'phase' | 'attempt_number' | 'call_seq' | 'role'>> = {}, content: Record<string, string> = {}): this {
    this.seq += 1;
    const names = Object.keys(content);
    if (this.capture === 'full') names.forEach((n, i) => { this.texts[refOf(this.seq, i)] = content[n] ?? ''; });
    this.records.push({
      schema: 'kriya.attempt_evidence/1', seq: this.seq, prev: hex(this.seq - 1), kind, run_id: this.runId,
      unit_id: who.unit_id ?? null, unit_kind: who.unit_kind ?? null, invocation_seq: who.invocation_seq ?? null, phase: who.phase ?? null,
      attempt_number: who.attempt_number ?? null, call_seq: who.call_seq ?? null, wire_seq: null, role: who.role ?? null,
      t_wall: T0, t_mono_ms: this.seq * 40, provenance: 'OBSERVED', payload,
      content_digests: Object.fromEntries(names.map((n, i) => [n, { digest: refOf(this.seq, i), bytes: 64 + this.seq }])),
      blobs: this.capture === 'full' ? Object.fromEntries(names.map((n, i) => [n, refOf(this.seq, i)])) : {},
      phase_provenance: who.attempt_number != null ? 'derived_from_attempt_scope' : who.role && ['planner', 'architect', 'reviewer'].includes(who.role) ? 'derived_from_role' : null,
    });
    return this;
  }
}

const UNIT = { unit_id: 'unit-1', unit_kind: 'task', invocation_seq: 1 };
const att = (n: number, extra: Record<string, unknown> = {}) => ({ ...UNIT, phase: 'attempt', attempt_number: n, ...extra });

function opening(b: Builder, baseRevision: string | null, treeHash: string | null, identity: string, plan: Record<string, unknown> = {}): void {
  b.add('run.opened', { kriya_build: { version: 'fixture' }, workspace_identity: identity, workspace_path: `/fixture/${identity}`, base_revision: baseRevision, base_tree_hash: treeHash });
  b.add('model.result', { finish_reason: 'stop', completion_tokens: 120 }, { phase: 'planning', role: 'planner', call_seq: 1 }, { analysis_model_claimed: 'plan' });
  b.add('unit.opened', { unit_id: 'unit-1', invocation_seq: 1, plan_id: null, plan_fingerprint: null, planned_source_files: [], authorized_write_scope: null, write_scope_mode: null, current_subtask_id: null, milestone_group_id: null, milestone_index: null, resume: false, ...plan }, UNIT, { goal: 'the goal text' });
}

/** What a real attempt records before its candidate: attempt.opened, the authority snapshot, the fitted prompt sections and the model call (plus the wire request when captured). */
function openAttempt(b: Builder, n: number, target: string, o: { wire?: boolean; mode?: string; fallback?: boolean } = {}): void {
  b.add('attempt.opened', { attempts_by_mode_before: {}, mode: o.mode ?? 'full_set', operation: 'create_full_file', reserved_fallback: o.fallback === true }, att(n));
  b.add('authority.snapshot', { authorized_write_scope: [], known_target_files: [target], model: 'dev-model', write_scope_mode: 'unrestricted',
    targets: [{ path: target, digest: hex(40), full_file: false, in_write_scope: null, operations: ['anchored_edit'], requested_operation: 'repair_with_patch', shown_tier: 'skeleton', shown_is_exact: false, shown_revision: hex(41), revision: hex(41), whole_file_authority: false, spans: [], loci: [] }] }, att(n));
  b.add('prompt.sections', { fitter: 'fit_developer_request', request: 'developer', capacity_tokens: 28384, request_tokens: 16374 + n * 100, fitted: false, optional_sections_unchanged: [], unlocated_sections: [],
    segments: [{ name: 'mandatory', chars: 39834, digest: hex(50 + n), mandatory: true, offset: 0, outcome: 'kept' }, { name: 'code_context', chars: 8809, digest: hex(60 + n), mandatory: false, offset: 39834, outcome: n > 1 ? 'omitted' : 'kept' }] }, att(n));
  const who = { ...att(n), role: 'developer', call_seq: n };
  if (o.wire) b.add('model.request', { dispatched: true, model: 'dev-model', tools: 0, wire_body: 'RECORDED' }, who, { messages: JSON.stringify([{ role: 'system', content: 'You are the developer.' }, { role: 'user', content: `Implement the goal in ${target}.` }]), wire_body: '{"model":"dev-model"}' });
  b.add('model.result', { model: 'dev-model', runtime_fingerprint: hex(70), inference_settings_digest: hex(71), finish_reason: 'stop', max_tokens: 4096, prompt_tokens: 16406, prompt_tokens_reported: 11695, completion_tokens: 480, elapsed_seconds: 3.2, backend_status: 'ok', backend_error: null,
    budget: { selected_context_window: 32768, context_window: 32768, required_tokens: 17686, context_state: { served_context_window: 32768, served_context_provenance: 'server_model_config', requested_context_window: 32768 } } }, who);
}

const gate = (b: Builder, attempt: number, name: string, success: boolean, exit: number, stage = 'validator') =>
  b.add('gate.result', { stage, gate: name, success, exit_code: exit, timed_out: false, duration_seconds: name === 'tests' ? 6.2 : 1.4, error_type: null }, att(attempt), { output: `${name} output` });

const sourceOf = (path: string, lines: number) => `// ${path}\npublic class ${path.split('/').pop()?.replace(/\.[a-z]+$/, '')} {\n${Array.from({ length: lines }, (_, i) => `    int field${i} = ${i};`).join('\n')}\n}\n`;

const staged = (b: Builder, attempt: number, path: string, o: { before: boolean; deleted?: boolean; unchanged?: boolean; added?: number; removed?: number }) => {
  const before = o.before ? sourceOf(path, 3) : null;
  const after = o.deleted ? null : o.unchanged ? before : `${sourceOf(path, 3 + (o.added ?? 0))}`.replace('field0 = 0;', 'field0 = 42; // changed');
  const unified = before !== null && after !== null && before !== after ? `--- a/${path}\n+++ b/${path}\n@@ -1,5 +1,${5 + (o.added ?? 0)} @@\n // ${path}\n public class ${path.split('/').pop()?.replace(/\.[a-z]+$/, '')} {\n-    int field0 = 0;\n+    int field0 = 42; // changed\n     int field1 = 1;\n     int field2 = 2;\n }\n`
    : before === null && after !== null ? `--- /dev/null\n+++ b/${path}\n@@ -0,0 +1,${after.split('\n').length - 1} @@\n${after.split('\n').slice(0, -1).map((l) => `+${l}`).join('\n')}\n` : null;
  const content: Record<string, string> = {};
  if (before !== null) content.before = before;
  if (after !== null) content.after = after;
  if (unified !== null) content.diff = unified;
  return b.add('candidate.change', {
    decision: 'STAGED', path, before_digest: o.before ? hex(7) : null, after_digest: o.deleted ? null : o.unchanged ? hex(7) : hex(8),
    created: !o.before, deleted: o.deleted === true, unchanged: o.unchanged === true, lines_added: o.added ?? 0, lines_removed: o.removed ?? 0, text_diff: true,
  }, att(attempt), content);
};

function finish(b: Builder, outcome: string): void {
  b.add('unit.closed', { outcome }, UNIT);
  b.add('run.closed', { terminal_status: outcome, lifecycle_state: 'closed' });
}

function present(b: Builder, capture: 'full' | 'digest_only', sealed: boolean, base: { revision: string | null; tree: string | null; identity: string }): StoreSeed {
  return { store: 'present', capture, sealed, records: b.records, texts: b.texts, manifestExtra: { workspace_identity: base.identity, workspace_path: `/fixture/${base.identity}`, base_revision: base.revision, base_tree_hash: base.tree } };
}

function greenfieldSuccess(): Scenario {
  const b = new Builder('ev-fx-greenfield-success', 'full'); opening(b, null, null, 'greenfield-app');
  openAttempt(b, 1, 'src/App.java', { wire: true }); staged(b, 1, 'src/App.java', { before: false, added: 42 }); staged(b, 1, 'src/AppTest.java', { before: false, added: 18 });
  gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', true, 0); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(1)); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-greenfield-success', evidenceId: 'ev-fx-greenfield-success', title: 'Greenfield: new files, success', goal: 'Create a small CLI app with tests', status: 'success', failureCategory: null, timestamp: '2026-10-10 09:00:00', durationSec: 71.2, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: null, tree: null, identity: 'greenfield-app' }) };
}

function greenfieldRetry(): Scenario {
  const b = new Builder('ev-fx-greenfield-compile-retry', 'full'); opening(b, null, null, 'greenfield-lib', { plan_id: 'plan-1', plan_fingerprint: hex(80), planned_source_files: ['src/Lib.java', 'src/LibTest.java'], authorized_write_scope: ['src/Lib.java', 'src/LibTest.java'], write_scope_mode: 'restricted', current_subtask_id: 's1', milestone_group_id: 'fx-milestone-1', milestone_index: 0 });
  openAttempt(b, 1, 'src/Lib.java'); staged(b, 1, 'src/Lib.java', { before: false, added: 30 }); gate(b, 1, 'compile', false, 1);
  b.add('diagnosis', { attempt: 1, type: 'compile', reason_code: 'COMPILE_FAILED', likely_files: ['src/Lib.java'], evidence_class: 'DERIVED_DETERMINISTIC', attribution_kind: 'SOURCE_DEFECT', attribution_tier: 'locator', attribution_confidence: 'high', source: 'orchestrator' }, att(1), { message: 'Lib.java:12: error: cannot find symbol', raw_output: 'raw' });
  b.add('recovery.decision', { attempt: 1, action: 'retry_same', retry: true, progress_classification: 'PROGRESS', stop_reason_code: null, failure_type: 'compile', retry_decision: { action: 'retry_same', reason: 'The compile failure names a symbol in the authorized file; retrying with the compiler output as evidence.', should_continue: true } }, att(1), { environment_failure: 'none' });
  b.add('fallback.decision', { fallback: false, phase: 'call', requested: 'dev-model', selected: 'dev-model' }, att(2));
  b.add('attempt.closed', { candidate_gates_passed: false, outcome: 'FAILED' }, att(1)); b.add('attempt.concluded', { succeeded: false }, att(1));
  b.add('retry.delta', { previous_attempt: 1, attempt: 2, information_gain: 'PRESENT', changed: ['failure_signature', 'retry_evidence'], dimensions: { model: { previous: 'dev-model', current: 'dev-model' }, model_profile: { previous: hex(9), current: hex(9) }, temperature: { previous: 0.2, current: 0.2 }, mode: { previous: 'full_set', current: 'full_set' }, failure_signature: { previous: null, current: hex(33) }, retry_evidence: { previous: null, current: hex(34) }, 'sections.code_context': { previous: hex(61), current: hex(62) } }, request_digests: { previous: hex(35), current: hex(36) } }, att(2)); openAttempt(b, 2, 'src/Lib.java'); staged(b, 2, 'src/Lib.java', { before: false, added: 31 });
  gate(b, 2, 'compile', true, 0); gate(b, 2, 'tests', true, 0); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(2)); b.add('attempt.concluded', { succeeded: true }, att(2)); finish(b, 'SUCCESS');
  return { id: 'fx-greenfield-compile-retry', evidenceId: 'ev-fx-greenfield-compile-retry', title: 'Greenfield: compile failure, then retry', goal: 'Create a utility library', status: 'success', failureCategory: null, timestamp: '2026-10-10 09:10:00', durationSec: 140.5, attempts: 2, milestone: { groupId: 'fx-milestone-1', index: 0, total: 2 }, seed: present(b, 'full', true, { revision: null, tree: null, identity: 'greenfield-lib' }) };
}

function brownfieldModify(): Scenario {
  const b = new Builder('ev-fx-brownfield-modify', 'full'); const rev = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'; opening(b, rev, hex(9), 'brownfield-svc');
  openAttempt(b, 1, 'src/main/CharacterUtil.java'); staged(b, 1, 'src/main/CharacterUtil.java', { before: true, added: 9, removed: 2 }); staged(b, 1, 'src/test/CharacterUtilTest.java', { before: true, added: 4 });
  gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', true, 0); b.add('mirror.gate_outcome', { attempt: 1, index: 0, success: true, type: 'compile' }, att(1), { outcome: 'mirrored' }); b.add('mirror.event', { kind: 'attempt.started', attempt: 1, source: 'workflow' }, att(1), { event: 'e' }); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(1)); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-brownfield-modify', evidenceId: 'ev-fx-brownfield-modify', title: 'Brownfield: modification with a recorded baseline', goal: 'Fix whitespace handling in CharacterUtil', status: 'success', failureCategory: null, timestamp: '2026-10-10 10:00:00', durationSec: 98.1, attempts: 1, milestone: { groupId: 'fx-milestone-1', index: 1, total: 2 }, seed: present(b, 'full', true, { revision: rev, tree: hex(9), identity: 'brownfield-svc' }) };
}

function brownfieldRegression(): Scenario {
  const b = new Builder('ev-fx-brownfield-regression', 'full'); const rev = '0f1e2d3c4b5a69788796a5b4c3d2e1f001234567'; opening(b, rev, hex(10), 'brownfield-svc');
  openAttempt(b, 1, 'src/main/Matcher.java'); staged(b, 1, 'src/main/Matcher.java', { before: true, added: 12, removed: 6 }); gate(b, 1, 'compile', true, 0); gate(b, 1, 'tests', false, 1);
  b.add('regression.decision', { classification: 'NEW_FAILURE', reason_code: 'REGRESSION_UNATTRIBUTED' }, att(1));
  b.add('diagnosis', { attempt: 1, type: 'regression_test', reason_code: 'REGRESSION_UNATTRIBUTED', likely_files: ['src/main/Matcher.java'], evidence_class: 'MODEL_CLAIMED', attribution_kind: 'REGRESSION', attribution_tier: 'judge', attribution_confidence: 'low', source: 'orchestrator' }, att(1), { message: 'tests failed after the change', raw_output: 'raw' });
  b.add('recovery.decision', { attempt: 1, action: 'stop_environment', retry: false, progress_classification: 'NO_PROGRESS', stop_reason_code: 'REGRESSION_UNATTRIBUTED', failure_type: 'regression_test', retry_decision: { action: 'stop_environment', reason: 'REGRESSION_UNATTRIBUTED: the regression cannot be attributed to a test the candidate changed.', should_continue: false } }, att(1), { environment_failure: 'none' }); b.add('attempt.closed', { candidate_gates_passed: false, outcome: 'FAILED' }, att(1)); b.add('attempt.concluded', { succeeded: false }, att(1)); finish(b, 'FAILED');
  return { id: 'fx-brownfield-regression', evidenceId: 'ev-fx-brownfield-regression', title: 'Brownfield: regression failure', goal: 'Speed up Matcher without changing behaviour', status: 'failure', failureCategory: 'quality_gate_failed', timestamp: '2026-10-10 10:30:00', durationSec: 211.4, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: rev, tree: hex(10), identity: 'brownfield-svc' }) };
}

function digestOnly(): Scenario {
  const b = new Builder('ev-fx-digest-only', 'digest_only'); opening(b, 'cafe0000cafe0000cafe0000cafe0000cafe0000', hex(11), 'brownfield-docs');
  openAttempt(b, 1, 'docs/guide.md', { wire: true }); staged(b, 1, 'docs/guide.md', { before: true, added: 5 }); gate(b, 1, 'compile', true, 0); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(1)); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-digest-only', evidenceId: 'ev-fx-digest-only', title: 'Digest-only evidence', goal: 'Update the guide', status: 'success', failureCategory: null, timestamp: '2026-10-09 15:00:00', durationSec: 40, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'digest_only', true, { revision: 'cafe0000cafe0000cafe0000cafe0000cafe0000', tree: hex(11), identity: 'brownfield-docs' }) };
}

function pruned(): Scenario {
  return { id: 'fx-pruned', evidenceId: 'ev-fx-pruned', title: 'Pruned evidence', goal: 'An older run whose store retention removed', status: 'success', failureCategory: null, timestamp: '2026-09-01 08:00:00', durationSec: 60, attempts: 1, milestone: { groupId: null, index: null, total: null },
    seed: { store: 'absent', absence: { reason: 'pruned', detail: 'the run recorded a store at /fixture/state/attempt-evidence/ev-fx-pruned (status CLOSED) and the store is not on disk now', recorded_event: { run_id: 'ev-fx-pruned', status: 'CLOSED', capture: 'full', seq_at_row: 9, path: '/fixture/state/attempt-evidence/ev-fx-pruned' } } } };
}

function deniedContent(): Scenario {
  const b = new Builder('ev-fx-denied-content', 'full'); opening(b, 'beef0000beef0000beef0000beef0000beef0000', hex(12), 'brownfield-api');
  openAttempt(b, 1, 'src/Api.java'); staged(b, 1, 'src/Api.java', { before: true, added: 3, removed: 1 }); gate(b, 1, 'compile', true, 0); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(1)); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
  return { id: 'fx-denied-content', evidenceId: 'ev-fx-denied-content', title: 'Full capture, content not authorized', goal: 'Adjust the API signature', status: 'success', failureCategory: null, timestamp: '2026-10-09 16:00:00', durationSec: 52, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', true, { revision: 'beef0000beef0000beef0000beef0000beef0000', tree: hex(12), identity: 'brownfield-api' }) };
}

function unsealed(): Scenario {
  const b = new Builder('ev-fx-unsealed', 'full'); opening(b, null, null, 'greenfield-crash');
  openAttempt(b, 1, 'src/Main.java'); staged(b, 1, 'src/Main.java', { before: false, added: 20 }); gate(b, 1, 'compile', true, 0);
  return { id: 'fx-unsealed', evidenceId: 'ev-fx-unsealed', title: 'Unsealed store (crashed or still writing)', goal: 'A run that never closed its store', status: 'in_progress', failureCategory: null, timestamp: '2026-10-10 11:00:00', durationSec: 0, attempts: 1, milestone: { groupId: null, index: null, total: null }, seed: present(b, 'full', false, { revision: null, tree: null, identity: 'greenfield-crash' }) };
}

function noChange(): Scenario {
  const b = new Builder('ev-fx-no-change', 'full'); opening(b, 'f00d0000f00d0000f00d0000f00d0000f00d0000', hex(13), 'brownfield-noop');
  openAttempt(b, 1, 'src/Util.java'); staged(b, 1, 'src/Util.java', { before: true, unchanged: true }); gate(b, 1, 'compile', true, 0); b.add('attempt.closed', { candidate_gates_passed: true, outcome: 'RETURNED' }, att(1)); b.add('attempt.concluded', { succeeded: true }, att(1)); finish(b, 'SUCCESS');
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

const s1 = (kind: string, attempt: number, source: string, message: string, details: Record<string, unknown>): S1Event => ({ kind, attempt, source, authority: 'advisory', message, failure_type: null, operation: null, details, created_at: 1790000000 });

/** The run_events and evidence_records the trace row would hold, derived from the scenario's own records (same attempts, same target), as the real workflow writes them. */
function withS1(scenario: Scenario): Scenario {
  if (scenario.seed.store !== 'present') return scenario;
  const records = scenario.seed.records;
  const attempts = [...new Set(records.map((r) => r.attempt_number).filter((n): n is number => n !== null))];
  const target = String((records.find((r) => r.kind === 'authority.snapshot')?.payload.known_target_files as string[] | undefined)?.[0] ?? 'src/Main.java');
  const events: S1Event[] = [
    s1('requirement.derived', 0, 'workflow.run_generation_workflow', '2 original requirement(s) derived', { digest: hex(90), goal_digest: hex(91), version: 1, requirements: [{ id: 'REQ-1', kind: 'requirement', ordinal: 1, source: 'goal', text: scenario.goal }, { id: 'REQ-2', kind: 'constraint', ordinal: 2, source: 'goal', text: 'Do not modify any other file.' }] }),
    s1('requirement.lineage', 0, 'workflow.run_generation_workflow', "plan cites ['REQ-1']; omitted (still active) ['REQ-2']", { stage: 'plan', cited: ['REQ-1'], omitted: ['REQ-2'], requirement_set_digest: hex(90) }),
  ];
  if (scenario.id === 'fx-greenfield-compile-retry') {
    events.push(s1('structured_plan_validation', 0, 'workflow.legacy_planner_repair', 'Legacy Planner output failed structured-plan schema validation (repair_attempts_so_far=0).', { valid: false, repair_attempts: 0, reason_codes: ['TOOL_SUBTASK_MISSING_TOOL_NAME'] }),
      s1('structured_plan_repair_requested', 0, 'workflow.legacy_planner_repair', 'Requesting bounded Planner repair (attempt 1/2).', { repair_attempt: 1, reason_codes: ['TOOL_SUBTASK_MISSING_TOOL_NAME'] }),
      s1('structured_plan_repair_result', 0, 'workflow.legacy_planner_repair', 'Repair attempt 1 result: complete.', { repair_attempt: 1, classification: 'complete', accepted: true }));
  }
  for (const n of attempts) {
    events.push(
      s1('context.known_target_package', n, 'attempt.run_attempt', `Known-target context package built for the attempt-${n} owner-contract replacement.`, { known_target_files: [target], member_hint_paths: [], unit_count: 1, package_hash: hex(92), tiers: [{ path: target, member_id: null, tier: 'skeleton' }], omitted: [{ path: target, rank: 1, reason: 'minimum_authority_unfit', estimated_tokens: 15197 }, { path: target, rank: 1, reason: 'body_elided', estimated_tokens: 8478 }] }),
      s1('context.edit_capability', n, 'attempt.run_attempt', "Mutation operations decided from this invocation's authoritative context.", { model: 'dev-model', budget_chars: 8512, targets: [{ path: target, operations: ['anchored_edit'], full_file: false, level: 0, loci: [], spans: [], uncovered_loci: [] }] }),
      s1('developer.prompt_composition', n, 'developer', 'Developer prompt composition (estimated per section; provider-reported total).', { code_context_tokens: 8809, skills_tokens: 22, sibling_signatures_tokens: 0, t0_header_tokens: 0, t1_tokens: 0, prompt_tokens_reported: 11695, token_counts: 'estimated (len/4) per section; total as reported by the provider' }),
    );
  }
  const evidence = scenario.id === 'fx-digest-only' ? [] : [{ kind: 'active_skills', source: 'skill_engine', attempt: 0, payload: { skills: ['java-conventions'] }, sensitivity: 'local_only', created_at: 1790000000 }];
  return { ...scenario, s1Events: events, s1Evidence: evidence };
}

export const SCENARIOS: readonly Scenario[] = [greenfieldSuccess(), greenfieldRetry(), brownfieldModify(), brownfieldRegression(), digestOnly(), pruned(), deniedContent(), unsealed(), noChange(), preRecorder(), captureOff()].map(withS1);
export const SCENARIO_IDS = SCENARIOS.map((s) => s.id);
