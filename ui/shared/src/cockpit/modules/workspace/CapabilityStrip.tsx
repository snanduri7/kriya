import { useEffect, useRef, useState } from 'react';
import { Badge } from '../../core';
import type { ExecutionPreflight, ProjectMemory } from '../../../model/kup';

type HealthRow = NonNullable<ExecutionPreflight['health']>[number];
type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral';

/** A cell composed from an answer Kriya already gave elsewhere on the page (the model probe, the Skills and Tools catalog): its words are that answer's own. */
export interface ComposedCell { label: string; tone: Tone; status: string; detail?: string }

const TONE: Record<string, Tone> = { AVAILABLE: 'ok', DEGRADED: 'warn', UNAVAILABLE: 'bad', NOT_REQUIRED: 'neutral', DISABLED: 'neutral', UNKNOWN: 'neutral' };
const WORDS: Record<string, string> = { AVAILABLE: 'Available', DEGRADED: 'Degraded', UNAVAILABLE: 'Unavailable', NOT_REQUIRED: 'Not required', DISABLED: 'Disabled', UNKNOWN: 'Unknown' };
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

function buildCells(health: readonly HealthRow[], composed: Readonly<Record<string, ComposedCell | null>>, memory: ProjectMemory | undefined): Cell[] {
  const cells = new Map<string, Cell>();
  for (const row of health) {
    cells.set(row.id, { id: row.id, tone: TONE[row.state] ?? 'neutral', status: WORDS[row.state] ?? row.state,
      title: `${row.label}: ${row.detail} (${row.reason_code}; evidence: ${row.evidence.join(', ') || 'none'})` });
  }
  for (const [id, cell] of Object.entries(composed)) if (cell) cells.set(id, { id, tone: cell.tone, status: cell.status, title: `${cell.label}: ${cell.status}${cell.detail ? ` - ${cell.detail}` : ''}` });
  if (memory) cells.set('memory', memoryCell(memory));
  return ORDER.filter((id) => cells.has(id)).map((id) => cells.get(id)!);
}

/**
 * The one-line summary of the cells, COUNTED from their authoritative tones (ok = ready, warn = degraded, bad = unavailable); the outward-lookup cell is named with its own state word because
 * "disabled" is a deliberate setting the person should see. Nothing here decides a cell's state.
 */
export function capabilitySummary(cells: readonly { id: string; tone: Tone; status: string }[]): string {
  const ready = cells.filter((c) => c.tone === 'ok').length;
  const degraded = cells.filter((c) => c.tone === 'warn').length;
  const unavailable = cells.filter((c) => c.tone === 'bad').length;
  const lookup = cells.find((c) => c.id === 'lookup');
  return [`${ready} ready`, ...(degraded ? [`${degraded} degraded`] : []), ...(unavailable ? [`${unavailable} unavailable`] : []), ...(lookup ? [`Lookup ${lookup.status.toLowerCase()}`] : [])].join(' · ');
}

/**
 * The capability strip: what this folder's Kriya runs on, knows and is protected by. The renderer states nothing of its own: a health row's state, reason code, detail and
 * evidence are Kriya's `execution.preflight.health`; the model, Skills and Tools cells are the page's own backend answers passed in; Learning is the memory block's counts.
 * A capability with no answer is simply absent from the strip - it is never shown as available by default.
 */
export function CapabilityStrip({ health, composed, memory }: { health: readonly HealthRow[]; composed: Readonly<Record<string, ComposedCell | null>>; memory: ProjectMemory | undefined }) {
  const ordered = buildCells(health, composed, memory);
  if (ordered.length === 0) return null;
  return (
    <section className="ck-strip" aria-label="Capabilities" data-testid="capability-strip">
      <ul>
        {ordered.map((c) => (
          <li key={c.id} data-capability={c.id} title={c.title}>
            <span className="ck-strip-label">{SHORT[c.id] ?? c.id}</span> <Badge tone={c.tone}>{c.status}</Badge>
            <div className="muted small ck-strip-why">{c.title}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * `Capabilities ▾` in the project bar: collapsed it is one line of counts (`7 ready · 1 unavailable · Lookup disabled`), expanded it lists every cell with its state AND the reason and evidence
 * Kriya gave for it. It closes on a click outside or on Escape (Escape closes this panel only).
 */
export function CapabilitiesControl({ health, composed, memory }: { health: readonly HealthRow[]; composed: Readonly<Record<string, ComposedCell | null>>; memory: ProjectMemory | undefined }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const cells = buildCells(health, composed, memory);
  if (cells.length === 0) return null;
  return (
    <div className="ck-capabilities" ref={root}>
      <button type="button" className="ck-capabilities-toggle" aria-expanded={open} aria-controls="ck-capabilities-panel" onClick={() => setOpen((v) => !v)} data-testid="capabilities-toggle">
        <strong>Capabilities</strong> <span className="muted small" data-testid="capabilities-summary">{capabilitySummary(cells)}</span> <span aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      {open ? <div id="ck-capabilities-panel" className="ck-capabilities-panel" role="dialog" aria-label="Capabilities"><CapabilityStrip health={health} composed={composed} memory={memory} /></div> : null}
    </div>
  );
}
