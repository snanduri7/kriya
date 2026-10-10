import { useState } from 'react';
import {
  AvailabilityView, Badge, KeyValue, Panel, activeSkills, callRows, contentAvailability, contextFacts, promptSections, runEventsOf, shortDigest, useAsync, useCapabilities, useDispatch, useModuleSelection, useProvider,
  useRunDetail, useRunEvidence, attemptFor, type CallRow, type ModuleDefinition, type RunEvidence, type EvidenceCallContent,
} from '../../core';
import { sanitizeText } from '../../../render/sanitize';

const n = (v: number | null) => (v === null ? 'not recorded' : String(v));

/** Percentage only when BOTH the provider-reported used tokens and the served capacity are recorded numbers; it is DERIVED and says so. */
export function contextPercent(call: CallRow): { percent: number; used: number; capacity: number } | null {
  if (call.promptTokensReported === null || call.servedContextWindow === null || call.servedContextWindow <= 0) return null;
  return { percent: Math.round((call.promptTokensReported / call.servedContextWindow) * 1000) / 10, used: call.promptTokensReported, capacity: call.servedContextWindow };
}

function PromptContent({ call, evidence }: { call: CallRow; evidence: RunEvidence }) {
  const provider = useProvider();
  const caps = useCapabilities();
  const [asked, setAsked] = useState<string | null>(null);
  const key = asked === String(call.requestSeq) ? asked : null;
  const content = useAsync<EvidenceCallContent>(key, async (signal) => contentAvailability(await provider.evidenceCallContent(evidence.evidenceRunId, { seq: call.requestSeq!, part: 'messages' }, signal)), { state: 'loading' });
  if (call.requestSeq === null) return <p className="muted">The recorder kept no wire request for this call (no model.request record), so the prompt text is not available. The sections and budget above are what was recorded.</p>;
  if (call.requestDigestOnly.includes('messages') && !call.requestKept.includes('messages')) return <AvailabilityView value={{ state: 'unavailable', reason: 'digest_only', detail: 'This run kept a digest of the prompt, not the prompt text.' }}>{() => null}</AvailabilityView>;
  if (!call.requestKept.includes('messages')) return <p className="muted">The request record kept no prompt messages.</p>;
  if (!caps.has('evidence_content')) return <AvailabilityView value={{ state: 'unavailable', reason: 'denied', detail: "Kriya's content visibility policy does not authorize showing stored content. Everything above is complete without it." }}>{() => null}</AvailabilityView>;
  if (key === null) return <button type="button" onClick={() => setAsked(String(call.requestSeq))}>Show prompt messages</button>;
  return (
    <AvailabilityView value={content}>
      {(data) => (
        <div>
          <pre className="mono" aria-label="Prompt messages" style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 360, overflow: 'auto' }}>{sanitizeText(data.content)}</pre>
          <p className="muted small" aria-label="Content provenance">{data.part} · {data.bytes_total} bytes · {data.encoding} · digest verified {shortDigest(data.ref)} · {data.role ?? 'role not recorded'} call {data.call_seq ?? '-'} · attempt {data.attempt ?? '-'}</p>
        </div>
      )}
    </AvailabilityView>
  );
}

