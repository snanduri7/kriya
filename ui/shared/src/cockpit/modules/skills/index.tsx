import { useState } from 'react';
import { Badge, CopyText, KeyValue, Panel, hasData, toAvailability, useAsync, useProvider, useWorkspacePath, UnavailableConfiguration, UnknownFields, whyNot, type Availability, type ModuleDefinition } from '../../core';
import type { SkillDescriptor, SkillsList } from '../../../model/kup';

const UNKNOWN_EXPLAIN: Record<string, string> = {
  skill_version: 'a skill has no version of its own (only the technology versions it supports)',
  digest: 'the content digest could not be computed',
};
const SCOPE_TEXT: Record<SkillDescriptor['scope'], string> = {
  workspace: 'This project (the open folder)',
  install: 'The Kriya installation (shared by every project)',
  configured: 'A configured skills folder outside the project',
};
const PROVENANCE_TEXT: Record<SkillDescriptor['provenance']['state'], string> = {
  NONE_RECORDED: 'No provenance is recorded. These rules are pre-existing content, which is not the same as verified.',
  RECORDED: 'Provenance is recorded for some or all rules.',
  UNREADABLE_OR_EMPTY: 'A provenance file exists but yields no usable record for the current rules.',
};

function VerifiedBadge({ s }: { s: SkillDescriptor }) {
  return s.verified ? <Badge tone="ok">verified{s.verified_at ? ` ${s.verified_at}` : ''}</Badge> : <Badge tone="warn">unverified</Badge>;
}

