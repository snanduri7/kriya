/** GUI v1 hardening (Phase M): bounded, local, seeded fuzzing of every request validator the renderer can reach. Properties, for 20,000 generated inputs per validator:
 *  - a validator NEVER throws, whatever it is given;
 *  - an accepted value satisfies an INDEPENDENT statement of the grammar (not the validator's own code);
 *  - an accepted value is a fresh plain object with only known keys (no prototype pollution, no extra field survives);
 *  - the command line built from an accepted request is inside the allowlist and no user-controlled value can be read as an option. */
import { describe, expect, it } from 'vitest';
import {
  CANDIDATE_DIGEST_RE, LAUNCH_ID_RE, REVIEW_ID_RE, validateCursor, validateDecideApproval, validateKupRequest, validateOpenInIde, validateRunId, validateSettingKey, validateSettingValue, validateStartRun,
  validateWorkspacePath,
} from '../src/main/ipc_contract';
import { argvIsAllowed, buildKriyaArgv } from '../src/main/kriya_argv';

function lcg(seed: number) { let s = seed >>> 0; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 2 ** 32; }; }
const rand = lcg(20261011);
const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(rand() * xs.length)]!;
const CHARS = ['a', 'Z', '0', '9', '-', '_', '.', ':', '/', ' ', '\u0000', '\n', '\r', '\t', '‮', 'é', '\u{1F600}', '`', '$', '(', ')', ';', '|', '&', '"', "'", '\\', '*', '~', '=', '+', '<', '>', '%', '\ud800'];
const str = (max = 40): string => Array.from({ length: Math.floor(rand() * max) }, () => pick(CHARS)).join('');
const WEIRD: unknown[] = [null, undefined, true, false, 0, -1, 1.5, NaN, Infinity, 2 ** 53, '', [], {}, [[]], () => 1, Symbol('x'), 10n, new Date(0), /x/, { toString: () => 'x' }, Object.create(null), { __proto__: { polluted: 1 } }];
const OPS = ['capabilities', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify', 'history.list', 'history.detail', 'history.prompt', 'evidence.manifest', 'evidence.verify', 'evidence.records',
  'evidence.change_content', 'evidence.call_content', 'workspace.status', 'workspace.describe', 'execution.preflight', 'launch.status', 'workspace.create', 'execution.context', 'execution.model_probe', 'shell.exec', 'evidence.__proto__', ''];
const KEYS = ['operation', 'workspace', 'run_id', 'snapshot_id', 'launch_id', 'parent', 'name', 'token', 'kind', 'cursor', 'limit', 'seq', 'part', 'kind', 'unit', 'phase', 'role', 'attempt', 'from_seq', 'keep', 'verify', 'from_trace', 'goal', 'path', 'line', '__proto__', 'constructor', 'extra', 'argv', 'command', 'shell'];
const value = (depth = 0): unknown => {
  const r = rand();
  if (r < 0.4) return str();
  if (r < 0.55) return Math.floor(rand() * 2000) - 5;
  if (r < 0.65 || depth > 2) return pick(WEIRD);
  if (r < 0.8) return Array.from({ length: Math.floor(rand() * 4) }, () => value(depth + 1));
  return object(depth + 1);
};
const object = (depth = 0): Record<string, unknown> => Object.fromEntries(Array.from({ length: Math.floor(rand() * 7) }, () => [pick(KEYS), value(depth)]));
const kupRequest = (): unknown => (rand() < 0.15 ? value() : { ...object(), operation: pick(OPS) });
const N = 20_000;
const plain = (v: unknown) => v !== null && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype;

