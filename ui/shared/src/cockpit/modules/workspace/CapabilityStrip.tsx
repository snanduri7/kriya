import { Badge } from '../../core';
import type { ExecutionPreflight, ProjectMemory } from '../../../model/kup';

type HealthRow = NonNullable<ExecutionPreflight['health']>[number];
type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral';

/** A cell composed from an answer Kriya already gave elsewhere on the page (the model probe, the Skills and Tools catalog): its words are that answer's own. */
export interface ComposedCell { label: string; tone: Tone; status: string; detail?: string }

const TONE: Record<string, Tone> = { AVAILABLE: 'ok', DEGRADED: 'warn', UNAVAILABLE: 'bad', NOT_REQUIRED: 'neutral', UNKNOWN: 'neutral' };
const WORDS: Record<string, string> = { AVAILABLE: 'Available', DEGRADED: 'Degraded', UNAVAILABLE: 'Unavailable', NOT_REQUIRED: 'Not required', UNKNOWN: 'Unknown' };
/** The order of the strip: what Kriya runs on, what it knows, what protects the repository, what proves a result, how it recovers, what it learns. */
const ORDER = ['model', 'code_intelligence', 'lsp', 'skills', 'tools', 'security', 'private_state', 'lookup', 'verification', 'recovery', 'memory'];
const SHORT: Record<string, string> = { model: 'Local model', code_intelligence: 'Code intelligence', lsp: 'Language server', skills: 'Skills', tools: 'Tools', security: 'Security',
  private_state: 'Private state', lookup: 'Outward lookup', verification: 'Verify', recovery: 'Recovery', memory: 'Learning' };

interface Cell { id: string; tone: Tone; status: string; title: string }

/** Memory is a state of its own kind: the counts are Kriya's, and no state word is claimed beyond what they say. */
function memoryCell(memory: ProjectMemory): Cell {
  const { counts } = memory;
  const usable = memory.store_state === 'OK' || memory.store_state === 'ABSENT';
  if (!usable) return { id: 'memory', tone: 'warn', status: 'Unusable', title: `Verified Project Memory: ${memory.store_state}. Nothing is reused.` };
  if (counts.current + counts.stale === 0) return { id: 'memory', tone: 'neutral', status: 'Nothing yet', title: 'Verified Project Memory: no verified fact is stored for this repository yet.' };
  return { id: 'memory', tone: counts.stale ? 'warn' : 'ok', status: `${counts.current} current${counts.stale ? ` · ${counts.stale} stale` : ''}`,
    title: `Verified Project Memory: ${counts.current} current fact(s), ${counts.stale} stale; ${counts.reused_last_run} reused in the last run.` };
}

/**
 * The capability strip: what this folder's Kriya runs on, knows and is protected by. The renderer states nothing of its own: a health row's state, reason code, detail and
 * evidence are Kriya's `execution.preflight.health`; the model, Skills and Tools cells are the page's own backend answers passed in; Learning is the memory block's counts.
 * A capability with no answer is simply absent from the strip - it is never shown as available by default.
 */
export function CapabilityStrip({ health, composed, memory }: { health: readonly HealthRow[]; composed: Readonly<Record<string, ComposedCell | null>>; memory: ProjectMemory | undefined }) {
  const cells = new Map<string, Cell>();
  for (const row of health) {
    cells.set(row.id, { id: row.id, tone: TONE[row.state] ?? 'neutral', status: WORDS[row.state] ?? row.state,
      title: `${row.label}: ${row.detail} (${row.reason_code}; evidence: ${row.evidence.join(', ') || 'none'})` });
  }
  for (const [id, cell] of Object.entries(composed)) if (cell) cells.set(id, { id, tone: cell.tone, status: cell.status, title: `${cell.label}: ${cell.status}${cell.detail ? ` - ${cell.detail}` : ''}` });
  if (memory) cells.set('memory', memoryCell(memory));
  const ordered = ORDER.filter((id) => cells.has(id)).map((id) => cells.get(id)!);
  if (ordered.length === 0) return null;
  return (
    <section className="ck-strip" aria-label="Capabilities" data-testid="capability-strip">
      <ul>
        {ordered.map((c) => (
          <li key={c.id} data-capability={c.id} title={c.title}>
            <span className="ck-strip-label">{SHORT[c.id] ?? c.id}</span> <Badge tone={c.tone}>{c.status}</Badge>
          </li>
        ))}
      </ul>
    </section>
  );
}
