/**
 * GUI-F-A1 (operator-trust / host-state consistency): the trust strip's host state must agree with the execution mode
 * the query handler uses. Before the fix main.ts decided "real" twice with two different rules (hostInfo: a configured
 * string; query: a configured string that exists), so a nonexistent configured executable showed a real host while
 * every query was answered by the fixture stand-in.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { isFixtureMode, resolveHostMode } from '../src/main/host_mode';

const EXE = '/opt/kriya/.venv/bin/kriya';
const present = (p: string) => p === EXE;
const absent = () => false;

describe('resolveHostMode: one rule for hostInfo and query', () => {
  it('real mode + executable exists -> real, not fixture', () => {
    const mode = resolveHostMode(true, EXE, present);
    expect(mode).toEqual({ mode: 'real', executable: EXE });
    expect(isFixtureMode(mode)).toBe(false);
  });
  it('real mode + executable missing -> typed error, never the fixture stand-in, never "fixtureMode"', () => {
    const mode = resolveHostMode(true, '/nowhere/kriya', absent);
    expect(mode.mode).toBe('error');
    expect(mode.mode === 'error' && mode.message).toContain('/nowhere/kriya');
    expect(isFixtureMode(mode)).toBe(false);
  });
  it('fixture mode: the D-9 gate closed, or no executable configured, whatever exists on disk', () => {
    for (const [allow, configured] of [[false, EXE], [false, null], [true, null], [true, ''], [false, '/nowhere/kriya']] as const) {
      const mode = resolveHostMode(allow, configured, present);
      expect(mode, `${allow} ${configured}`).toEqual({ mode: 'fixture' });
      expect(isFixtureMode(mode)).toBe(true);
    }
  });
  it('configured path changes from valid to invalid: the decision follows the current state of the disk and the setting', () => {
    let onDisk = new Set([EXE]);
    const exists = (p: string) => onDisk.has(p);
    expect(resolveHostMode(true, EXE, exists).mode).toBe('real');
    onDisk = new Set(); // the venv was removed
    expect(resolveHostMode(true, EXE, exists).mode).toBe('error');
    expect(resolveHostMode(true, '/other/kriya', exists).mode).toBe('error'); // the setting now names a missing path
    onDisk = new Set(['/other/kriya']);
    expect(resolveHostMode(true, '/other/kriya', exists)).toEqual({ mode: 'real', executable: '/other/kriya' });
  });
  it('an operator-visible error names the typed HOST_ERROR contract, like an invalid configuration directory', () => {
    const mode = resolveHostMode(true, '/nowhere/kriya', absent);
    expect(mode.mode === 'error' && mode.message).toMatch(/does not exist/);
  });
});

describe('main.ts consults the one decision for BOTH handlers (trust strip == query behaviour)', () => {
  const mainTs = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
  it('hostInfo and query each call resolveHostMode, and no handler re-derives real mode from the setting alone', () => {
    expect((mainTs.match(/resolveHostMode\(ALLOW_REAL, settings\.get\('kriyaExecutable'\), existsSync\)/g) ?? []).length).toBe(2);
    expect(mainTs).toContain("fixtureMode: isFixtureMode(");
    expect(mainTs).not.toMatch(/!!settings\.get\('kriyaExecutable'\)/);
    expect(mainTs).not.toMatch(/ALLOW_REAL && configured/);
  });
  it('an error mode is a typed HOST_ERROR before any child is spawned', () => {
    expect(mainTs).toMatch(/host\.mode === 'error'\) return errorEnvelope\(v\.value\.operation, 'HOST_ERROR', `kriya not launched: \$\{host\.message\}`\)/);
  });
});
