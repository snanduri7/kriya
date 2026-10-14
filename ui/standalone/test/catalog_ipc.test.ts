/** Demo V2: the renderer-reachable grammar of the read-only catalog operations (skills.*, tools.*). An id is identity, never a path or an option. */
import { describe, expect, it } from 'vitest';
import { LIMITS, validateKupRequest } from '../src/main/ipc_contract';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { SERVICE_ARGUMENTS, toServiceRequest } from '../src/main/kriya_service';

const W = '/Users/someone/project';

describe('catalog operations: request grammar', () => {
  it('skills.list and tools.list take exactly workspace', () => {
    for (const operation of ['skills.list', 'tools.list'] as const) {
      expect(validateKupRequest({ operation, workspace: W })).toEqual({ ok: true, value: { operation, workspace: W } });
      expect(validateKupRequest({ operation, workspace: W, extra: 1 }).ok).toBe(false);
      expect(validateKupRequest({ operation }).ok).toBe(false);
      expect(validateKupRequest({ operation, workspace: 'relative/path' }).ok).toBe(false);
    }
  });
  it('the describe operations take exactly workspace and one id', () => {
    expect(validateKupRequest({ operation: 'skills.describe', workspace: W, skill_id: 'org.apache:artemis-server' })).toEqual({ ok: true, value: { operation: 'skills.describe', workspace: W, skill_id: 'org.apache:artemis-server' } });
    for (const operation of ['tools.describe', 'tools.approval_state'] as const) {
      expect(validateKupRequest({ operation, workspace: W, tool_id: 'mcp:filesystem' })).toEqual({ ok: true, value: { operation, workspace: W, tool_id: 'mcp:filesystem' } });
    }
    // each takes ITS id key and nothing else: a skill_id on a tool operation (or the reverse) is a refusal, not a silently dropped field
    expect(validateKupRequest({ operation: 'tools.describe', workspace: W, skill_id: 'x' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'skills.describe', workspace: W, tool_id: 'x' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'skills.describe', workspace: W }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'tools.describe', workspace: W, tool_id: 'x', path: '/etc' }).ok).toBe(false);
  });
  it('an id is bounded and free of control characters, and is never a path argument the host opens', () => {
    const bad = ['', 'a\u0000b', 'a\nb', 'a\u007fb', 'x'.repeat(LIMITS.catalogIdMax + 1), 42, null, undefined, ['a'], { a: 1 }];
    for (const id of bad) {
      expect(validateKupRequest({ operation: 'skills.describe', workspace: W, skill_id: id }).ok, String(id)).toBe(false);
      expect(validateKupRequest({ operation: 'tools.approval_state', workspace: W, tool_id: id }).ok, String(id)).toBe(false);
    }
    expect(validateKupRequest({ operation: 'tools.describe', workspace: W, tool_id: 'x'.repeat(LIMITS.catalogIdMax) }).ok).toBe(true);
  });
  it('the validated request is a fresh plain object with only its known keys', () => {
    const input = { operation: 'tools.describe', workspace: W, tool_id: 'mcp:a', constructor: 'x' } as Record<string, unknown>;
    expect(validateKupRequest(input).ok).toBe(false); // an unknown key is refused, never carried through
    const ok = validateKupRequest({ operation: 'tools.describe', workspace: W, tool_id: 'mcp:a' });
    expect(ok.ok && Object.keys(ok.value).sort()).toEqual(['operation', 'tool_id', 'workspace']);
  });
});

describe('catalog operations: transport', () => {
  it('are served only by the persistent Kriya service (the one-shot grammar has no flag for them)', () => {
    for (const request of [
      { operation: 'skills.list', workspace: W }, { operation: 'skills.describe', workspace: W, skill_id: 'a' }, { operation: 'tools.list', workspace: W },
      { operation: 'tools.describe', workspace: W, tool_id: 'mcp:a' }, { operation: 'tools.approval_state', workspace: W, tool_id: 'mcp:a' },
    ] as const) expect(() => buildKriyaArgv(request), request.operation).toThrow(/persistent Kriya service/);
  });
  it('the service table lists exactly the arguments each takes, and the wire request carries them as typed', () => {
    expect(SERVICE_ARGUMENTS['skills.list']).toEqual(['workspace']);
    expect(SERVICE_ARGUMENTS['skills.describe']).toEqual(['workspace', 'skill_id']);
    expect(SERVICE_ARGUMENTS['tools.list']).toEqual(['workspace']);
    expect(SERVICE_ARGUMENTS['tools.describe']).toEqual(['workspace', 'tool_id']);
    expect(SERVICE_ARGUMENTS['tools.approval_state']).toEqual(['workspace', 'tool_id']);
    expect(toServiceRequest('q', { operation: 'tools.describe', workspace: W, tool_id: 'plugin:p' })).toEqual(
      { id: 'q', schema_version: 1, capability_set: '1.1', operation: 'tools.describe', arguments: { workspace: W, tool_id: 'plugin:p' } });
    expect(() => toServiceRequest('q', { operation: 'tools.list', workspace: W, tool_id: 'x' } as never)).toThrow(/does not take: tool_id/);
  });
});
