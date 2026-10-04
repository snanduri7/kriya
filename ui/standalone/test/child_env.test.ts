/** The child environment policy for the real kriya (08 review F-1/F-2; owner decision 2026-10-04). */
import { describe, expect, it } from 'vitest';
import { KRIYA_CHILD_ENV_KEYS, KRIYA_CHILD_PATH, buildKriyaChildEnv } from '../src/main/child_env';

const HOSTILE = {
  HOME: '/Users/op', PATH: '/evil/bin:/usr/bin', PYTHONDONTWRITEBYTECODE: '0', PYTHONPATH: '/evil/site', PYTHONHOME: '/evil/py',
  KRIYA_TRUST_FILE: '/tmp/trust.json', KRIYA_AUTHORITY_HOME: '/tmp/auth', KRIYA_CONFIG: '/tmp/kriya.yaml', KRIYA_LOG_DIR: '/tmp/logs',
  OPENAI_API_KEY: 'sk-secret', OLLAMA_HOST: 'http://evil:11434', HTTP_PROXY: 'http://proxy', LANG: 'en_US.UTF-8', TMPDIR: '/tmp', SHELL: '/bin/zsh',
};

describe('buildKriyaChildEnv', () => {
  it('passes exactly PATH (fixed), PYTHONDONTWRITEBYTECODE (fixed 1) and HOME; nothing of the operator environment leaks', () => {
    const r = buildKriyaChildEnv(HOSTILE);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.env).toEqual({ PATH: KRIYA_CHILD_PATH, PYTHONDONTWRITEBYTECODE: '1', HOME: '/Users/op' });
    for (const k of ['PYTHONPATH', 'PYTHONHOME', 'KRIYA_TRUST_FILE', 'KRIYA_AUTHORITY_HOME', 'KRIYA_CONFIG', 'KRIYA_LOG_DIR', 'OPENAI_API_KEY', 'OLLAMA_HOST', 'HTTP_PROXY', 'LANG', 'TMPDIR', 'SHELL']) expect(r.env).not.toHaveProperty(k);
    expect(KRIYA_CHILD_PATH).toBe('/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin');
    expect(Object.keys(r.env).every((k) => (KRIYA_CHILD_ENV_KEYS as readonly string[]).includes(k))).toBe(true);
  });
  it('an operator KRIYA_STATE_DIR passes through only when absolute; a relative or control-character value refuses the launch', () => {
    const ok = buildKriyaChildEnv({ ...HOSTILE, KRIYA_STATE_DIR: '/Volumes/work/state' });
    expect(ok.ok && ok.env.KRIYA_STATE_DIR).toBe('/Volumes/work/state');
    expect(ok.ok && Object.keys(ok.env).sort()).toEqual([...KRIYA_CHILD_ENV_KEYS].sort());
    for (const bad of ['relative/state', '~/state', '.kriya-state', '', '/a\nb', '/a\u0000b']) {
      const r = buildKriyaChildEnv({ ...HOSTILE, KRIYA_STATE_DIR: bad });
      expect(r.ok, JSON.stringify(bad)).toBe(false);
      if (!r.ok) expect(r.message).toContain('KRIYA_STATE_DIR');
    }
  });
  it('without an absolute HOME the launch is refused rather than guessed', () => {
    for (const home of [undefined, '', 'relative', 'C:\\Users\\x']) {
      const r = buildKriyaChildEnv({ ...HOSTILE, HOME: home });
      expect(r.ok, String(home)).toBe(false);
    }
  });
  it('PYTHONDONTWRITEBYTECODE is a constant, never inherited', () => {
    for (const v of [undefined, '0', '', 'false']) {
      const r = buildKriyaChildEnv({ HOME: '/Users/op', PYTHONDONTWRITEBYTECODE: v });
      expect(r.ok && r.env.PYTHONDONTWRITEBYTECODE).toBe('1');
    }
  });
});
