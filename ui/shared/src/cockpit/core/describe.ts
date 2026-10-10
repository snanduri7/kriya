import { payloadOf, type EvidenceRecord } from './records';

const str = (v: unknown): string | null => (typeof v === 'string' && v.length ? v : null);
const short = (digest: unknown): string => (typeof digest === 'string' ? digest.replace(/^sha256:/, '').slice(0, 10) : '-');

export interface RecordSummary {
  /** One human line. */
  title: string;
  /** Further recorded facts, each a short human sentence. */
  facts: string[];
  /** The recorded evidence class (diagnosis records), else null; provenance is a separate property of the record. */
  evidenceClass: string | null;
}

/** A human-readable summary of one record from its recorded fields; unknown kinds say their kind and nothing else (no invented meaning). */
export function describeRecord(r: EvidenceRecord): RecordSummary {
  const p = payloadOf(r);
  const facts: string[] = [];
  const add = (label: string, value: unknown) => { const v = typeof value === 'string' ? value : typeof value === 'number' || typeof value === 'boolean' ? String(value) : null; if (v !== null && v !== '') facts.push(`${label}: ${v}`); };
  let title = r.kind;
  let evidenceClass: string | null = null;
  switch (r.kind) {
    case 'run.opened': title = 'Run opened'; add('base revision', p.base_revision); add('workspace identity', short(p.workspace_identity)); break;
    case 'run.closed': title = `Run closed: ${str(p.terminal_status) ?? 'terminal status not recorded'}`; add('lifecycle', p.lifecycle_state); add('model calls', p.model_calls); break;
    case 'unit.opened': title = `Unit ${r.unit_id ?? '?'} opened`; add('plan', p.plan_id); add('subtask', p.current_subtask_id); add('write scope mode', p.write_scope_mode); break;
    case 'unit.closed': title = `Unit ${r.unit_id ?? '?'} closed: ${str(p.outcome) ?? 'outcome not recorded'}`; add('failure category', p.failure_category); break;
    case 'attempt.opened': title = `Attempt ${r.attempt_number ?? '?'} opened`; add('mode', p.mode); add('operation', p.operation); add('reserved fallback', p.reserved_fallback); break;
    case 'attempt.closed': title = `Attempt ${r.attempt_number ?? '?'} closed: ${str(p.outcome) ?? 'outcome not recorded'}`; add('candidate gates passed', p.candidate_gates_passed); break;
    case 'attempt.concluded': title = `Attempt ${r.attempt_number ?? '?'} ${p.succeeded === true ? 'succeeded' : p.succeeded === false ? 'failed' : 'has no recorded conclusion'}`; break;
    case 'model.result': title = `Model call${r.call_seq != null ? ` #${r.call_seq}` : ''} (${r.role ?? 'role not recorded'}) finished: ${str(p.finish_reason) ?? 'finish reason not recorded'}`; add('model', p.model); add('provider-reported prompt tokens', p.prompt_tokens_reported); add('completion tokens', p.completion_tokens); break;
    case 'model.request': title = `Model request${r.call_seq != null ? ` #${r.call_seq}` : ''} (${r.role ?? 'role not recorded'})`; add('model', p.model); add('dispatched', p.dispatched); add('refusal', p.refusal_type); break;
    case 'model.response': title = 'Model response'; add('finish reason', p.finish_reason); add('error', p.error_type); break;
    case 'prompt.sections': title = p.fitter === 'fit_variable_section' ? `Prompt section ${str(p.section) ?? '?'} fitted` : 'Prompt sections fitted'; add('request tokens', p.request_tokens); add('capacity tokens', p.capacity_tokens); add('omitted', p.omitted); break;
    case 'authority.snapshot': title = 'Authority snapshot: what this invocation could change'; add('write scope mode', p.write_scope_mode); add('known targets', Array.isArray(p.known_target_files) ? p.known_target_files.length : null); break;
    case 'obligations.snapshot': title = 'Obligations snapshot'; add('ledger revision', p.ledger_revision); break;
    case 'developer.parse': title = `Developer answer parsed as ${str(p.kind) ?? 'unknown kind'}`; add('path', p.path); add('reason', p.reason_code); break;
    case 'candidate.change': title = `${str(p.decision) ?? 'Change'}: ${str(p.path) ?? 'path not recorded'}`; add('reason', p.reason_code); add('lines added', p.lines_added); add('lines removed', p.lines_removed); add('baseline digest', p.before_digest ? short(p.before_digest) : null); break;
    case 'gate.result': title = `Gate ${str(p.gate) ?? '?'} ${p.success === true ? 'passed' : p.success === false ? 'failed' : '(result not recorded)'}`; add('stage', p.stage); add('exit code', p.exit_code); add('duration (s)', p.duration_seconds); break;
    case 'mirror.gate_outcome': title = `Gate ${str(p.type) ?? '?'} ${p.success === true ? 'passed' : p.success === false ? 'failed' : '(result not recorded)'} (mirrored outcome)`; break;
    case 'diagnosis': title = `Diagnosis: ${str(p.reason_code) ?? str(p.type) ?? 'reason not recorded'}`; evidenceClass = str(p.evidence_class); add('attribution', [p.attribution_kind, p.attribution_tier, p.attribution_confidence].filter((x) => typeof x === 'string').join(' / ')); break;
    case 'recovery.decision': title = `Recovery decision: ${str(p.action) ?? 'action not recorded'}`; add('retry', p.retry); add('progress', p.progress_classification); break;
    case 'fallback.decision': title = `Model fallback decision (${str(p.phase) ?? 'phase not recorded'})`; add('requested', p.requested); add('selected', p.selected); add('fell back', p.fallback); break;
    case 'retry.delta': title = `Retry delta, attempt ${String(p.previous_attempt ?? '?')} to ${String(p.attempt ?? r.attempt_number ?? '?')}`; add('information gain', p.information_gain); add('changed', Array.isArray(p.changed) ? p.changed.join(', ') || '(nothing)' : null); break;
    case 'regression.decision': title = `Regression decision: ${str(p.classification) ?? 'classification not recorded'}`; add('reason', p.reason_code); break;
    case 'mirror.event': title = `Run event: ${str(p.kind) ?? 'kind not recorded'}`; add('source', p.source); add('authority', p.authority); break;
    case 'mirror.evidence': title = `Mirrored evidence: ${str(p.kind) ?? 'kind not recorded'}`; add('sensitivity', p.sensitivity); break;
    case 'tool.execution': title = 'Tool execution'; break;
    case 'recorder.gap': title = `Recorder gap: ${str(p.lost_kind) ?? 'kind not recorded'}`; add('reason', p.reason ?? p.error); break;
    default: break;
  }
  return { title, facts, evidenceClass };
}

/** Which parts of a record are bytes the recorder kept, and which are only digests (the record is the authority on why content is absent). */
export function recordContent(r: EvidenceRecord): { kept: string[]; digestOnly: string[] } {
  const kept = Object.keys(r.blobs ?? {});
  return { kept, digestOnly: Object.keys(r.content_digests ?? {}).filter((k) => !kept.includes(k)) };
}
