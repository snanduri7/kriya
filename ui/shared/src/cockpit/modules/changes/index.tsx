import { AvailabilityView, Badge, KeyValue, Panel, effectiveAttempt, hasData, ofKind, payloadOf, shortDigest, useCapabilities, useDispatch, useModuleSelection, useRunEvidence, type EvidenceRecord, type ModuleDefinition } from '../../core';

export type ChangeKind = 'ADDED' | 'MODIFIED' | 'DELETED' | 'UNCHANGED' | 'UNAVAILABLE';

export interface FileChange { path: string; kind: ChangeKind; baseline: string; after: string; added: number | null; removed: number | null; note: string | null; seq: number }

/** Generic change semantics straight from the recorded candidate.change payload: no assumption that a baseline or the file pre-existed. */
export function changeOf(r: EvidenceRecord): FileChange {
  const p = payloadOf(r);
  const path = typeof p.path === 'string' ? p.path : '(path not recorded)';
  const base = { path, seq: r.seq, added: typeof p.lines_added === 'number' ? p.lines_added : null, removed: typeof p.lines_removed === 'number' ? p.lines_removed : null };
  if (p.decision !== 'STAGED') return { ...base, kind: 'UNAVAILABLE', baseline: 'baseline unavailable', after: 'no candidate was staged', note: `refused: ${String(p.reason_code ?? 'reason not recorded')}`, added: null, removed: null };
  if (p.unchanged === true) return { ...base, kind: 'UNCHANGED', baseline: `recorded baseline ${shortDigest(p.before_digest)}`, after: 'identical to the baseline', note: null };
  if (p.deleted === true) return { ...base, kind: 'DELETED', baseline: `recorded baseline ${shortDigest(p.before_digest)}`, after: 'deleted', note: null };
  if (p.created === true) return { ...base, kind: 'ADDED', baseline: 'new file: no baseline exists (empty, not a historical file)', after: shortDigest(p.after_digest), note: null };
  if (typeof p.before_digest === 'string') return { ...base, kind: 'MODIFIED', baseline: `recorded baseline ${shortDigest(p.before_digest)}`, after: shortDigest(p.after_digest), note: null };
  return { ...base, kind: 'UNAVAILABLE', baseline: 'baseline unavailable (never substituted from the current worktree)', after: shortDigest(p.after_digest), note: null };
}

function Changes() {
  const evidence = useRunEvidence();
  const caps = useCapabilities();
  const dispatch = useDispatch();
  const attempt = useModuleSelection('attempt');
  const file = useModuleSelection('filePath');
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const all = ofKind(data.records, 'candidate.change');
        const which = effectiveAttempt(data.records, attempt, ['candidate.change']);
        const changes = all.filter((r) => (r.attempt_number ?? null) === which).map(changeOf);
        const capture = data.manifest.manifest?.capture;
        const content = capture === 'digest_only' ? 'Digest only: this run kept digests, not file content.'
          : !caps.has('evidence_content') ? 'Content not authorized: Kriya\'s content visibility policy is off, so no before/after text is requested.'
          : 'Content is authorized; the before/after viewer is not part of this build yet.';
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <Panel title={which === null ? 'Changes' : `Changes · attempt ${which}`}>
              {changes.length === 0 ? <div className="ck-state"><strong>No candidate change recorded{which === null ? '' : ' for this attempt'}</strong>{hasData(evidence) ? 'The store has no candidate.change record here.' : ''}</div> : (
                <ul className="ck-list" aria-label="Changed files">
                  {changes.map((c) => (
                    <li key={`${c.seq}`}>
                      <button type="button" aria-selected={c.path === file} onClick={() => dispatch({ type: 'selectFile', filePath: c.path })}>
                        <Badge tone={c.kind === 'ADDED' ? 'ok' : c.kind === 'DELETED' ? 'bad' : c.kind === 'UNAVAILABLE' ? 'warn' : 'info'}>{c.kind}</Badge> <span className="mono">{c.path}</span>
                        <div className="muted small">{c.baseline}{c.added !== null ? ` · +${c.added}/-${c.removed ?? 0}` : ''}{c.note ? ` · ${c.note}` : ''}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Content"><KeyValue items={[['Availability', content]]} /></Panel>
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
