import { useState } from 'react';
import { Badge, hasData, type Availability } from '../../core';
import type { ExecutionContext, ExecutionModelProbe } from '../../../model/kup';

type Routing = ExecutionContext['routing'][number];
type Skill = NonNullable<ExecutionContext['skills']>['items'][number];
type ProbeRole = ExecutionModelProbe['roles'][number];

const ROLE_LABEL: Record<string, string> = { planner: 'Planner', architect: 'Architect', developer: 'Developer', reviewer: 'Reviewer', run_verifier: 'Run verifier', skill_gap: 'Skill-gap check', spec_compliance: 'Spec compliance' };
const SKILLS_SHOWN = 12;

function why<T>(a: Availability<T>, what: string): string | null {
  switch (a.state) {
    case 'loading': return `Asking Kriya about ${what}…`;
    case 'error': return `${what} could not be read: ${a.message} (${a.code})`;
    case 'unavailable': return a.detail;
    case 'not_recorded': return a.detail;
    default: return null;
  }
}

function UnavailableContext({ data }: { data: ExecutionContext }) {
  return <p className="muted" role="status">Kriya could not resolve this folder&apos;s configuration{data.unavailable_reason ? `: ${data.unavailable_reason.message} (${data.unavailable_reason.code})` : ''}. Skills, tools and model routing are shown once it loads.</p>;
}

/** How a configured model/endpoint relates to what the probe saw. Matched on (model, endpoint) only; the probe never guesses. */
export function probeStateFor(row: Pick<Routing, 'model' | 'endpoint'>, probe: Availability<ExecutionModelProbe>): ProbeRole['state'] | 'checking' | 'unknown' {
  if (probe.state === 'loading') return 'checking';
  if (!hasData(probe) || !probe.data.available) return 'unknown';
  return probe.data.roles.find((r) => r.model === row.model && r.endpoint === row.endpoint)?.state ?? 'unknown';
}

const PROBE_TEXT: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'neutral' }> = {
  model_present: { label: 'served', tone: 'ok' }, model_missing: { label: 'not served by the server', tone: 'bad' }, unreachable: { label: 'server not reachable', tone: 'bad' },
  error: { label: 'server error', tone: 'bad' }, unreadable: { label: 'unreadable answer', tone: 'warn' }, not_probed: { label: 'not checked (non-local endpoint)', tone: 'warn' },
  checking: { label: 'checking…', tone: 'info' }, unknown: { label: 'unknown', tone: 'neutral' },
};

