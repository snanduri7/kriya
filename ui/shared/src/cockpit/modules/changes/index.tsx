import { useState } from 'react';
import {
  AvailabilityView, Badge, KeyValue, Panel, attemptFor, changeRows, contentAvailability, shortDigest, useAsync, useCapabilities, useDispatch, useModuleSelection, useProvider, useRunEvidence,
  type Availability, type ChangeRow, type EvidenceChangeContent, type ModuleDefinition, type RunEvidence,
} from '../../core';
import { sanitizeText } from '../../../render/sanitize';
import { DiffView } from './DiffView';

export type { ChangeKind, ChangeRow } from '../../core';

/** What the user is told about the baseline: only what the record supports. */
export function baselineText(row: ChangeRow): string {
  switch (row.kind) {
    case 'ADDED': return 'new file: no baseline exists (empty, not a historical file)';
    case 'MODIFIED': case 'DELETED': case 'UNCHANGED': return `recorded baseline ${shortDigest(row.beforeDigest)}`;
    default: return row.decision === 'STAGED' ? 'baseline unavailable (never substituted from the current worktree)' : 'baseline unavailable: no candidate was staged';
  }
}

/** Which part a change's content view reads, if any: a staged change shows its diff; a refused one, the content the model proposed. */
export function viewPart(row: ChangeRow): 'diff' | 'proposed' | null {
  if (row.kept.includes('diff')) return 'diff';
  if (row.kept.includes('proposed')) return 'proposed';
  if (row.digestOnly.includes('diff') || row.digestOnly.includes('proposed') || row.digestOnly.length) return row.decision === 'STAGED' ? 'diff' : 'proposed';
  return null;
}

type Content = { state: 'idle' } | { state: 'denied' } | { state: 'digest_only' } | { state: 'none'; why: string } | { state: 'askable'; part: 'diff' | 'proposed' };

/** Decided from what is already known (capability, the record), so the precise reason is shown without a request whenever Kriya's data says it. */
export function contentState(row: ChangeRow, contentAuthorized: boolean, capture: string | undefined): Content {
  const part = viewPart(row);
  if (part === null) return { state: 'none', why: row.kind === 'UNCHANGED' ? 'The candidate is identical to the baseline: there is no diff.' : 'The recorder kept no content for this change.' };
  if (capture === 'digest_only' || (row.kept.length === 0 && row.digestOnly.length > 0)) return { state: 'digest_only' };
  if (!contentAuthorized) return { state: 'denied' };
  return { state: 'askable', part };
}

function ContentPanel({ row, evidence }: { row: ChangeRow; evidence: RunEvidence }) {
  const provider = useProvider();
  const caps = useCapabilities();
  const [asked, setAsked] = useState<string | null>(null);
  const state = contentState(row, caps.has('evidence_content'), evidence.manifest.manifest?.capture);
  const key = state.state === 'askable' && asked === `${row.seq}:${state.part}` ? asked : null;
  const content = useAsync<EvidenceChangeContent>(key, async (signal) => contentAvailability(await provider.evidenceChangeContent(evidence.evidenceRunId, { seq: row.seq, part: (state as { part: string }).part }, signal)), { state: 'loading' });
  switch (state.state) {
    case 'denied': return <AvailabilityView value={{ state: 'unavailable', reason: 'denied', detail: "Kriya's content visibility policy does not authorize showing stored content. Metadata above is complete." }}>{() => null}</AvailabilityView>;
    case 'digest_only': return <AvailabilityView value={{ state: 'unavailable', reason: 'digest_only', detail: 'This run kept digests of file content, not the content itself. Metadata above is complete.' }}>{() => null}</AvailabilityView>;
    case 'none': return <p className="muted">{state.why}</p>;
    case 'idle': return null;
    case 'askable': break;
  }
  if (key === null) return <button type="button" onClick={() => setAsked(`${row.seq}:${state.part}`)}>{state.part === 'diff' ? 'Show diff' : 'Show proposed content'}</button>;
  return (
    <AvailabilityView value={content as Availability<EvidenceChangeContent>}>
      {(data) => (
        <div>
          {data.part === 'diff' ? <DiffView diff={data.content} path={data.path} /> : <pre className="mono" aria-label="Proposed content" style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 420, overflow: 'auto' }}>{sanitizeText(data.content)}</pre>}
          <p className="muted small" aria-label="Content provenance">
            {data.part} · {data.bytes_total} bytes · {data.encoding} · digest verified {shortDigest(data.ref)} · {data.path ?? 'path not recorded'} · attempt {data.attempt ?? '-'}
          </p>
        </div>
      )}
    </AvailabilityView>
  );
}

function Changes() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const attempt = useModuleSelection('attempt');
  const file = useModuleSelection('filePath');
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const all = changeRows(data.records);
        const which = attemptFor(data.records, attempt, ['candidate.change']);
        const rows = all.filter((r) => r.attempt === which);
        const selected = rows.find((r) => r.path === file) ?? null;
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <Panel title={which === null ? 'Changes' : `Changes · attempt ${which}`}>
              {rows.length === 0 ? <div className="ck-state"><strong>No candidate change recorded{which === null ? '' : ' for this attempt'}</strong>The store has no candidate.change record here.</div> : (
                <ul className="ck-list" aria-label="Changed files">
                  {rows.map((c) => (
                    <li key={c.seq}>
                      <button type="button" aria-selected={c.path === file} onClick={() => dispatch({ type: 'selectFile', filePath: c.path })}>
                        <Badge tone={c.kind === 'ADDED' ? 'ok' : c.kind === 'DELETED' ? 'bad' : c.kind === 'UNAVAILABLE' ? 'warn' : 'info'}>{c.kind}</Badge> <span className="mono">{c.path}</span>
                        <div className="muted small">{baselineText(c)}{c.linesAdded !== null ? ` · +${c.linesAdded}/-${c.linesRemoved ?? 0}` : ''}{c.reasonCode ? ` · refused: ${c.reasonCode}` : ''}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            {selected ? (
              <Panel title={`${selected.path}`}>
                <KeyValue items={[['Change', selected.kind], ['Baseline', baselineText(selected)], ['Baseline digest', selected.beforeDigest ?? 'none recorded'], ['Candidate digest', selected.afterDigest ?? 'none recorded'], ['Record', `seq ${selected.seq} · attempt ${selected.attempt ?? '-'}`]]} />
                <ContentPanel row={selected} evidence={data} />
              </Panel>
            ) : rows.length ? <p className="muted">Select a file for its recorded detail.</p> : null}
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const changesModule: ModuleDefinition = {
  id: 'changes',
  placement: { page: 'runs', surface: 'center-tab', label: 'Changes', order: 40 },
  requiredCapabilities: ['evidence'], optionalCapabilities: ['evidence_content'], selectionDependencies: ['runId', 'attempt', 'filePath'],
  whenUnavailable: 'explain',
  render: Changes,
};
