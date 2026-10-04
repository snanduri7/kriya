import { useMemo } from 'react';
import type { Comparison, RunDetail } from '../model/kup';
import type { HostAdapter, OpenInIdeResult } from '../host/HostAdapter';
import { DRAWER_TABS, type DrawerTab } from '../state/selection';
import { isRecorded } from '../model/availability';
import { diffLines } from '../render/diff';
import { EVIDENCE_REFS_LABEL, attributionEvidenceRefs } from '../model/normalize';
import { sanitizeText } from '../render/sanitize';
import { Recorded } from './Availability';
import { Payload } from './Payload';
import { TabPanel, Tabs } from './Tabs';
import { VirtualList } from './VirtualList';

export interface DrawerProps {
  detail: RunDetail | null;
  open: boolean;
  tab: DrawerTab;
  comparisonPath: string | null;
  onTab: (t: DrawerTab) => void;
  onToggle: () => void;
  onSelectComparison: (path: string | null) => void;
  host: HostAdapter;
  onOpenResult: (r: OpenInIdeResult) => void;
  height: number;
}

export function Drawer({ detail, open, tab, comparisonPath, onTab, onToggle, onSelectComparison, host, onOpenResult, height }: DrawerProps) {
  const comparisons: Comparison[] = detail && isRecorded(detail.comparisons) ? detail.comparisons.data : [];
  const selected = comparisons.find((c) => c.path === comparisonPath) ?? null;
  const diff = useMemo(() => (selected ? diffLines(selected.before.text, selected.after.text) : null), [selected]);
  const changed = diff ? diff.lines.filter((l) => l.op !== 'equal').length : 0;
  return (
    <section className={`drawer${open ? ' open' : ''}`} aria-label="Drawer: diff and why">
      <div className="drawer-bar">
        <button type="button" className="small" aria-expanded={open} onClick={onToggle}>{open ? 'Hide' : 'Show'} drawer</button>
        <Tabs tabs={DRAWER_TABS.map((k) => ({ key: k, label: k === 'diff' ? 'Diff' : 'Why' }))} active={tab} onChange={onTab} label="Drawer sections" idPrefix="drawer" />
      </div>
      {open && detail ? (
        <>
          <TabPanel idPrefix="drawer" tabKey="diff" active={tab === 'diff'}>
            <Recorded section={detail.comparisons} title="Recorded comparison">
              {() => (
                <div className="diff-wrap">
                  <div className="diff-files" role="group" aria-label="Compared files">
                    {comparisons.map((c) => (
                      <button key={c.path} type="button" className={`chip${c.path === comparisonPath ? ' active' : ''}`} aria-pressed={c.path === comparisonPath} onClick={() => onSelectComparison(c.path)}>{sanitizeText(c.path)}</button>
                    ))}
                  </div>
                  {selected && diff ? (
                    <div>
                      <div className="muted">
                        before: {sanitizeText(selected.before.provenance)}{selected.before.revision ? ` @ ${sanitizeText(selected.before.revision)}` : ''} · after: {sanitizeText(selected.after.provenance)}{selected.after.revision ? ` @ ${sanitizeText(selected.after.revision)}` : ''} · {diff.lines.length} lines, {changed} changed{diff.exact ? '' : ' (block view: too large for an exact diff)'} · read-only
                        <button type="button" className="small" onClick={() => { void host.openInIde({ path: selected.path, line: firstChangedLine(diff.lines) }).then(onOpenResult); }}>Open in IDE</button>
                      </div>
                      <VirtualList
                        items={diff.lines}
                        rowHeight={20}
                        height={height}
                        selectedIndex={null}
                        onSelect={() => undefined}
                        getKey={(_l, i) => String(i)}
                        ariaLabel={`Diff of ${selected.path}`}
                        renderRow={(l) => (
                          <div className={`dline d-${l.op}`}>
                            <span className="mono ln">{l.before ?? ''}</span><span className="mono ln">{l.after ?? ''}</span>
                            <span className="mono sign">{l.op === 'add' ? '+' : l.op === 'del' ? '-' : ' '}</span>
                            <span className="mono dtext">{sanitizeText(l.text)}</span>
                          </div>
                        )}
                      />
                    </div>
                  ) : <div className="muted">select a compared file</div>}
                </div>
              )}
            </Recorded>
            <p className="muted">Only recorded before/after text with provenance is compared; today's file is never shown as history (P-23).</p>
          </TabPanel>
          <TabPanel idPrefix="drawer" tabKey="why" active={tab === 'why'}>
            <div className="why">
              <div><strong>Recorded failure category</strong>: {sanitizeText(detail.run.failure_category ?? 'none recorded')}</div>
              <Recorded section={detail.failure_report} title="Failure report (per failed attempt)">
                {(rep) => (
                  <ol>{rep.map((f, i) => { const o = (f ?? {}) as Record<string, unknown>; return <li key={i}>{sanitizeText(String(o.failure_type ?? '?'))} · {sanitizeText(String(o.category ?? '?'))} · tier {sanitizeText(String(o.attribution_tier ?? 'not recorded'))}</li>; })}</ol>
                )}
              </Recorded>
              <Recorded section={detail.attribution} title="Recorded causal attribution">
                {(a) => (
                  <dl className="facts">
                    <dt>First incorrect state</dt><dd>{sanitizeText(a.first_incorrect_state ?? 'not recorded')}</dd>
                    <dt>Cause</dt><dd>{sanitizeText(a.cause ?? 'not recorded')}</dd>
                    <dt>Classification (display vocabulary, as recorded)</dt><dd>{sanitizeText(a.category ?? 'not recorded')}</dd>
                    <dt>Evidence references</dt>
                    <dd className="mono">{(() => { const refs = attributionEvidenceRefs(a); return refs.state !== 'listed' ? EVIDENCE_REFS_LABEL[refs.state] : (
                      <div>
                        <ul className="plain" aria-label="Evidence references">
                          {refs.ids.map((id, i) => <li key={i}>{sanitizeText(id)} — unresolved reference</li>)}
                        </ul>
                        <div className="muted small">{refs.ids.length} reference{refs.ids.length === 1 ? '' : 's'}: no identifier namespace is defined for evidence_ids, and recorded evidence records carry no identifier (EvidenceRecord.to_dict), so none resolves to a record; nothing here is verified attribution.{refs.repeated.length ? ` Repeated within the list: ${sanitizeText(refs.repeated.join(', '))}.` : ''}{refs.nonStrings ? ` ${refs.nonStrings} entr${refs.nonStrings === 1 ? 'y is' : 'ies are'} not a string (see the record below).` : ''}</div>
                      </div>
                    ); })()}</dd>
                  </dl>
                )}
              </Recorded>
              <Recorded section={detail.attribution} title="Recorded attribution (raw)">
                {(a) => <Payload value={a} label="attribution record" />}
              </Recorded>
              <Recorded section={detail.diagnostics} title="Diagnostics">{(d) => <Payload value={d} label="diagnostics" />}</Recorded>
              <p className="muted">Failure categories are not causal attribution; the chain is shown only where it was recorded (P-23, D-5).</p>
            </div>
          </TabPanel>
        </>
      ) : null}
    </section>
  );
}

function firstChangedLine(lines: { op: string; after: number | null; before: number | null }[]): number | undefined {
  const l = lines.find((x) => x.op !== 'equal');
  return l ? (l.after ?? l.before ?? undefined) : undefined;
}