export function ModelRouting({ context, probe }: { context: Availability<ExecutionContext>; probe: Availability<ExecutionModelProbe> }) {
  const note = why(context, 'model routing');
  if (note || !hasData(context)) return <p className="muted" role="status">{note}</p>;
  if (!context.data.available) return <UnavailableContext data={context.data} />;
  return (
    <>
      <table className="ck-table" aria-label="Model routing">
        <thead><tr><th scope="col">Role</th><th scope="col">Model</th><th scope="col">Server</th><th scope="col">Source</th><th scope="col">Availability</th></tr></thead>
        <tbody>
          {context.data.routing.map((r) => {
            const state = probeStateFor(r, probe);
            const t = PROBE_TEXT[state] ?? PROBE_TEXT.unknown!;
            return (
              <tr key={r.role}>
                <th scope="row">{ROLE_LABEL[r.role] ?? r.role}</th>
                <td className="mono">{r.model}{r.fallback.length ? <span className="muted small"> → {r.fallback.join(' → ')}</span> : null}</td>
                <td className="mono small">{r.endpoint}</td>
                <td>{r.source === 'override' ? 'role override' : 'default model'}</td>
                <td><Badge tone={t.tone}>{t.label}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">Routing is the operator&apos;s configuration (shown, never changed here). Availability is one bounded check made now; it proves the server answers and lists the model, not that a request will complete.</p>
    </>
  );
}

function SkillRow({ s }: { s: Skill }) {
  return (
    <li>
      <strong>{s.name}</strong> <span className="muted small">{s.category}</span>{' '}
      {s.verified ? <Badge tone="ok">verified{s.verified_at ? ` ${s.verified_at}` : ''}</Badge> : <Badge tone="warn">unverified</Badge>}{' '}
      {s.staged_rule_count > 0 ? <Badge tone="info">{s.staged_rule_count} staged rule{s.staged_rule_count === 1 ? '' : 's'} awaiting review</Badge> : null}
      <div className="muted small">{s.description}{s.verified && s.verified_context ? ` · verified: ${s.verified_context}` : ''}</div>
      <div className="muted small">{s.rule_count} rule{s.rule_count === 1 ? '' : 's'} · {s.origin === 'kriya_install' ? 'part of the Kriya installation' : 'this project or a configured skills folder'}{s.supported_versions !== '*' ? ` · versions ${s.supported_versions}` : ''}{s.tags.length ? ` · ${s.tags.join(', ')}` : ''}</div>
    </li>
  );
}

export function SkillsSummary({ context }: { context: Availability<ExecutionContext> }) {
  const [all, setAll] = useState(false);
  const note = why(context, 'Skills');
  if (note || !hasData(context)) return <p className="muted" role="status">{note}</p>;
  const skills = context.data.skills;
  if (!context.data.available || !skills) return <UnavailableContext data={context.data} />;
  const shown = all ? skills.items : skills.items.slice(0, SKILLS_SHOWN);
  return (
    <>
      <p><strong>Selected automatically by Kriya.</strong> <span className="muted">{skills.selection_note}</span></p>
      {skills.items.length === 0 ? <p className="muted">No Skills were discovered for this folder.</p> : (
        <>
          <ul className="ck-skills" aria-label="Skills Kriya can use">{shown.map((s) => <SkillRow key={s.name} s={s} />)}</ul>
          {skills.items.length > SKILLS_SHOWN ? <button type="button" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${skills.total}`}</button> : null}
          {skills.truncated ? <p className="muted small" role="note">Showing the first {skills.items.length} of {skills.total} Skills.</p> : null}
        </>
      )}
      <p className="muted small">A relevant Skill that is unverified, or a technology in your goal with no Skill, can stop a desktop run: Kriya then needs a knowledge decision, which needs a terminal. {skills.management}</p>
    </>
  );
}

export function ToolsSummary({ context }: { context: Availability<ExecutionContext> }) {
  const note = why(context, 'tools');
  if (note || !hasData(context)) return <p className="muted" role="status">{note}</p>;
  const tools = context.data.tools;
  if (!context.data.available || !tools) return <UnavailableContext data={context.data} />;
  const net = (n: string) => (n === 'denied' ? 'no network' : `network: ${n}`);
  return (
    <>
      <p>Managed by Kriya / operator configuration. <span className="muted">{tools.management}</span></p>
      <p>Plugins enabled: {tools.plugins.enabled.length ? tools.plugins.enabled.map((p) => <span key={p} className="mono">{p} </span>) : <span className="muted">none configured</span>}<span className="muted small"> (directory {tools.plugins.directory})</span></p>
      {tools.mcp_servers.length === 0 ? <p className="muted">No MCP servers are configured for this folder.</p> : (
        <ul className="ck-skills" aria-label="MCP servers">
          {tools.mcp_servers.map((m) => (
            <li key={m.name}>
              <strong className="mono">{m.name}</strong> <Badge tone="neutral">health not checked</Badge>
              <div className="muted small">declared: {m.declared_capabilities.workspace_read ? 'reads the workspace' : 'no workspace read'} · {m.declared_capabilities.workspace_write ? 'writes the workspace' : 'no workspace write'} · {net(m.declared_capabilities.network)}</div>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">{tools.health_note} Invocation approval for an MCP tool is granted by the operator (<span className="mono">kriya mcp approve</span>); this application never starts a server, shows a command or a credential, or approves a tool.</p>
    </>
  );
}
