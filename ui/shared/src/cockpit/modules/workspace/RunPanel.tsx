import { useState } from 'react';
import { Badge, KeyValue, Panel, hasData, useExecution, useWorkspacePath, type Availability, type PreflightState } from '../../core';
import type { ExecutionModelProbe, ExecutionPreflight } from '../../../model/kup';
import { useRunSetup } from './runSetup';
import { ModelRouting, SkillsSummary, ToolsSummary } from './SetupSections';

const GOAL_MAX = 16000;
const SOURCES: Record<string, string> = {
  packaged_default: 'Packaged defaults (this folder has no kriya.yaml)',
  auto_discovered_cwd: 'kriya.yaml in this folder',
  auto_discovered_install_dir: 'kriya.yaml in the Kriya installation',
};
const STATE_SOURCE: Record<string, string> = { env: 'KRIYA_STATE_DIR', config: 'paths.state in the configuration', default: 'Kriya default (~/.kriya/state)' };
const AUTHORITY: Record<string, string> = {
  no_findings: 'No findings: this folder\'s configuration sets nothing only an operator may set',
  approved: 'Operator-only fields are present and covered by a durable approval',
  refused: 'Refused: operator-only fields need an approval (kriya authority approve)',
  failed: 'The configuration could not be loaded',
};

/** Who can fix a blocker, keyed on Kriya's closed blocker codes. The code and Kriya's own message are always shown too; this only says what authority acts next. */
const BLOCKER_FIX: Record<string, string> = {
  CONFIG_AUTHORITY_REFUSED: 'An operator must approve those fields with `kriya authority approve` in a terminal; this application cannot.',
  CONFIG_LOAD_FAILED: 'Fix the kriya.yaml in this folder (or the Kriya installation). Kriya will not guess a configuration.',
  RUN_ACTIVE: 'Wait for the other run to finish; one run owns a folder at a time.',
  RECOVERY_REQUIRED: 'The last run left the folder mid-change. Recover it with `kriya runs recover` in a terminal, then check again.',
  STATE_DIRECTORY_INVALID: 'The state location comes from the operator environment (KRIYA_STATE_DIR) or configuration; an operator must fix it.',
  STATE_DIRECTORY_NOT_OBSERVABLE: 'A run here would record somewhere this application does not read. An operator must align KRIYA_STATE_DIR.',
  TOOL_UNAVAILABLE: 'Install or repair the tool (on macOS, accepting the Xcode license makes /usr/bin/git work), then check again.',
};

const AUTHORITY_TONE = { no_findings: 'ok', approved: 'warn', refused: 'bad', failed: 'bad' } as const;

interface Row { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'neutral'; status: string; detail?: string }

/** The model-server row from the probe: only what the probe measured. */
function modelRow(probe: Availability<ExecutionModelProbe>): Row {
  if (probe.state === 'loading') return { label: 'Model server', tone: 'info', status: 'Checking…' };
  if (!hasData(probe)) return { label: 'Model server', tone: 'neutral', status: 'Not checked', detail: probe.state === 'error' ? `${probe.message} (${probe.code})` : undefined };
  if (!probe.data.available) return { label: 'Model server', tone: 'neutral', status: 'Not checked', detail: 'The configuration did not load, so there is nothing to check.' };
  const bad = probe.data.roles.filter((r) => r.state === 'unreachable' || r.state === 'error' || r.state === 'model_missing');
  const unchecked = probe.data.roles.filter((r) => r.state === 'not_probed' || r.state === 'unreadable');
  if (bad.length) {
    const down = probe.data.endpoints.filter((e) => e.state !== 'reachable' && e.state !== 'not_probed');
    const missing = bad.filter((r) => r.state === 'model_missing');
    const detail = [down.length ? `${down.map((e) => `${e.endpoint}: ${e.state}${e.reason ? ` (${e.reason})` : ''}`).join('; ')}. Start the model server (for example Ollama); this application cannot.` : '',
      missing.length ? `Not served: ${[...new Set(missing.map((r) => r.model))].join(', ')}. Pull or load the model on the server.` : ''].filter(Boolean).join(' ');
    return { label: 'Model server', tone: 'bad', status: down.length ? 'Not reachable' : 'A configured model is not served', detail };
  }
  if (unchecked.length) return { label: 'Model server', tone: 'warn', status: 'Not checked', detail: unchecked.map((r) => `${r.model} at ${r.endpoint}: ${r.state === 'not_probed' ? 'non-local endpoint, not contacted under local_only' : 'unreadable answer'}`).join('; ') };
  return { label: 'Model server', tone: 'ok', status: 'Answering; configured models are served', detail: `${probe.data.endpoints.map((e) => e.endpoint).join(', ')} (checked now)` };
}

