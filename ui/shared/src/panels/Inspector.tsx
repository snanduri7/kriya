import type { KupEnvelope, Prompt, RunDetail, RunEvent } from '../model/kup';
import type { HostAdapter, OpenInIdeResult } from '../host/HostAdapter';
import type { SlotState } from '../state/requests';
import { INSPECTOR_TABS, type InspectorTab } from '../state/selection';
import { isRecorded } from '../model/availability';
import { unknownEventKeys } from '../model/normalize';
import { sanitizeText } from '../render/sanitize';
import { AvailabilityBadge, Recorded } from './Availability';
import { Payload } from './Payload';
import { TabPanel, Tabs } from './Tabs';

export interface InspectorProps {
  detail: RunDetail | null;
  selectedEvent: RunEvent | null;
  tab: InspectorTab;
  onTab: (t: InspectorTab) => void;
  prompt: SlotState<KupEnvelope<Prompt>>;
  onLoadPrompt: () => void;
  host: HostAdapter;
  onOpenResult: (r: OpenInIdeResult) => void;
}

const TAB_LABELS: Record<InspectorTab, string> = { context: 'Context', prompt: 'Prompt', output: 'Output', gates: 'Gates', evidence: 'Evidence' };

export function Inspector({ detail, selectedEvent, tab, onTab, prompt, onLoadPrompt, host, onOpenResult }: InspectorProps) {
  const copy = (text: string) => { void host.copyToClipboard(text); };
  const open = (path: string, line?: number) => { void host.openInIde({ path, line }).then(onOpenResult); };
  const tabs = INSPECTOR_TABS.map((k) => ({ key: k, label: TAB_LABELS[k], badge: detail ? badgeFor(detail, k) : null }));
  return (
    <aside className="inspector" aria-label="Inspector">
      <Tabs tabs={tabs} active={tab} onChange={onTab} label="Inspector sections" idPrefix="insp" />
      {!detail ? <div className="placeholder">select a run</div> : (
        <>
          <TabPanel idPrefix="insp" tabKey="context" active={tab === 'context'}>
            <Recorded section={detail.context} title="Recorded context">
              {(ctx) => (
                <div>
                  <table className="table" aria-label="Context items">
                    <thead><tr><th>path</th><th>tier</th><th>members</th><th>omission</th><th></th></tr></thead>
                    <tbody>
                      {ctx.items.map((it, i) => (
                        <tr key={`${it.path}-${i}`} className={it.omitted ? 'omitted' : ''}>
                          <td className="mono">{sanitizeText(it.path)}</td>
                          <td>{sanitizeText(it.tier ?? 'not recorded')}</td>
                          <td className="mono">{it.member_ids?.length ? sanitizeText(it.member_ids.join(', ')) : '-'}</td>
                          <td>{it.omitted ? sanitizeText(it.omission_reason ?? 'omitted, reason not recorded') : 'included'}</td>
                          <td><button type="button" className="small" onClick={() => open(it.path)}>Open in IDE</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h3>Token accounting</h3>
                  {ctx.tokens ? (
                    <div className="tokens">
                      <div><strong>Estimated (Kriya)</strong>: {ctx.tokens.estimated ? sanitizeText(JSON.stringify(ctx.tokens.estimated)) : 'not recorded'}</div>
                      <div><strong>Provider-reported</strong>: {ctx.tokens.provider_reported ? sanitizeText(JSON.stringify(ctx.tokens.provider_reported)) : 'not recorded'}</div>
                      <p className="muted">Estimates and provider counts are never mixed (P-23).</p>
                    </div>
                  ) : <div className="availability">token accounting: not recorded</div>}
                  {ctx.package_hash ? <div className="muted mono">package hash: {sanitizeText(ctx.package_hash)}</div> : null}
                </div>
              )}
            </Recorded>
            <h3>Context events (recorded)</h3>
            {detail && isRecorded(detail.run_events) ? (
              <ul className="plain">
                {detail.run_events.data.filter((e) => typeof e.event === 'string' && (e.event.startsWith('context.') || e.event === 'developer.prompt_composition')).slice(0, 200).map((e, i) => (
                  <li key={i} className="mono small">{sanitizeText(e.at ?? '-')} {sanitizeText(e.event ?? '')} (attempt {e.attempt ?? '?'})</li>
                ))}
              </ul>
            ) : <div className="availability">run events: <AvailabilityBadge section={detail.run_events} /></div>}
          </TabPanel>
          <TabPanel idPrefix="insp" tabKey="prompt" active={tab === 'prompt'}>
            <p className="muted">The prompt is sensitive data and is fetched only on request (P-23, P-25). This store persists one planning prompt per run, not each Developer request (P-30).</p>
            {prompt.value === null && !prompt.pending ? <button type="button" onClick={onLoadPrompt}>Load prompt (sensitive)</button> : null}
            {prompt.pending ? <div className="muted">loading…</div> : null}
            {prompt.error ? <div className="warn" role="alert">{prompt.error.code}: {sanitizeText(prompt.error.message)}</div> : null}
            {prompt.value?.data ? (
              <div>
                <div className="muted">role: {sanitizeText(prompt.value.data.role ?? 'unknown')} · scope: {sanitizeText(prompt.value.data.scope ?? 'unknown')} · observed {sanitizeText(prompt.value.observed_at)}{prompt.current ? '' : ' (stale)'}</div>
                {prompt.value.data.prompt_rendered === null ? <div className="availability">prompt: not recorded</div> : <Payload value={prompt.value.data.prompt_rendered} label="prompt_rendered" onCopy={copy} />}
              </div>
            ) : null}
          </TabPanel>
          <TabPanel idPrefix="insp" tabKey="output" active={tab === 'output'}>
            <Recorded section={detail.output} title="Model output">{(out) => <Payload value={out} label="output" onCopy={copy} />}</Recorded>
            <p className="muted">Missing model responses are never recreated (P-23).</p>
          </TabPanel>
          <TabPanel idPrefix="insp" tabKey="gates" active={tab === 'gates'}>
            <Recorded section={detail.gate_outcomes} title="Gate outcomes">
              {(gates) => (
                <ol className="gates">
                  {gates.map((g, i) => { const o = (g ?? {}) as Record<string, unknown>; return (
                    <li key={i} className={o.passed === true ? 'st-success' : o.passed === false ? 'st-failed' : 'st-unknown'}>
                      <span className="mono">attempt {String(o.attempt ?? '?')}</span> · {sanitizeText(String(o.gate ?? o.name ?? 'gate'))} · {o.passed === true ? 'passed' : o.passed === false ? 'failed' : 'result not recorded'}
                      {typeof o.reason_code === 'string' ? <span className="badge">{sanitizeText(o.reason_code)}</span> : null}
                    </li>
                  ); })}
                </ol>
              )}
            </Recorded>
            <h3>Model hops</h3>
            <Recorded section={detail.model_hops} title="Model hops">{(hops) => <Payload value={hops} label="model_hops" onCopy={copy} />}</Recorded>
          </TabPanel>
          <TabPanel idPrefix="insp" tabKey="evidence" active={tab === 'evidence'}>
            <h3>Selected event</h3>
            {selectedEvent ? (
              <div>
                {unknownEventKeys(selectedEvent).length ? <div className="warn">unknown fields preserved: {unknownEventKeys(selectedEvent).map(sanitizeText).join(', ')}</div> : null}
                <Payload value={selectedEvent} label="event" onCopy={copy} />
              </div>
            ) : <div className="muted">select an event in the timeline</div>}
            <h3>Evidence records</h3>
            <Recorded section={detail.evidence_records} title="Evidence records">{(ev) => <Payload value={ev} label="evidence_records" onCopy={copy} />}</Recorded>
            <h3>Generation metrics</h3>
            <Recorded section={detail.generation_metrics} title="Generation metrics">{(m) => <Payload value={m} label="generation_metrics" onCopy={copy} />}</Recorded>
          </TabPanel>
        </>
      )}
    </aside>
  );
}

function badgeFor(detail: RunDetail, k: InspectorTab): string | null {
  const s = k === 'context' ? detail.context : k === 'output' ? detail.output : k === 'gates' ? detail.gate_outcomes : k === 'evidence' ? detail.evidence_records : null;
  if (!s) return null;
  return s.availability === 'recorded' ? null : String(s.availability).replace('_', ' ');
}
