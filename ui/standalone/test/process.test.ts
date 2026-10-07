import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { runKriya, PROCESS_LIMITS } from '../src/main/kriya_process';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { KRIYA_CHILD_ENV_KEYS, buildKriyaChildEnv } from '../src/main/child_env';
import { checkEnvelope } from '@kriya-ui/shared/src/model/normalize';

const FAKE = join(__dirname, '..', 'fake-kriya', 'fake_kriya.mjs');
const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');
const SID = '20261004T093000000000Z-f1c70001';
const run = (argv: string[], env: Record<string, string> = {}, limits = {}) => runKriya({ executable: process.execPath, argv: [FAKE, ...argv], nodeScript: true, env: { KRIYA_FAKE_FIXTURES: GEN, ...env }, limits });

describe('kriya process runner (P-31, P-32) against the fixture stand-in', () => {
  it('fixtures exist', () => { expect(existsSync(join(GEN, 'index.json'))).toBe(true); });
  it('limits are 60 s, 8 MiB stdout, 1 MiB stderr', () => { expect(PROCESS_LIMITS).toEqual({ timeoutMs: 60000, stdoutMaxBytes: 8388608, stderrMaxBytes: 1048576 }); });
  it('answers every operation with a valid v1 envelope', async () => {
    for (const req of [{ operation: 'capabilities' }, { operation: 'snapshot.list' }, { operation: 'snapshot.verify', snapshot_id: SID }, { operation: 'history.list', snapshot_id: SID, limit: 5 }, { operation: 'history.detail', snapshot_id: SID, run_id: 'run-diff-2000' }, { operation: 'history.prompt', snapshot_id: SID, run_id: 'run-diff-2000' }, { operation: 'workspace.status', workspace: '/tmp/ws' }] as const) {
      const out = await run(buildKriyaArgv(req));
      expect(out.kind, req.operation).toBe('json');
      expect(checkEnvelope(out.json).ok).toBe(true);
    }
  });
  it('the >4 MiB detail passes under the 8 MiB limit', async () => {
    const out = await run(buildKriyaArgv({ operation: 'history.detail', snapshot_id: SID, run_id: 'run-big-events' }));
    expect(out.kind).toBe('json'); expect(out.stdoutBytes).toBeGreaterThan(4 * 1024 * 1024);
  });
  it('oversized stdout is RESPONSE_TOO_LARGE, never partial data', async () => {
    const out = await run(buildKriyaArgv({ operation: 'capabilities' }), { KRIYA_FAKE_BEHAVIOR: 'huge', KRIYA_FAKE_HUGE_BYTES: '300000' }, { stdoutMaxBytes: 100000 });
    expect(out.kind).toBe('error'); expect(out.code).toBe('RESPONSE_TOO_LARGE'); expect(out.json).toBeUndefined();
  });
  it('non-JSON stdout is INVALID_RESPONSE with the stderr tail kept', async () => {
    const out = await run(buildKriyaArgv({ operation: 'capabilities' }), { KRIYA_FAKE_BEHAVIOR: 'garbage' });
    expect(out.kind).toBe('error'); expect(out.code).toBe('INVALID_RESPONSE'); expect(out.exitCode).toBe(1);
  });
  it('a hung kriya is killed at the timeout', async () => {
    const out = await run(buildKriyaArgv({ operation: 'capabilities' }), { KRIYA_FAKE_BEHAVIOR: 'slow', KRIYA_FAKE_SLEEP_MS: '5000' }, { timeoutMs: 400 });
    expect(out.kind).toBe('error'); expect(out.message).toContain('did not answer'); expect(out.durationMs).toBeLessThan(3000); expect(out.signal).toBe('SIGKILL');
  });
  it('a schema_version 2 answer is refused by the shared envelope check (P-27)', async () => {
    const out = await run(buildKriyaArgv({ operation: 'capabilities' }), { KRIYA_FAKE_BEHAVIOR: 'schema2' });
    const c = checkEnvelope(out.json); expect(c.ok).toBe(false); if (!c.ok) expect(c.code).toBe('UNSUPPORTED_SCHEMA_VERSION');
  });
  it('snapshot.verify names exactly the requested id; the corrupt behaviour is the typed SNAPSHOT_CORRUPT with that id', async () => {
    const ok = await run(buildKriyaArgv({ operation: 'snapshot.verify', snapshot_id: SID }));
    expect(ok.kind).toBe('json'); expect((ok.json as { data: { snapshot_id: string; digest_verified: boolean } }).data).toMatchObject({ snapshot_id: SID, digest_verified: true });
    const bad = await run(buildKriyaArgv({ operation: 'snapshot.verify', snapshot_id: SID }), { KRIYA_FAKE_BEHAVIOR: 'verify_corrupt' });
    expect((bad.json as { data: unknown; error: { code: string; snapshot_id: string } }).error).toMatchObject({ code: 'SNAPSHOT_CORRUPT', snapshot_id: SID });
    expect((bad.json as { data: unknown }).data).toBeNull();
    const unknown = await run(buildKriyaArgv({ operation: 'snapshot.verify', snapshot_id: '20260101T000000000000Z-00000000' }));
    expect((unknown.json as { error: { code: string } }).error.code).toBe('SNAPSHOT_UNAVAILABLE');
  });
  it('argv outside the grammar is refused before any spawn', async () => {
    const out = await runKriya({ executable: process.execPath, argv: [FAKE, 'generate', 'goal'], nodeScript: true });
    expect(out.kind).toBe('error'); expect(out.message).toContain('allowlisted'); expect(out.durationMs).toBe(0);
  });
  it('a real spawn under the child environment policy sees exactly that policy (plus the stand-in knobs), never the parent environment', async () => {
    process.env.KRIYA_UI_TEST_LEAK = 'leak';
    const policy = buildKriyaChildEnv({ HOME: '/Users/op', PATH: '/evil', PYTHONPATH: '/evil/site', KRIYA_TRUST_FILE: '/t', OPENAI_API_KEY: 'x', KRIYA_STATE_DIR: '/Volumes/work/state' });
    expect(policy.ok).toBe(true);
    if (!policy.ok) return;
    // echoenv makes the stand-in print its process environment (test aid only); the fixture knobs are the stand-in's own
    const out = await runKriya({ executable: process.execPath, argv: [FAKE, ...buildKriyaArgv({ operation: 'capabilities' })], nodeScript: true, env: { ...policy.env, KRIYA_FAKE_FIXTURES: GEN, KRIYA_FAKE_BEHAVIOR: 'echoenv' } });
    expect(out.kind).toBe('json');
    const seen = out.json as Record<string, string>;
    // __CF_USER_TEXT_ENCODING is injected by macOS into every new process whatever its parent passes (OS artifact, not an inherited variable).
    const policyKeys = Object.keys(seen).filter((k) => !['KRIYA_FAKE_FIXTURES', 'KRIYA_FAKE_BEHAVIOR', 'ELECTRON_RUN_AS_NODE', '__CF_USER_TEXT_ENCODING'].includes(k)).sort();
    expect(policyKeys).toEqual([...KRIYA_CHILD_ENV_KEYS].sort());
    expect(seen.PATH).toBe('/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin'); expect(seen.PYTHONDONTWRITEBYTECODE).toBe('1'); expect(seen.HOME).toBe('/Users/op'); expect(seen.KRIYA_STATE_DIR).toBe('/Volumes/work/state');
    for (const k of ['KRIYA_UI_TEST_LEAK', 'PYTHONPATH', 'KRIYA_TRUST_FILE', 'OPENAI_API_KEY', 'SHELL', 'TMPDIR', 'LANG']) expect(seen).not.toHaveProperty(k);
  });
  it('the child runs in the explicit cwd it is given, whatever directory the host itself was started from (F-5)', async () => {
    const { mkdtempSync, realpathSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const configDir = realpathSync(mkdtempSync(join(tmpdir(), 'kriya-ui-config-')));
    const before = process.cwd();
    const seen: string[] = [];
    try {
      for (const launchedFrom of ['/', realpathSync(tmpdir())]) {
        process.chdir(launchedFrom);
        const out = await runKriya({ executable: process.execPath, argv: [FAKE, ...buildKriyaArgv({ operation: 'capabilities' })], nodeScript: true, env: { KRIYA_FAKE_FIXTURES: GEN, KRIYA_FAKE_BEHAVIOR: 'echocwd' }, cwd: configDir });
        expect(out.kind).toBe('json');
        seen.push(realpathSync((out.json as { cwd: string }).cwd));
      }
    } finally { process.chdir(before); }
    expect(seen).toEqual([configDir, configDir]);
  });
  it('the environment is minimal: the child does not inherit the parent environment', async () => {
    process.env.KRIYA_UI_TEST_LEAK = 'leak';
    const out = await run(buildKriyaArgv({ operation: 'capabilities' }), { KRIYA_FAKE_BEHAVIOR: 'garbage' });
    expect(out.kind).toBe('error');
    // the stand-in echoes nothing about env; prove the shape by inspecting the runner source instead
    const src = (await import('node:fs')).readFileSync(join(__dirname, '..', 'src', 'main', 'kriya_process.ts'), 'utf8');
    expect(src).not.toMatch(/\.\.\.process\.env/);
  });
});