function checklist(pre: ExecutionPreflight, probe: Availability<ExecutionModelProbe>): Row[] {
  const c = pre.configuration;
  const git = pre.toolchains.tools.find((t) => t.tool === 'git');
  const rows: Row[] = [
    { label: 'Workspace', tone: pre.recovery.status === 'CLEAN' ? 'ok' : 'bad', status: pre.recovery.status === 'CLEAN' ? 'Clean' : `Recovery state ${pre.recovery.status}`, detail: pre.workspace.name },
    { label: 'Configuration', tone: c.status === 'loaded' ? (AUTHORITY_TONE[c.authority.status as keyof typeof AUTHORITY_TONE] ?? 'warn') : 'bad', status: AUTHORITY[c.authority.status] ?? c.authority.status, detail: SOURCES[c.source] ?? c.source },
    { label: 'Run state', tone: pre.state.directory ? (pre.state.observable_by_cockpit === false ? 'bad' : 'ok') : 'bad', status: pre.state.directory ? (pre.state.observable_by_cockpit === false ? 'Not read by this app' : 'Recorded where this app reads') : 'Not resolved', detail: pre.state.directory ?? undefined },
    { label: 'Git (needed by every run)', tone: git ? (git.available ? 'ok' : 'bad') : 'neutral', status: git ? (git.available ? 'Available' : 'Unavailable') : 'Not reported', detail: git && !git.available && typeof git.reason === 'string' ? git.reason : undefined },
    modelRow(probe),
    { label: 'Approvals', tone: pre.interaction.approvals === 'change_review' ? 'ok' : pre.interaction.approvals === 'configured_mode' ? 'warn' : 'neutral', status: pre.interaction.approvals === 'change_review' ? 'Every change is held for your review' : pre.interaction.approvals === 'configured_mode' ? `Autonomy mode ${pre.interaction.autonomy_mode}` : 'Unknown', detail: pre.interaction.note },
  ];
  return rows;
}

function ToolsLine({ data }: { data: ExecutionPreflight }) {
  const tools = data.toolchains.tools;
  const seen = tools.filter((t) => t.available).map((t) => t.tool);
  const missing = tools.filter((t) => !t.available);
  const why = missing.map((t) => `${t.tool}: ${typeof t.reason === 'string' ? t.reason : 'not found'}`).join('; ');
  return <span title={`${data.toolchains.note}${why ? ` Unavailable - ${why}` : ''}`}>{seen.length ? seen.join(', ') : 'none'}{missing.length ? <span className="muted"> · unavailable: {missing.map((t) => t.tool).join(', ')}</span> : null}</span>;
}

/** Kriya's execution preflight, compactly and in its own words. The cockpit resolves nothing: every line is a field of the answer. */
export function PreflightSummary({ data }: { data: ExecutionPreflight }) {
  const c = data.configuration;
  const model = data.model;
  return (
    <KeyValue items={[
      ['Workspace', <span key="w"><strong>{data.workspace.name}</strong> <span className="muted mono" title={data.workspace.identity}>{data.workspace.identity.slice(0, 12)}…</span></span>],
      ['Configuration', <span key="c">{SOURCES[c.source] ?? c.source}{c.file ? <span className="muted mono"> · {c.file}</span> : null}</span>],
      ['Authority', <Badge key="a" tone={c.authority.status === 'refused' || c.authority.status === 'failed' ? 'bad' : c.authority.status === 'approved' ? 'warn' : 'ok'}>{AUTHORITY[c.authority.status] ?? c.authority.status}</Badge>],
      ['State directory', data.state.directory ? <span key="s"><span className="mono">{data.state.directory}</span> <span className="muted">({STATE_SOURCE[data.state.source ?? ''] ?? data.state.source})</span>{data.state.observable_by_cockpit === false ? <Badge tone="bad"> not read by this cockpit</Badge> : null}</span> : 'Not resolved'],
      ['Models', model ? <span key="m"><span className="mono">{model.model}</span> <span className="muted">via {model.provider} at {model.endpoint}, context {model.context_window}{model.fallback_chain.length ? `, fallback ${model.fallback_chain.join(' → ')}` : ''}</span></span> : 'Not resolved (configuration did not load)'],
      ['Approvals', data.interaction.note],
      ['Tools a run can see', <ToolsLine key="t" data={data} />],
      ['Configuration digest', c.fingerprint ? <span key="f" className="mono" title={c.fingerprint}>{c.fingerprint.slice(0, 16)}…</span> : 'Not recorded'],
    ]} />
  );
}

function preflightBody(state: PreflightState) {
  switch (state.state) {
    case 'unavailable': return <p className="muted" role="status">{state.reason}</p>;
    case 'idle':
    case 'loading': return <p className="muted" role="status">Asking Kriya what a run here would be…</p>;
    case 'error': return <div className="ck-state" role="alert"><strong>The execution preflight could not be read</strong>{state.error.message} <span className="muted small">({state.error.code})</span></div>;
    case 'ready': return <PreflightSummary data={state.data} />;
    default: return null;
  }
}

