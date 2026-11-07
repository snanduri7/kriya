import { Badge, useNavigate, useWorkspaceUi } from '../../core';
import type { Availability } from '../../core';
import type { ExecutionModelProbe, ExecutionPreflight } from '../../../model/kup';
import { CapabilitiesControl, type ComposedCell } from './CapabilityStrip';
import { projectFacts, type ProjectFact } from './Readiness';
import { OpenInEditorButton, RevealButton } from './WorkspaceFacts';

type Chip = { id: string; label: string; status: string; tone: ProjectFact['tone']; note?: string };
const MARK = { ok: '✓', warn: '⚠', bad: '✕', info: '•', neutral: '○' } as const;

/**
 * The compact readiness line: Git, Config, Model and Security, each one word from Kriya's own answer (the same facts the Inspector's Project tab explains). The folder state joins them ONLY when it is
 * not CLEAN (a run in progress, or a real recovery) - a healthy folder needs no chip. Security is the health row Kriya reports; with no row it is absent, never "ok" by default.
 */
export function readinessChips(pre: ExecutionPreflight, probe: Availability<ExecutionModelProbe>): Chip[] {
  const facts = projectFacts(pre, probe);
  const pick = (id: ProjectFact['id']) => facts.find((f) => f.id === id);
  const chips: Chip[] = [];
  const folder = pick('folder');
  if (folder && folder.status !== 'Ready') chips.push({ id: 'folder', label: 'Folder', status: folder.status, tone: folder.tone, ...(folder.note ? { note: folder.note } : {}) });
  for (const [id, label] of [['git', 'Git'], ['configuration', 'Config'], ['model', 'Model']] as const) {
    const f = pick(id);
    if (f) chips.push({ id, label, status: f.status, tone: f.tone, ...(f.note ? { note: f.note } : {}) });
  }
  const security = (pre.health ?? []).find((r) => r.id === 'security');
  if (security) chips.push({ id: 'security', label: 'Security', status: security.state === 'AVAILABLE' ? 'Available' : security.state === 'DEGRADED' ? 'Degraded' : security.state === 'UNAVAILABLE' ? 'Unavailable' : security.state, tone: security.state === 'AVAILABLE' ? 'ok' : security.state === 'DEGRADED' ? 'warn' : security.state === 'UNAVAILABLE' ? 'bad' : 'neutral', note: security.detail });
  return chips;
}

export interface ProjectBarProps {
  name: string;
  path: string;
  pre: ExecutionPreflight | null;
  probe: Availability<ExecutionModelProbe>;
  composed: Readonly<Record<string, ComposedCell | null>>;
  inspectorOpen: boolean;
  /** The readiness verdict of the idle page (Kriya's own preflight, summarised): Ready, Action required, Cannot start; null while a run is shown. */
  verdict: 'ready' | 'action' | 'blocked' | null;
}

/** The PROJECT BAR: where am I (name, path), what can I do with the folder (Open in Finder, Open in Editor), is it ready (four chips), what is available (Capabilities), Settings. Always visible. */
export function ProjectBar({ name, path, pre, probe, composed, inspectorOpen, verdict }: ProjectBarProps) {
  const ui = useWorkspaceUi();
  const go = useNavigate();
  const chips = pre ? readinessChips(pre, probe) : [];
  const showTab = () => { ui.setTab('project'); ui.setInspectorOpen(true); };
  return (
    <div className="ck-ws-bar" role="group" aria-label="Project">
      <div className="ck-ws-bar-row">
        <h1 title={name}>{name}</h1>
        <span className="ck-ws-path mono small muted" title={path}>{path}</span>
        {verdict === 'ready' ? <Badge tone="ok">✓ Ready</Badge> : verdict === 'action' ? <Badge tone="warn">Action required</Badge> : verdict === 'blocked' ? <Badge tone="bad">Cannot start</Badge> : null}
        <RevealButton />
        <OpenInEditorButton />
        <span className="grow" />
        {pre?.health ? <CapabilitiesControl health={pre.health} composed={composed} memory={pre.memory} /> : null}
        <button type="button" className="ck-quiet" onClick={() => go('settings')}>Settings ⚙</button>
        <button type="button" className="ck-quiet" aria-pressed={inspectorOpen} onClick={() => ui.setInspectorOpen(!inspectorOpen)}>{inspectorOpen ? 'Hide Inspector' : 'Inspector'}</button>
      </div>
      {chips.length ? (
        <ul className="ck-ws-chips" aria-label="Project readiness" data-testid="readiness-chips">
          {chips.map((c) => (
            <li key={c.id} data-chip={c.id} data-tone={c.tone}>
              <button type="button" className="ck-chip" title={`${c.label}: ${c.status}${c.note ? ` - ${c.note}` : ''}. Click for the details.`} onClick={showTab}>
                <span aria-hidden="true">{MARK[c.tone]}</span> {c.label} <span className="muted small">{c.status}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