function SkillDetail({ workspace, id, nonce }: { workspace: string; id: string; nonce: number }) {
  const provider = useProvider();
  const detail = useAsync(`skill:${workspace}:${id}:${nonce}`, async (signal) => toAvailability(await provider.skillsDescribe(workspace, id, signal)), { state: 'loading' } as Availability<never>);
  const note = whyNot(detail, 'this Skill');
  if (note || !hasData(detail)) return <p className="muted" role="status">{note}</p>;
  if (!detail.data.available) return <UnavailableConfiguration reason={detail.data.unavailable_reason} />;
  const s = detail.data.skill;
  if (!detail.data.found || !s) return <p className="muted" role="status">Kriya no longer lists a Skill named {id}. Refresh the list.</p>;
  const p = s.provenance;
  return (
    <section aria-label={`Skill ${s.name}`}>
      <h4 style={{ margin: '0 0 6px' }}>{s.name} <VerifiedBadge s={s} /></h4>
      <p>{s.description}</p>
      <KeyValue items={[
        ['Category', s.category],
        ['Supports versions', s.supported_versions === '*' ? 'Any version' : s.supported_versions],
        ['Skill version', 'Not recorded (a skill has no version of its own)'],
        ['Scope', SCOPE_TEXT[s.scope]],
        ['Origin', s.origin === 'kriya_install' ? 'Part of the Kriya installation' : 'This project or a configured skills folder'],
        ['Source', s.source === 'local_directory' ? 'A local skills folder' : s.source],
        ['Digest', s.digest ? <span key="d"><span className="mono" title={s.digest}>{s.digest.slice(0, 19)}…</span> <CopyText text={s.digest} what="skill digest" /></span> : 'Could not be computed'],
        ['Verified', s.verified ? `Yes${s.verified_at ? `, on ${s.verified_at}` : ''}${s.verified_context ? ` (${s.verified_context})` : ''}` : s.verification_gap_acknowledged ? 'No (the gap was acknowledged)' : 'No'],
        ['Rules', `${s.rule_count} approved${s.staged_rule_count ? `, ${s.staged_rule_count} staged and awaiting review` : ''}`],
        ['Examples / instructions', `${s.example_count} example file${s.example_count === 1 ? '' : 's'} · ${s.has_instructions ? 'has instructions' : 'no instructions'}`],
      ]} />
      <h5 style={{ margin: '12px 0 4px' }}>Provenance</h5>
      <p className="muted small">{PROVENANCE_TEXT[p.state]}</p>
      <KeyValue items={[
        ['Rules with a recorded source', `${p.tracked_rule_count} of ${s.rule_count}`],
        ['Rules without one', String(p.untracked_rule_count)],
        ['Of the recorded, verified', String(p.verified_tracked_count)],
      ]} />
      {p.sources.length === 0 ? null : (
        <table className="ck-table" aria-label={`Sources of ${s.name}`}>
          <thead><tr><th>Source (as recorded)</th><th>Rules</th><th>Verified</th><th>Added</th></tr></thead>
          <tbody>
            {p.sources.map((src) => (
              <tr key={src.source}>
                <td><span className="mono small" style={{ overflowWrap: 'anywhere' }}>{src.source}</span> <CopyText text={src.source} what="source" /></td>
                <td>{src.rule_count}</td><td>{src.verified_count}</td>
                <td className="small">{src.first_added ?? '—'}{src.last_added && src.last_added !== src.first_added ? ` – ${src.last_added}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {p.sources_truncated ? <p className="muted small" role="note">Only the first sources are shown.</p> : null}
      <UnknownFields names={s.unknown_fields} explain={UNKNOWN_EXPLAIN} />
      <p className="muted small">The text of the rules is not shown here: it is acquired content, kept in the skill&apos;s folder.</p>
    </section>
  );
}

function SkillsTable({ list, selected, onSelect }: { list: SkillsList; selected: string | null; onSelect(id: string): void }) {
  return (
    <table className="ck-table" aria-label="Skills">
      <thead><tr><th>Skill</th><th>Verified</th><th>Rules</th><th>Staged</th><th>Scope</th></tr></thead>
      <tbody>
        {list.items.map((s) => (
          <tr key={s.id} aria-selected={s.id === selected}>
            <td><button type="button" className="ck-quiet" aria-pressed={s.id === selected} aria-label={`Show ${s.name}`} onClick={() => onSelect(s.id)}><strong>{s.name}</strong></button><div className="muted small">{s.description}</div></td>
            <td><VerifiedBadge s={s} /></td>
            <td>{s.rule_count}</td>
            <td>{s.staged_rule_count > 0 ? <Badge tone="info">{s.staged_rule_count}</Badge> : 0}</td>
            <td className="small">{s.scope}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Skills a run in the open folder would load. Read-only: visibility and provenance, no edit, install, remove or enable (those are not part of this build). */
function Skills() {
  const provider = useProvider();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const [chosen, setChosen] = useState<string | null>(null);
  const list = useAsync(workspace === null ? null : `skills:${workspace}:${nonce}`, async (signal) => toAvailability(await provider.skillsList(workspace!, signal)), { state: 'loading' } as Availability<never>);
  if (workspace === null) return <Panel title="Skills"><div className="ck-state"><strong>No workspace is open</strong>Open a folder to see the Skills Kriya would use there.</div></Panel>;
  const note = whyNot(list, 'Skills');
  let body;
  if (note || !hasData(list)) body = <p className="muted" role="status">{note}</p>;
  else if (!list.data.available) body = <UnavailableConfiguration reason={list.data.unavailable_reason} />;
  else if (list.data.items.length === 0) body = <p className="muted" role="status">No Skills were discovered for this folder.</p>;
  else {
    const selected = chosen !== null && list.data.items.some((s) => s.id === chosen) ? chosen : null;
    body = (
      <>
        <SkillsTable list={list.data} selected={selected} onSelect={setChosen} />
        {list.data.truncated ? <p className="muted small" role="note">Showing the first {list.data.items.length} of {list.data.total} Skills.</p> : null}
        {selected ? <SkillDetail workspace={workspace} id={selected} nonce={nonce} /> : <p className="muted small">Choose a Skill to see its provenance.</p>}
      </>
    );
  }
  return (
    <Panel title="Skills">
      <p className="muted">Kriya selects Skills automatically for each run from the goal and the repository; there is no manual selection. This page shows what is there and where it came from.
        {' '}<button type="button" className="ck-quiet" onClick={() => setNonce((n) => n + 1)}>Refresh</button></p>
      {body}
      <p className="muted small">Read-only. Skills are created, reviewed and promoted with <span className="mono">kriya skills</span> in a terminal; this application does not change them.</p>
    </Panel>
  );
}

export const skillsModule: ModuleDefinition = {
  id: 'skills',
  placement: { page: 'skills', surface: 'page', label: 'Skills', order: 10 },
  requiredCapabilities: ['op:skills.list', 'op:skills.describe'], optionalCapabilities: [], selectionDependencies: [],
  render: Skills,
};
