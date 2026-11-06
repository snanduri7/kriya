import { Badge } from '../design';
import type { ExecutionPreflight, ProjectMemory } from '../../model/kup';

type HealthRow = NonNullable<ExecutionPreflight['health']>[number];
export type CellState = 'AVAILABLE' | 'USED' | 'ACTIVE' | 'NOT_REQUIRED' | 'DISABLED' | 'UNAVAILABLE' | 'DEGRADED' | 'UNKNOWN';
export interface RunCell { id: string; label: string; state: CellState; text: string; evidence: string }

/** What the run recorded, reduced to facts by the existing selectors; null = the evidence could not be read. */
export interface RunCapabilityInput {
  modelCalls: number | null;
  gates: { total: number; passed: number } | null;
  attempts: { opened: number; concluded: readonly boolean[] } | null;
  authorityRecorded: boolean | null;
  skills: readonly string[] | null;
  knownTargetFiles: number | null;
  memoryEntry: ProjectMemory['runs'][number] | null;
  health: readonly HealthRow[];
}

const TONE = { AVAILABLE: 'ok', USED: 'ok', ACTIVE: 'ok', NOT_REQUIRED: 'neutral', DISABLED: 'neutral', UNAVAILABLE: 'bad', DEGRADED: 'warn', UNKNOWN: 'neutral' } as const;
const WORDS: Record<CellState, string> = { AVAILABLE: 'Available', USED: 'Used', ACTIVE: 'Active', NOT_REQUIRED: 'Not required', DISABLED: 'Disabled', UNAVAILABLE: 'Unavailable', DEGRADED: 'Degraded', UNKNOWN: 'Not recorded' };
const fromHealth = (health: readonly HealthRow[], id: string, label: string): RunCell | null => {
  const row = health.find((r) => r.id === id);
  return row ? { id, label, state: row.state as CellState, text: row.reason_code, evidence: `project health: ${row.reason_code}` } : null;
};

/**
 * The capability states of ONE recorded run. Every cell is a statement about what the run's own evidence records (a count of recorded model calls, gate results, attempts, a
 * recorded authority snapshot, the recorded active skills, the recorded known-target package, the memory entry Kriya wrote for the run); the project-level rows (language server,
 * outward lookup) are Kriya's health rows. A capability the evidence does not speak to is "not recorded" - never USED or AVAILABLE by default. The cell names its evidence.
 */
export function runCapabilityCells(input: RunCapabilityInput): RunCell[] {
  const cells: RunCell[] = [];
  const calls = input.modelCalls;
  cells.push({ id: 'model', label: 'Local model', state: calls === null || calls === 0 ? 'UNKNOWN' : 'USED', text: calls ? `${calls} request${calls === 1 ? '' : 's'} answered` : 'no model answer recorded', evidence: 'model.response records' });
  cells.push(input.knownTargetFiles === null || input.knownTargetFiles === 0
    ? { id: 'code_intelligence', label: 'Code intelligence', state: 'UNKNOWN', text: 'no known-target package recorded', evidence: 'context.known_target_package event' }
    : { id: 'code_intelligence', label: 'Code intelligence', state: 'USED', text: `${input.knownTargetFiles} file${input.knownTargetFiles === 1 ? '' : 's'} located by the structural index`, evidence: 'context.known_target_package event' });
  const lsp = fromHealth(input.health, 'lsp', 'Language server');
  if (lsp) cells.push(lsp);
  cells.push(input.skills === null ? { id: 'skills', label: 'Skills', state: 'UNKNOWN', text: 'not recorded', evidence: 'the run detail\'s active skills' }
    : input.skills.length === 0 ? { id: 'skills', label: 'Skills', state: 'NOT_REQUIRED', text: 'no skill was active', evidence: 'the run detail\'s active skills' }
    : { id: 'skills', label: 'Skills', state: 'USED', text: input.skills.join(', '), evidence: 'the run detail\'s active skills' });
  cells.push({ id: 'security', label: 'Security', state: input.authorityRecorded ? 'ACTIVE' : 'UNKNOWN', text: input.authorityRecorded ? 'the run\'s authority was recorded and enforced' : 'no authority snapshot recorded', evidence: 'authority.snapshot record' });
  const lookup = fromHealth(input.health, 'lookup', 'Outward lookup');
  if (lookup) cells.push({ ...lookup, state: lookup.state === 'AVAILABLE' ? 'UNKNOWN' : lookup.state, text: lookup.state === 'AVAILABLE' ? 'enabled; no lookup is recorded in run evidence' : lookup.text });
  cells.push(input.gates === null || input.gates.total === 0 ? { id: 'verify', label: 'Verify', state: 'UNKNOWN', text: 'no gate result recorded', evidence: 'gate.result records' }
    : { id: 'verify', label: 'Verify', state: 'USED', text: `${input.gates.passed} of ${input.gates.total} recorded checks passed`, evidence: 'gate.result records' });
  const a = input.attempts;
  cells.push(a === null || a.opened === 0 ? { id: 'recovery', label: 'Recovery', state: 'UNKNOWN', text: 'no attempt recorded', evidence: 'attempt.opened records' }
    : a.opened > 1 ? { id: 'recovery', label: 'Recovery', state: 'USED', text: `${a.opened} attempts (${a.concluded.map((ok, i) => `#${i + 1} ${ok ? 'succeeded' : 'failed'}`).join(', ') || 'outcomes not recorded'})`, evidence: 'attempt.opened / attempt.concluded records' }
    : { id: 'recovery', label: 'Recovery', state: 'NOT_REQUIRED', text: a.concluded[0] === false ? 'one attempt, which failed (no second attempt recorded)' : 'the first attempt was the only attempt', evidence: 'attempt.opened / attempt.concluded records' });
  const m = input.memoryEntry;
  if (m) cells.push({ id: 'memory', label: 'Learning', state: 'USED', text: `reused ${m.reused.length} · learned ${m.learned.length + m.refreshed.length}${m.rejected ? ` · rejected ${m.rejected}` : ''}`, evidence: 'the memory entry recorded for this run' });
  return cells;
}

export function RunCapabilityStrip({ cells }: { cells: readonly RunCell[] }) {
  if (cells.length === 0) return null;
  return (
    <section className="ck-strip" aria-label="Capabilities in this run" data-testid="run-capability-strip">
      <ul>
        {cells.map((c) => (
          <li key={c.id} data-capability={c.id} data-state={c.state} title={`${c.label}: ${c.text} (evidence: ${c.evidence})`}>
            <span className="ck-strip-label">{c.label}</span> <Badge tone={TONE[c.state]}>{WORDS[c.state]}</Badge> <span className="muted small">{c.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
