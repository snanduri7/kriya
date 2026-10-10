/** Seeds an isolated Kriya state directory through the REAL recorder and trace logger (used by the real-Kriya tests; never a model). */
export const SEED = `
import json, os
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.trace import TraceLogger
state = os.environ['KRIYA_STATE_DIR']
ident = {'unit_id': 'direct', 'unit_kind': 'direct', 'invocation_seq': 1, 'phase': 'attempt', 'attempt_number': 1}
def store(rid, capture='full', seal=True, tamper=False):
    w = AttemptEvidenceWriter(state, rid, capture=capture, manifest={'workspace_identity': 'ws', 'base_revision': 'b2b5fc411394e307', 'base_tree_hash': None})
    w.append('run.opened', {'base_revision': 'b2b5fc411394e307'})
    w.append('attempt.opened', {'mode': 'direct'}, identity=ident)
    w.append('model.result', {'finish_reason': 'stop'}, identity={**ident, 'role': 'developer', 'call_seq': 1})
    w.append('mirror.gate_outcome', {'attempt': 1, 'index': 0, 'success': True, 'type': 'compile'}, identity=ident, content={'outcome': 'mirrored'})
    w.append('candidate.change', {'decision': 'STAGED', 'path': 'src/A.java', 'before_digest': 'sha256:' + 'b' * 64, 'after_digest': 'sha256:' + 'a' * 64, 'created': False, 'deleted': False, 'unchanged': False, 'lines_added': 1, 'lines_removed': 0, 'text_diff': True}, identity=ident, content={'before': 'class A {}\\n', 'after': 'class A {}\\n// added\\n', 'diff': '--- a/src/A.java\\n+++ b/src/A.java\\n@@ -1 +1,2 @@\\n class A {}\\n+// added\\n'})
    w.append('diagnosis', {'attempt': 1, 'type': 'compile', 'reason_code': 'COMPILE_FAILED', 'evidence_class': 'DERIVED_DETERMINISTIC', 'attribution_kind': 'SOURCE_DEFECT', 'attribution_tier': 'locator', 'attribution_confidence': 'high', 'likely_files': ['src/A.java']}, identity=ident, content={'message': 'boom'})
    w.append('attempt.concluded', {'succeeded': True}, identity=ident)
    w.append('run.closed', {'terminal_status': 'SUCCESS'})
    if seal: w.seal('run.closed')
    if tamper:
        p = os.path.join(state, 'attempt-evidence', rid, 'records.jsonl')
        lines = open(p, 'rb').read().split(b'\\n')
        rec = json.loads(lines[2]); rec['payload'] = {'tampered': True}
        lines[2] = json.dumps(rec, sort_keys=True, separators=(',', ':')).encode()
        open(p, 'wb').write(b'\\n'.join(lines))
def event(rid, status, **extra):
    d = {'status': status, 'capture': 'full', **extra}
    if rid: d['run_id'] = rid; d['path'] = os.path.join(state, 'attempt-evidence', rid)
    return [{'kind': 'evidence.attempt_store', 'attempt': 0, 'source': 'workflow', 'authority': 'auxiliary', 'message': 'attempt evidence store', 'failure_type': None, 'operation': None, 'details': d, 'created_at': 1.0}]
store('ev-ok'); store('ev-open', seal=False); store('ev-digest', capture='digest_only'); store('ev-broken', tamper=True)
log = TraceLogger(os.path.join(state, 'traces.db'))
rows = [('t-ok', 'success', event('ev-ok', 'OPEN')), ('t-open', 'in_progress', event('ev-open', 'OPEN')), ('t-digest', 'success', event('ev-digest', 'OPEN')),
        ('t-broken', 'success', event('ev-broken', 'OPEN')), ('t-pruned', 'success', event('ev-gone', 'CLOSED')), ('t-old', 'success', []),
        ('t-off', 'success', event(None, 'DISABLED', reason='capture off'))]
for rid, status, events in rows:
    log.log_run(run_id=rid, goal='real-store goal ' + rid, duration_sec=1.234, attempts=1, status=status, files_modified=[], run_events=events)
`;
