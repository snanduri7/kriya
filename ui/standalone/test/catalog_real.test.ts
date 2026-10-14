/** Skills and Tools against the REAL `kriya kup-stdio`: the host's service asks Kriya (`skills.*`, `tools.*`), and every real answer validates against the generated KUP
 * validators the GUI uses. This is the producer-and-consumer check the mocked fixture tests cannot make. Never calls a model. Needs the repository's .venv. */
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { validateData } from '@kriya-ui/kup';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { KriyaService } from '../src/main/kriya_service';

const ROOT = join(__dirname, '..', '..', '..');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const available = existsSync(KRIYA);

describe.skipIf(!available)('Skills and Tools, real Kriya', () => {
  let base = ''; let workspace = ''; let refused = ''; let service: KriyaService;
  const listing = (dir: string): string[] => readdirSync(dir, { recursive: true }).map(String).sort();

  beforeAll(() => {
    base = realpathSync(mkdtempSync(join(tmpdir(), 'kriya-catalog-')));
    workspace = join(base, 'proj'); refused = join(base, 'refused');
    const alpha = join(workspace, 'skills', 'alpha');
    mkdirSync(alpha, { recursive: true });
    writeFileSync(join(alpha, 'skill.yaml'), "name: alpha\ndescription: Alpha rules\nverified: true\nverified_at: '2026-10-01'\n");
    writeFileSync(join(alpha, 'rules.txt'), 'SECRET PROPRIETARY RULE one\nrule two\n');
    writeFileSync(join(alpha, 'rule_provenance.json'), JSON.stringify([{ text: 'rule two', verified: false, source: 'https://docs.example.org/a', added_at: '2026-09-30' }]));
    writeFileSync(join(workspace, 'kriya.yaml'), 'paths:\n  skills: ./skills\nskills:\n  load_global: false\n  load_cwd: false\n');
    // A folder-supplied MCP server is a security-authority field: Kriya must refuse it, not list it.
    mkdirSync(refused);
    writeFileSync(join(refused, 'kriya.yaml'), 'mcp:\n  evil:\n    command: /bin/sh\n    args: ["-c", "echo SECRETARG"]\n    env:\n      TOKEN: SECRETENV\n');
    const config = join(base, 'config'); const state = join(base, 'state'); const home = join(base, 'home');
    for (const d of [config, state, home]) mkdirSync(d);
    service = new KriyaService({ executable: KRIYA, cwd: config, env: { HOME: home, KRIYA_STATE_DIR: state, KRIYA_MCP_APPROVAL_HOME: join(base, 'approvals'), PYTHONDONTWRITEBYTECODE: '1' } });
  }, 60_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  const ask = async (request: Parameters<KriyaService['request']>[0]) => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(out.message);
    const env = out.json as { error: { code: string } | null; data: Record<string, any> | null };
    if (env.error === null) { const checked = validateData(request.operation, env.data); expect(checked.ok ? [] : checked.errors, request.operation).toEqual([]); }
    return env;
  };

  it('skills.list / skills.describe: real descriptors that validate, with provenance and no rule text or path', async () => {
    const before = listing(base);
    const list = await ask({ operation: 'skills.list', workspace });
    expect(list.error).toBeNull();
    expect(list.data!.items.map((s: { id: string }) => s.id)).toEqual(['alpha']);
    const alpha = list.data!.items[0];
    expect(alpha).toMatchObject({ verified: true, rule_count: 2, scope: 'workspace', skill_version: null });
    expect(alpha.provenance).toMatchObject({ state: 'RECORDED', tracked_rule_count: 1, untracked_rule_count: 1 });
    expect(alpha.provenance.sources[0].source).toBe('https://docs.example.org/a');
    expect(JSON.stringify(list.data!.items)).not.toMatch(/SECRET|PROPRIETARY/);
    expect(JSON.stringify(list.data!.items)).not.toContain(base); // identity is the id; no descriptor carries a path
    const one = await ask({ operation: 'skills.describe', workspace, skill_id: 'ALPHA' });
    expect(one.data).toMatchObject({ found: true, skill: { id: 'alpha' } });
    const none = await ask({ operation: 'skills.describe', workspace, skill_id: 'nope' });
    expect(none.data).toMatchObject({ found: false, skill: null });
    expect(listing(base)).toEqual(before); // reading wrote nothing, anywhere
    expect(readFileSync(join(workspace, 'skills', 'alpha', 'rules.txt'), 'utf8')).toContain('SECRET PROPRIETARY RULE one');
  }, 60_000);

  it('tools.*: a folder-supplied MCP server is refused by Kriya (authority), listed as nothing, and its command, arguments and environment never appear', async () => {
    for (const request of [{ operation: 'tools.list', workspace: refused }, { operation: 'tools.describe', workspace: refused, tool_id: 'mcp:evil' },
      { operation: 'tools.approval_state', workspace: refused, tool_id: 'mcp:evil' }] as const) {
      const env = await ask(request);
      expect(env.error).toBeNull();
      expect(env.data).toMatchObject({ available: false, authority_status: 'refused' });
      expect(env.data!.unavailable_reason.code).toBe('CONFIG_AUTHORITY_REFUSED');
      expect(JSON.stringify(env)).not.toMatch(/SECRETARG|SECRETENV|\/bin\/sh/);
    }
  }, 60_000);

  it('tools.list on a folder with no tools is an available, empty answer; an unknown tool is found:false / UNKNOWN, never approved', async () => {
    const list = await ask({ operation: 'tools.list', workspace });
    expect(list.data).toMatchObject({ available: true, total: 0, items: [] });
    const state = await ask({ operation: 'tools.approval_state', workspace, tool_id: 'mcp:ghost' });
    expect(state.data).toMatchObject({ found: false, approval: { state: 'UNKNOWN', reason: 'TOOL_NOT_FOUND' } });
  }, 60_000);

  it('a bad id is a typed INVALID_REQUEST from Kriya, not a crash', async () => {
    const out = await service.request({ operation: 'skills.describe', workspace, skill_id: 'a\u0000b' } as never);
    expect(out.kind === 'json' ? (out.json as { error: { code: string } }).error.code : out.kind).toBe('INVALID_REQUEST');
  }, 60_000);
});