function ReadinessChecklist({ pre, probe }: { pre: ExecutionPreflight; probe: Availability<ExecutionModelProbe> }) {
  const rows = checklist(pre, probe);
  const warn = rows.find((r) => r.tone === 'bad' && pre.ready);
  return (
    <section aria-label="Readiness" className="ck-readiness">
      <div className="ck-readiness-head">
        <strong>Readiness</strong>{' '}
        {!pre.ready ? <Badge tone="bad">not ready</Badge> : warn ? <Badge tone="warn">ready to start, with a warning</Badge> : <Badge tone="ok">ready</Badge>}
        <span className="muted small"> Facts Kriya reported for this folder, each with its source.</span>
      </div>
      <ul className="ck-checks">
        {rows.map((r) => (
          <li key={r.label}>
            <span className="ck-check-label">{r.label}</span> <Badge tone={r.tone}>{r.status}</Badge>
            {r.detail ? <div className="muted small">{r.detail}</div> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Goal entry, Kriya's readiness, what the run would have (Skills, tools, model routing), and Start Run. Not a chat: one engineering goal starts one Kriya run. */
export function RunPanel() {
  const ex = useExecution();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const { context, probe } = useRunSetup(workspace, nonce);
  const pre = ex.preflight.state === 'ready' ? ex.preflight.data : null;
  const blockers = pre ? pre.blockers : [];
  const empty = ex.goal.trim().length === 0;
  const canStart = ex.available && pre !== null && pre.ready && !empty && !ex.locked && ex.goal.length <= GOAL_MAX;
  const recheck = () => { setNonce((n) => n + 1); ex.refreshPreflight(); };
  const skillCount = hasData(context) && context.data.skills ? context.data.skills.total : null;
  const unverified = hasData(context) && context.data.skills ? context.data.skills.items.filter((k) => !k.verified).length : 0;
  const mcp = hasData(context) && context.data.tools ? context.data.tools.mcp_servers.length : null;
  return (
    <Panel title="Start a run">
      {!ex.available ? <p className="muted" role="status">This host cannot start a run.</p> : (
        <>
          <label htmlFor="ck-goal" className="ck-label">Engineering goal</label>
          <textarea id="ck-goal" className="ck-goal" rows={5} value={ex.goal} maxLength={GOAL_MAX} disabled={ex.locked}
            placeholder="Describe the engineering task, e.g. “Add a clamp(value, low, high) function with tests.”"
            onChange={(e) => ex.setGoal(e.target.value)} aria-describedby="ck-goal-help" />
          <div id="ck-goal-help" className="muted small">One goal starts one Kriya run through its normal planning, generation and verification. {ex.goal.length}/{GOAL_MAX}</div>
          {pre ? <ReadinessChecklist pre={pre} probe={probe} /> : preflightBody(ex.preflight)}
          {blockers.length ? (
            <div className="ck-state" role="alert">
              <strong>Kriya will not start a run here yet</strong>
              <ul>{blockers.map((b) => <li key={b.code}>{b.message} <span className="muted small">({b.code})</span>{BLOCKER_FIX[b.code] ? <div className="muted small">{BLOCKER_FIX[b.code]}</div> : null}</li>)}</ul>
              <button type="button" onClick={recheck}>Check again</button>
            </div>
          ) : (pre ? <p><button type="button" className="ck-quiet" onClick={recheck}>Check again</button></p> : null)}
          <details className="ck-setup">
            <summary>Skills{skillCount === null ? '' : ` · ${skillCount}${unverified ? ` (${unverified} unverified)` : ''}`}</summary>
            <SkillsSummary context={context} />
          </details>
          <details className="ck-setup">
            <summary>Tools and capabilities{mcp === null ? '' : ` · ${mcp} MCP server${mcp === 1 ? '' : 's'}`}</summary>
            <ToolsSummary context={context} />
          </details>
          <details className="ck-setup">
            <summary>Model routing</summary>
            <ModelRouting context={context} probe={probe} />
          </details>
          {pre ? <details className="ck-setup"><summary>Run configuration details</summary><PreflightSummary data={pre} /></details> : null}
          {ex.refusal ? (
            <div className="ck-state" role="alert">
              <strong>The run was not started</strong>
              {ex.refusal.message} <span className="muted small">({ex.refusal.code})</span>
            </div>
          ) : null}
          <p>
            <button type="button" className="ck-primary" disabled={!canStart || ex.starting} onClick={() => void ex.start()}>{ex.starting ? 'Starting…' : 'Start Run'}</button>{' '}
            {ex.locked && !ex.starting ? <span className="muted small">A run is in progress.</span> : null}
            {!ex.locked && empty ? <span className="muted small">Enter a goal to start.</span> : null}
          </p>
        </>
      )}
    </Panel>
  );
}
