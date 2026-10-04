/** The hand-written host validators (ipc_contract.ts, kept dependency-free for the main process) must agree with the
 * GENERATED schema validators (@kriya-ui/kup, host-contract.schema.json) on every corpus item - one contract, two
 * implementations, proven equal rather than assumed. */
import { describe, expect, it } from 'vitest';
import { validate } from '@kriya-ui/kup';
import { validateKupRequest, validateOpenInIde } from '../src/main/ipc_contract';

const SID = '20261004T093000000000Z-f1c70001';
const REQUESTS: unknown[] = [
  { operation: 'snapshot.acquire' }, { operation: 'snapshot.acquire', workspace: '/w' }, { operation: 'snapshot.acquire', workspace: 'w' }, { operation: 'snapshot.acquire', x: 1 },
  { operation: 'snapshot.list' }, { operation: 'snapshot.list', verify: true }, { operation: 'snapshot.list', verify: 1 }, { operation: 'snapshot.prune' }, { operation: 'snapshot.prune', keep: 0 }, { operation: 'snapshot.prune', keep: -1 }, { operation: 'snapshot.prune', keep: 1001 }, { operation: 'snapshot.prune', keep: 1.5 },
  { operation: 'history.list', snapshot_id: SID }, { operation: 'history.list', snapshot_id: 'latest' }, { operation: 'history.list', snapshot_id: SID.toUpperCase() }, { operation: 'history.detail', snapshot_id: SID, run_id: 'r1' }, { operation: 'history.prompt', snapshot_id: SID, run_id: 'r1' }, { operation: 'history.detail', snapshot_id: SID }, { operation: 'history.prompt', run_id: 'r1' },
  { operation: 'capabilities' }, { operation: 'capabilities', x: 1 }, 'capabilities', null, {},
  { operation: 'history.list' }, { operation: 'history.list', limit: 1 }, { operation: 'history.list', snapshot_id: SID, limit: 200 }, { operation: 'history.list', snapshot_id: SID, limit: 201 }, { operation: 'history.list', limit: 0 }, { operation: 'history.list', limit: 1.5 }, { operation: 'history.list', limit: '5' },
  { operation: 'history.list', snapshot_id: SID, cursor: 'abc' }, { operation: 'history.list', snapshot_id: SID, cursor: '-abc' }, { operation: 'history.list', cursor: '' }, { operation: 'history.list', cursor: 'a b' }, { operation: 'history.list', cursor: 'x'.repeat(1024) }, { operation: 'history.list', cursor: 'x'.repeat(1025) }, { operation: 'history.list', cursor: 'WyIyMDI2IiwicnVuIl0=' },
  { operation: 'history.detail', run_id: 'r1' }, { operation: 'history.detail', run_id: '--all' }, { operation: 'history.detail', run_id: 'a/b' }, { operation: 'history.detail', run_id: 'r1', cursor: 'c' }, { operation: 'history.detail' }, { operation: 'history.detail', run_id: 'r'.repeat(256) }, { operation: 'history.detail', run_id: 'r'.repeat(257) }, { operation: 'history.detail', run_id: 'ruén' },
  { operation: 'history.prompt', run_id: 'run-0001.a:b_c' }, { operation: 'history.prompt', run_id: '' },
  { operation: 'workspace.status', workspace: '/w' }, { operation: 'workspace.status', workspace: 'w' }, { operation: 'workspace.status', workspace: '/a\nb' }, { operation: 'workspace.status', workspace: '' }, { operation: 'workspace.status' },
  { operation: 'history.delete', run_id: 'r' }, { operation: 42 },
];
const OPENS: unknown[] = [{ path: '/w/a.py' }, { path: '/w/a.py', line: 7 }, { path: '/w/a.py', line: 0 }, { path: '/w/a.py', line: '7' }, { path: '' }, { path: '/w/a.py', cmd: 'x' }, { line: 3 }, null];

describe('hand-written host validators agree with the generated schema validators', () => {
  it('KupRequest', () => {
    for (const r of REQUESTS) expect(validateKupRequest(r).ok, JSON.stringify(r)).toBe(validate.kupRequest(r).ok);
  });
  it('OpenInIdeRequest', () => {
    for (const r of OPENS) expect(validateOpenInIde(r).ok, JSON.stringify(r)).toBe(validate.openInIdeRequest(r).ok);
  });
});
