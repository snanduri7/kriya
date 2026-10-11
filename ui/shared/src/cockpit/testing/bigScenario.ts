/** Deterministic synthetic recorder-shaped runs for the scale tests (Phase E). Test infrastructure only: never imported by product code. */
import type { Scenario, SeedRecord } from '../providers/fixture/scenarios';

const hex = (n: number) => 'sha256:' + n.toString(16).padStart(2, '0').repeat(32).slice(0, 64);
const NASTY_NAMES = ['src/Ünïcode/файл.java', 'docs/space in name.md', "src/it's \"quoted\" & $(subshell) `tick`.py", 'a/'.repeat(30) + 'deep.txt', 'src/日本語/テスト.py', 'x'.repeat(240) + '.go'];

export interface BigOptions { id: string; evidenceId: string; attempts: number; filesPerAttempt: number; gatesPerAttempt: number; extraRecords?: number; goal?: string; sealed?: boolean }

export function bigScenario(o: BigOptions): Scenario {
  const records: SeedRecord[] = [];
  const texts: Record<string, string> = {};
  let seq = 0;
  const push = (kind: string, over: Partial<SeedRecord> = {}, payload: Record<string, unknown> = {}, blobs: Record<string, string> = {}): SeedRecord => {
    seq += 1;
    const record: SeedRecord = { seq, prev: hex(seq - 1), kind, run_id: o.evidenceId, unit_id: 'direct', unit_kind: 'direct', invocation_seq: null, phase: null, attempt_number: null, call_seq: null, wire_seq: null, role: null,
      t_wall: `2026-10-11T10:${String(Math.floor(seq / 60) % 60).padStart(2, '0')}:${String(seq % 60).padStart(2, '0')}Z`, t_mono_ms: seq * 10, provenance: 'observed', payload, content_digests: {}, blobs, schema: 'kriya.attempt_evidence/1', phase_provenance: null, ...over };
    records.push(record);
    return record;
  };
  push('run.opened', { unit_id: null, unit_kind: null });
  push('unit.opened');
  for (let attempt = 1; attempt <= o.attempts; attempt += 1) {
    push('attempt.opened', { attempt_number: attempt }, { mode: 'full_set', operation: 'generate' });
    push('model.request', { attempt_number: attempt, role: 'developer', call_seq: seq }, { dispatched: true });
    push('model.result', { attempt_number: attempt, role: 'developer', call_seq: seq }, { finish_reason: 'stop' });
    for (let f = 0; f < o.filesPerAttempt; f += 1) {
      const path = NASTY_NAMES[f % NASTY_NAMES.length]!.replace('.', `${f}.`);
      const ref = hex(5000 + seq);
      texts[ref] = Array.from({ length: 12 }, (_v, i) => `${i % 3 === 0 ? '+' : i % 3 === 1 ? '-' : ' '}line ${i} of ${path}`).join('\n') + '\n';
      push('candidate.change', { attempt_number: attempt }, { decision: 'STAGED', path, created: f % 2 === 0, deleted: false, unchanged: false, lines_added: 4, lines_removed: f % 2 === 0 ? 0 : 4, text_diff: true }, { diff: ref });
    }
    for (let g = 0; g < o.gatesPerAttempt; g += 1) push('gate.result', { attempt_number: attempt }, { gate: ['compile', 'test', 'regression_test', 'lint'][g % 4], success: g !== 1 || attempt === o.attempts, stage: 'targeted', exit_code: 0 });
    push('attempt.closed', { attempt_number: attempt }, { outcome: attempt === o.attempts ? 'SUCCEEDED' : 'FAILED' });
  }
  for (let i = 0; i < (o.extraRecords ?? 0); i += 1) push('model.request', { role: 'reviewer', call_seq: seq }, { n: i });
  push('unit.closed');
  push('run.closed', { unit_id: null, unit_kind: null }, { terminal_status: 'SUCCESS' });
  return {
    id: o.id, evidenceId: o.evidenceId, title: 'Scale run', goal: o.goal ?? 'Scale goal', status: 'success', failureCategory: null, timestamp: '2026-10-11T10:00:00Z', durationSec: 12.5, attempts: o.attempts,
    milestone: { groupId: null, index: null, total: null },
    seed: { store: 'present', capture: 'full', sealed: o.sealed ?? true, records, manifestExtra: { workspace_identity: 'ab'.repeat(32) }, texts },
  };
}

/** `count` minimal history rows (each with an absent store: a history of thousands of runs does not keep thousands of stores in memory). */
export function manyRuns(count: number): Scenario[] {
  return Array.from({ length: count }, (_v, i) => ({
    id: `hist-${String(i).padStart(5, '0')}`, evidenceId: null, title: `Historic ${i}`, goal: i % 97 === 0 ? 'g'.repeat(900) : `Historic goal ${i} ünï ${i % 7 === 0 ? '$(rm -rf)' : ''}`,
    status: i % 5 === 0 ? 'failure' : 'success', failureCategory: null, timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, 0) + i * 60_000).toISOString(), durationSec: i % 300,
    attempts: i % 4, milestone: { groupId: null, index: null, total: null }, seed: { store: 'absent', absence: { reason: 'pruned', detail: 'retention', recorded_event: null } },
  } as Scenario));
}