function Detail({ call, data, attempt }: { call: CallRow; data: RunEvidence; attempt: number | null }) {
  const detail = useRunDetail();
  const events = runEventsOf(detail.state === 'ready' ? detail.data : null);
  const facts = contextFacts(events, call.attempt ?? attempt);
  const sections = promptSections(data.records).filter((s) => s.attempt === call.attempt);
  const skills = activeSkills(detail.state === 'ready' ? detail.data : null);
  const pct = contextPercent(call);
  const requestSection = sections.find((s): s is Extract<typeof s, { kind: 'request' }> => s.kind === 'request');
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <Panel title="Invocation">
        <KeyValue items={[['Attempt / unit', `${n(call.attempt)} / ${call.unitId ?? 'not recorded'}`], ['Role', call.role ?? 'not recorded'], ['Model', call.model ?? 'not recorded'], ['Runtime fingerprint', call.runtimeFingerprint ? shortDigest(call.runtimeFingerprint) : 'not recorded'],
          ['Outcome', `${call.finishReason ?? 'finish reason not recorded'}${call.backendStatus ? ` (backend ${call.backendStatus})` : ''}`], ['Elapsed', call.elapsedSeconds === null ? 'not recorded' : `${Math.round(call.elapsedSeconds * 1000) / 1000}s`]]} />
      </Panel>
      <Panel title="Budget and tokens">
        <KeyValue items={[
          ['Context window served', call.servedContextWindow === null ? 'not recorded' : `${call.servedContextWindow}${call.servedContextProvenance ? ` (${call.servedContextProvenance})` : ''}`],
          ['Prompt tokens, estimated by Kriya', n(call.promptTokensEstimated)], ['Prompt tokens, reported by the provider', n(call.promptTokensReported)], ['Completion tokens', n(call.completionTokens)], ['Required tokens (budget)', n(call.requiredTokens)],
          ['Context use', pct ? <span key="p">{pct.percent}% ({pct.used} of {pct.capacity}) <Badge tone="info" title="reported prompt tokens / served context window">DERIVED</Badge></span> : 'not shown: needs both the reported tokens and the served window'],
        ]} />
      </Panel>
      <Panel title="Prompt sections">
        {requestSection ? (
          <>
            <p className="muted small" style={{ margin: '0 0 4px' }}>{requestSection.request ?? 'request'} · {n(requestSection.requestTokens)} tokens (estimated) of {n(requestSection.capacityTokens)} · fitted: {requestSection.fitted === null ? 'not recorded' : requestSection.fitted ? 'yes (sections were trimmed)' : 'no (all sections kept)'}</p>
            <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Prompt sections table">
              <thead><tr className="muted"><th align="left">section</th><th align="left">chars</th><th align="left">outcome</th><th align="left">digest</th></tr></thead>
              <tbody>{requestSection.segments.map((s) => <tr key={`${s.name}${s.offset}`}><td>{s.name}{s.mandatory ? ' (mandatory)' : ''}</td><td>{n(s.chars)}</td><td><Badge tone={s.outcome === 'kept' ? 'ok' : 'warn'}>{s.outcome ?? 'not recorded'}</Badge></td><td className="mono">{shortDigest(s.digest)}</td></tr>)}</tbody>
            </table>
            {requestSection.unlocated.length ? <p className="muted small">Unlocated sections: {requestSection.unlocated.join(', ')}</p> : null}
          </>
        ) : <p className="muted">No prompt.sections record for this attempt.</p>}
        {sections.filter((s) => s.kind === 'variable').map((s) => s.kind === 'variable' ? <p key={s.seq} className="muted small" style={{ margin: '4px 0 0' }}>Variable section {s.section ?? '?'}: used {n(s.usedTokens)} of room {n(s.roomTokens)} tokens, kept {n(s.keptChars)} chars{s.omitted ? ', omitted' : ''}.</p> : null)}
      </Panel>
      <Panel title="Files and ranges shown">
        {facts.knownTargets ? (
          <>
            <KeyValue items={[['Known target files', facts.knownTargets.files.join(', ') || 'none recorded'], ['Package digest', facts.knownTargets.packageHash ? shortDigest(facts.knownTargets.packageHash) : 'not recorded']]} />
            <ul style={{ margin: '4px 0 0', paddingLeft: 16 }} aria-label="Context tiers">{facts.knownTargets.tiers.map((t, i) => <li key={i}><span className="mono">{t.path}</span> shown as <Badge>{t.tier ?? 'tier not recorded'}</Badge>{t.memberId ? ` (member ${t.memberId})` : ''}</li>)}</ul>
          </>
        ) : <p className="muted">No context package event was recorded for this attempt.</p>}
        {facts.editCapability ? <p className="muted small" style={{ margin: '6px 0 0' }}>Edit operations allowed: {facts.editCapability.targets.map((t) => `${t.path} (${t.operations.join(', ') || 'none'})`).join('; ')}</p> : null}
      </Panel>
      <Panel title="Omissions and reasons">
        {facts.knownTargets?.omitted.length ? <ul style={{ margin: 0, paddingLeft: 16 }} aria-label="Omissions">{facts.knownTargets.omitted.map((o, i) => <li key={i}><span className="mono">{o.path}</span>: <b>{o.reason ?? 'reason not recorded'}</b>{o.estimatedTokens !== null ? ` (about ${o.estimatedTokens} tokens)` : ''}</li>)}</ul> : <p className="muted">{facts.knownTargets ? 'The recorded package lists no omission.' : 'No omission information was recorded.'}</p>}
      </Panel>
      <Panel title="Guidance and skills">
        <KeyValue items={[['Active skills', skills === null ? 'not recorded' : skills.length ? skills.join(', ') : 'none active'], ['Skill / code-context tokens (estimated)', facts.composition ? `${facts.composition.skills_tokens ?? 'n/a'} / ${facts.composition.code_context_tokens ?? 'n/a'}` : 'not recorded']]} />
      </Panel>
      <Panel title="Prompt text"><PromptContent call={call} evidence={data} /></Panel>
    </div>
  );
}

function Context() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const attemptSel = useModuleSelection('attempt');
  const seq = useModuleSelection('eventSeq');
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const calls = callRows(data.records);
        if (!calls.length) return <div className="ck-state"><strong>No model call recorded</strong>The store holds no model.result record, so no invocation's context can be shown.</div>;
        const which = attemptFor(data.records, attemptSel, ['model.result']);
        const mine = calls.filter((c) => c.attempt === which || (which === null && c.attempt === null));
        const list = mine.length ? mine : calls;
        const selected = list.find((c) => c.seq === seq || c.requestSeq === seq) ?? list[0]!;
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <Panel title={which === null ? 'Model invocations' : `Model invocations · attempt ${which}`}>
              <ul className="ck-list" aria-label="Model invocations">
                {list.map((c) => (
                  <li key={c.seq}><button type="button" aria-selected={c.seq === selected.seq} onClick={() => dispatch({ type: 'selectEventSeq', eventSeq: c.seq })}>
                    <span className="mono">#{c.callSeq ?? '?'}</span> {c.role ?? 'role not recorded'} · {c.model ?? 'model not recorded'} <span className="muted small">{c.finishReason ?? ''}</span>
                  </button></li>
                ))}
              </ul>
            </Panel>
            <Detail call={selected} data={data} attempt={which} />
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const contextModule: ModuleDefinition = {
  id: 'context',
  placement: { page: 'runs', surface: 'center-tab', label: 'Context', order: 20 },
  requiredCapabilities: ['evidence'], optionalCapabilities: ['evidence_content'], selectionDependencies: ['runId', 'attempt', 'eventSeq'],
  whenUnavailable: 'explain',
  render: Context,
};