describe('validators never throw and accept only the grammar', () => {
  it('validateKupRequest: no throw; an accepted request is a plain object whose argv is allowlisted and option-safe', () => {
    let accepted = 0;
    for (let i = 0; i < N; i += 1) {
      const input = kupRequest();
      const result = validateKupRequest(input);
      if (!result.ok) { expect(typeof result.message).toBe('string'); continue; }
      accepted += 1;
      expect(plain(result.value)).toBe(true);
      expect(({} as Record<string, unknown>).polluted).toBeUndefined();
      expect(OPS).toContain(result.value.operation);
      expect(result.value.operation).not.toBe('workspace.create'); // never accepted from the renderer
      if (result.value.operation === 'execution.context' || result.value.operation === 'execution.model_probe') { expect(() => buildKriyaArgv(result.value)).toThrow(/persistent Kriya service/); continue; } // served by the persistent service only
      const argv = buildKriyaArgv(result.value);
      expect(argvIsAllowed(argv), argv.join(' ')).toBe(true);
      for (const field of ['run_id', 'snapshot_id', 'cursor', 'launch_id', 'workspace'] as const) {
        const v = (result.value as unknown as Record<string, unknown>)[field];
        if (typeof v === 'string') expect(v.startsWith('-') && argv.includes(v), `${field} ${v} would read as an option`).toBe(false);
      }
      for (const a of argv) { expect(typeof a).toBe('string'); expect(a).not.toMatch(/\u0000/); }
    }
    expect(accepted).toBeGreaterThan(0);
  });

  it('validateRunId / validateCursor / validateWorkspacePath: independent grammar checks', () => {
    for (let i = 0; i < N; i += 1) {
      const v = rand() < 0.2 ? value() : str(70);
      const run = validateRunId(v);
      if (run.ok) expect(run.value).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/);
      const cursor = validateCursor(v);
      if (cursor.ok) expect(cursor.value).toMatch(/^[A-Za-z0-9][A-Za-z0-9+/=_-]*$/);
      const ws = validateWorkspacePath(v);
      if (ws.ok) { expect(ws.value.startsWith('/')).toBe(true); expect(ws.value).not.toMatch(/[\u0000-\u001f]/); }
    }
    for (const ok of ['run-1', '20261011T094647-bc1ca4c4', 'a.b:c_d']) expect(validateRunId(ok).ok, ok).toBe(true);
    for (const bad of ['', '-x', '.hidden', '../x', 'a b', 'a/b', 'a\u0000', 'é', 'a\n']) expect(validateRunId(bad).ok, JSON.stringify(bad)).toBe(false);
  });

  it('validateStartRun and validateDecideApproval: exact shapes only', () => {
    for (let i = 0; i < N; i += 1) {
      const input = rand() < 0.3 ? value() : { workspace: rand() < 0.5 ? '/tmp/' + str(10) : str(10), goal: str(30), ...(rand() < 0.2 ? { extra: 1 } : {}) };
      const r = validateStartRun(input);
      if (r.ok) { expect(Object.keys(r.value).sort()).toEqual(['goal', 'workspace']); expect(r.value.goal.trim().length).toBeGreaterThan(0); expect(r.value.goal).not.toContain('\u0000'); expect(r.value.workspace.startsWith('/')).toBe(true); }
      const d = validateDecideApproval(rand() < 0.3 ? value() : { workspace: '/w/' + str(5), launch_id: rand() < 0.5 ? '20261010T101010-abcdef01' : str(20), review_id: rand() < 0.5 ? 'ra-' + 'a1'.repeat(12) : str(27), candidate_digest: rand() < 0.5 ? 'sha256:' + 'b2'.repeat(32) : str(71), decision: pick(['approve', 'reject', 'approve-always', 'Approve', '', 1, null]) });
      if (d.ok) {
        expect(Object.keys(d.value).sort()).toEqual(['candidate_digest', 'decision', 'launch_id', 'review_id', 'workspace']);
        expect(d.value.launch_id).toMatch(LAUNCH_ID_RE); expect(d.value.review_id).toMatch(REVIEW_ID_RE); expect(d.value.candidate_digest).toMatch(CANDIDATE_DIGEST_RE);
        expect(['approve', 'reject']).toContain(d.value.decision);
      }
    }
  });

  it('validateOpenInIde / settings: no throw, accepted values are inside their grammar', () => {
    for (let i = 0; i < N; i += 1) {
      const o = validateOpenInIde(rand() < 0.3 ? value() : { path: '/p/' + str(20), ...(rand() < 0.5 ? { line: pick([1, 0, -1, 1.5, 10_000_001, '3', null]) } : {}) });
      if (o.ok) { expect(o.value.path).not.toMatch(/[\u0000-\u001f]/); if (o.value.line !== undefined) expect(Number.isInteger(o.value.line) && o.value.line >= 1).toBe(true); }
      const k = validateSettingKey(rand() < 0.5 ? pick(['editor', 'kriyaExecutable', 'workspacePath', 'configDirectory', '__proto__', 'x', 1, null]) : value());
      if (k.ok) {
        const v = validateSettingValue(k.value, rand() < 0.3 ? value() : pick(['vscode', '/abs/path', 'relative', '', '/a\u0000b', null, 'x'.repeat(5000)]));
        if (v.ok && typeof v.value === 'string' && k.value !== 'editor') expect(v.value.startsWith('/')).toBe(true);
      }
    }
  });
});


