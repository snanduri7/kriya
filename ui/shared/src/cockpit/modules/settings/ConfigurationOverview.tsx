import { Badge, KeyValue, Panel } from '../../core';
import type { ExecutionPreflight } from '../../../model/kup';

type Pre = ExecutionPreflight;
type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral';
const TONE: Record<string, Tone> = { AVAILABLE: 'ok', DEGRADED: 'warn', UNAVAILABLE: 'bad', NOT_REQUIRED: 'neutral', UNKNOWN: 'neutral' };
const WORDS: Record<string, string> = { AVAILABLE: 'In force', DEGRADED: 'Degraded', UNAVAILABLE: 'Unavailable', NOT_REQUIRED: 'Off', UNKNOWN: 'Unknown' };
const ROLE: Record<string, string> = { planner: 'Planner', architect: 'Architect', reviewer: 'Reviewer', run_verifier: 'Run verifier', skill_gap: 'Skill-gap check', spec_compliance: 'Spec compliance' };
const APPROVALS: Record<string, string> = { change_review: 'Every change is held for your review before it is applied', configured_mode: 'Applied under the configured autonomy mode', unknown: 'Unknown', unavailable: 'Unavailable' };

function HealthLine({ pre, id }: { pre: Pre; id: string }) {
  const row = pre.health?.find((r) => r.id === id);
  if (!row) return <span className="muted">not reported by this Kriya</span>;
  return <span title={`${row.reason_code}; evidence: ${row.evidence.join(', ') || 'none'}`}><Badge tone={TONE[row.state] ?? 'neutral'}>{WORDS[row.state] ?? row.state}</Badge> <span className="muted small">{row.detail}</span></span>;
}

/**
 * How Kriya is configured for the open project, in customer terms. READ-ONLY: every value is a field of Kriya's `execution.preflight` answer (the resolved configuration), and
 * no setting is written from here - operator-controlled settings change only through the folder's kriya.yaml and Kriya's own approval (`kriya authority`), the canonical writer.
 */
export function ConfigurationOverview({ pre }: { pre: Pre }) {
  const model = pre.model;
  const overrides = model ? Object.entries(model.role_overrides) : [];
  return (
    <section aria-label="How Kriya is configured" data-testid="configuration-overview">
      <Panel title="Models">
        {model ? (
          <KeyValue items={[
            ['Runtime', <span key="r">{model.provider} at <span className="mono">{model.endpoint}</span></span>],
            ['Planner', overrides.find(([role]) => role === 'planner')?.[1] ?? `${model.model} (the default model)`],
            ['Developer', `${model.model} (the default model)`],
            ...overrides.filter(([role]) => role !== 'planner').map(([role, name]) => [ROLE[role] ?? role, name] as const),
            ['If the model is unavailable', model.fallback_chain.length ? model.fallback_chain.join(' → ') : 'no fallback configured'],
            ['Context window', `${model.context_window.toLocaleString('en-US')} tokens`],
          ]} />
        ) : <p className="muted">The configuration did not load, so no model is resolved.</p>}
        <p className="muted small">Whether the model server is answering is checked on the Project page (&quot;Local model&quot;).</p>
      </Panel>
      <Panel title="Privacy and network">
        <KeyValue items={[
          ['Outward lookup', <HealthLine key="l" pre={pre} id="lookup" />],
          ['Where run state lives', pre.state.directory ? <span key="s" className="mono">{pre.state.directory}</span> : 'not resolved'],
          ['State protection', <HealthLine key="p" pre={pre} id="private_state" />],
        ]} />
      </Panel>
      <Panel title="Execution">
        <KeyValue items={[
          ['Containment', <HealthLine key="c" pre={pre} id="security" />],
          ['Approvals', APPROVALS[pre.interaction.approvals] ?? pre.interaction.approvals],
          ['Runtime verification', <HealthLine key="v" pre={pre} id="verification" />],
          ['Recovery', <HealthLine key="rc" pre={pre} id="recovery" />],
        ]} />
      </Panel>
      <Panel title="Knowledge">
        <KeyValue items={[
          ['Skills', 'Listed on the Skills page, with what each one is verified against'],
          ['Verified Project Memory', pre.memory ? `${pre.memory.counts.current} current · ${pre.memory.counts.stale} stale (store: ${pre.memory.store_state})` : 'not reported by this Kriya'],
        ]} />
      </Panel>
      <Panel title="Code intelligence">
        <KeyValue items={[['Structural index', <HealthLine key="i" pre={pre} id="code_intelligence" />], ['Java language server', <HealthLine key="j" pre={pre} id="lsp" />]]} />
      </Panel>
      <p className="muted small" role="note">This page only shows the resolved configuration ({pre.configuration.source === 'packaged_default' ? 'packaged defaults' : pre.configuration.file ?? pre.configuration.source}). Operator-controlled settings are changed in the folder&apos;s kriya.yaml and approved with Kriya&apos;s own authority command; nothing here writes them.</p>
    </section>
  );
}
