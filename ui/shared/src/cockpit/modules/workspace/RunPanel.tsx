import { Badge, KeyValue, Panel, useExecution, type PreflightState } from '../../core';
import type { ExecutionPreflight } from '../../../model/kup';

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

/** Goal entry + Kriya's preflight + Start Run. Not a chat: one engineering goal starts one Kriya run. */
export function RunPanel() {
  const ex = useExecution();
  const pre = ex.preflight.state === 'ready' ? ex.preflight.data : null;
  const blockers = pre ? pre.blockers : [];
  const empty = ex.goal.trim().length === 0;
  const canStart = ex.available && pre !== null && pre.ready && !empty && !ex.locked && ex.goal.length <= GOAL_MAX;
  return (
    <Panel title="Start a run">
      {!ex.available ? <p className="muted" role="status">This host cannot start a run.</p> : (
        <>
          <label htmlFor="ck-goal" className="ck-label">Engineering goal</label>
          <textarea id="ck-goal" className="ck-goal" rows={5} value={ex.goal} maxLength={GOAL_MAX} disabled={ex.locked}
            placeholder="Describe the engineering task, e.g. “Add a clamp(value, low, high) function with tests.”"
            onChange={(e) => ex.setGoal(e.target.value)} aria-describedby="ck-goal-help" />
          <div id="ck-goal-help" className="muted small">One goal starts one Kriya run through its normal planning, generation and verification. {ex.goal.length}/{GOAL_MAX}</div>
          {preflightBody(ex.preflight)}
          {blockers.length ? (
            <div className="ck-state" role="alert">
              <strong>Kriya will not start a run here yet</strong>
              <ul>{blockers.map((b) => <li key={b.code}>{b.message} <span className="muted small">({b.code})</span></li>)}</ul>
              <button type="button" onClick={ex.refreshPreflight}>Check again</button>
            </div>
          ) : null}
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