describe('New Project, notification and settings guards never throw and accept only their grammar (seeded fuzz)', () => {
  it('ProjectHost.create: nothing reaches Kriya unless it is exactly {live token, name}; a refused/odd answer approves nothing', async () => {
    const { ProjectHost, PROJECT_TOKEN_RE } = await import('../src/main/project_host');
    let n = 0;
    const asked: Array<[string, string]> = []; const approved: string[] = [];
    const answers: unknown[] = [null, 'x', 7, {}, { error: null, data: null }, { error: null, data: { workspace: 'rel' } }, { error: { code: 'X' }, data: null }, { error: null, data: { workspace: '/ok/a' } }, { data: { workspace: '/ok/b' }, error: { code: 'WORKSPACE_UNAVAILABLE', message: 'm', reason: 'already_exists' } }];
    let k = 0;
    const host = new ProjectHost({ create: async (p, name) => { asked.push([p, name]); return answers[k++ % answers.length]; }, approveCreated: (p) => { approved.push(p); }, runActive: () => false, now: () => 0, mintToken: () => `pt-${String(++n).padStart(32, '0')}` });
    const parents = ['/a', 'rel', '', '/a\nb', '/' + 'x'.repeat(5000)];
    let liveTokens: string[] = [];
    for (let i = 0; i < 4000; i += 1) {
      if (rand() < 0.1) { const c = await host.chooseParent(async () => pick(parents)); if (c.chosen) liveTokens.push(c.token!); expect(c.chosen ? PROJECT_TOKEN_RE.test(c.token!) : c.token === null).toBe(true); }
      const raw = rand() < 0.5 ? { token: rand() < 0.6 && liveTokens.length ? pick(liveTokens) : str(40), name: rand() < 0.7 ? str(120) : value() } : value();
      const r = await host.create(raw);
      expect(typeof r.created).toBe('boolean');
      if (r.created) { expect(r.path && r.path.startsWith('/')).toBe(true); expect(approved).toContain(r.path); }
      liveTokens = liveTokens.slice(-4);
    }
    for (const [parent, name] of asked) { expect(parent.startsWith('/')).toBe(true); expect(typeof name).toBe('string'); expect(name.length).toBeGreaterThan(0); }
    expect(approved.every((p) => p.startsWith('/'))).toBe(true);
  });
  it('NotificationHost.notify: only a closed kind for the current launch is ever shown, and the text never contains renderer input', async () => {
    const { NotificationHost, NOTIFY_KINDS } = await import('../src/main/notification_host');
    const LID = '20261011T101010-abcdef01';
    const shown: string[] = [];
    const host = new NotificationHost({ supported: () => true, windowFocused: () => false, currentLaunch: () => LID, workspacePath: () => '/Users/p/proj', show: (spec) => { shown.push(`${spec.title}|${spec.body}`); }, onActivate: () => undefined, now: () => rand() * 1e9 });
    for (let i = 0; i < 20000; i += 1) {
      const raw = rand() < 0.4 ? { kind: pick([...NOTIFY_KINDS, str(10), ...WEIRD]), launch_id: rand() < 0.7 ? LID : str(30), ...(rand() < 0.2 ? { body: str(30), title: str(30) } : {}) } : value();
      const r = host.notify(raw);
      expect(typeof r.shown).toBe('boolean');
      if (r.shown) { expect(raw).toMatchObject({ launch_id: LID }); expect((NOTIFY_KINDS as readonly string[]).includes((raw as { kind: string }).kind)).toBe(true); }
    }
    expect(shown.length).toBeGreaterThan(0);
    for (const text of shown) expect(text).toMatch(/^Kriya (needs your approval|run finished|run did not finish successfully)\|/);
  });
  it('rendererMayWrite: no input combination lets the cockpit renderer write a host-owned setting', async () => {
    const { rendererMayWrite, validateSettingKey: key, validateSettingValue: val, HOST_OWNED_SETTINGS } = await import('../src/main/ipc_contract');
    for (let i = 0; i < 20000; i += 1) {
      const k = key(rand() < 0.5 ? pick(['editor', 'kriyaExecutable', 'workspacePath', 'configDirectory', ...HOST_OWNED_SETTINGS]) : value());
      if (!k.ok) continue;
      const v = val(k.value, rand() < 0.5 ? (rand() < 0.5 ? null : '/ok/path') : value());
      if (!v.ok) continue;
      if ((HOST_OWNED_SETTINGS as readonly string[]).includes(k.value)) expect(rendererMayWrite(k.value, 'cockpit', v.value), `${k.value}=${String(v.value)}`).toBe(false);
      if (k.value === 'workspacePath' && v.value !== null) expect(rendererMayWrite(k.value, 'cockpit', v.value)).toBe(false);
    }
  });
});
