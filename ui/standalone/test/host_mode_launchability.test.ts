/**
 * GUI M1 closure: a configured kriya executable that EXISTS but cannot be launched (a directory, a regular file
 * without execute permission). Measures the real launch path (kriya_process.ts spawn) and the resolver.
 * UNCOMMITTED closure evidence - proposed addition, see the GUI M1 final closure report.
 */
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { resolveHostMode, isFixtureMode } from '../src/main/host_mode';
import { runKriya } from '../src/main/kriya_process';

const ARGV = ['traces', '--json', '--capabilities'];
const dir = mkdtempSync(join(tmpdir(), 'kriya-launchability-'));
const directoryPath = join(dir, 'a-directory'); // a directory named like an executable
const nonExecFile = join(dir, 'kriya-not-executable');
writeFileSync(nonExecFile, '#!/bin/sh\necho should-never-run\n'); chmodSync(nonExecFile, 0o644);
const missing = join(dir, 'does-not-exist');
import { mkdirSync } from 'node:fs';
mkdirSync(directoryPath);

describe('invalid configured executables that exist on disk', () => {
  it('B. a DIRECTORY: resolver says real (existsSync is true), the launch fails -> typed HOST_ERROR, no payload', async () => {
    const mode = resolveHostMode(true, directoryPath, existsSync);
    expect(mode.mode).toBe('real');
    expect(isFixtureMode(mode)).toBe(false);
    const outcome = await runKriya({ executable: directoryPath, argv: ARGV, env: { HOME: dir }, cwd: dir });
    expect(outcome.kind).toBe('error');
    expect(outcome.code).toBe('HOST_ERROR');
    expect(outcome.message).toMatch(/spawn failed: spawn .*E(ACCES|ISDIR|PERM)/);
    expect(outcome.json).toBeUndefined();
    expect(outcome.exitCode).toBeNull();
    expect(outcome.stdoutBytes).toBe(0);
  });
  it('C. a REGULAR FILE WITHOUT EXECUTE PERMISSION: same - typed HOST_ERROR, nothing ran', async () => {
    const mode = resolveHostMode(true, nonExecFile, existsSync);
    expect(mode.mode).toBe('real');
    const outcome = await runKriya({ executable: nonExecFile, argv: ARGV, env: { HOME: dir }, cwd: dir });
    expect(outcome.kind).toBe('error');
    expect(outcome.code).toBe('HOST_ERROR');
    expect(outcome.message).toMatch(/spawn failed: spawn .*EACCES/);
    expect(outcome.json).toBeUndefined();
    expect(outcome.stdoutBytes).toBe(0);
  });
  it('A. a MISSING path is refused by the resolver before any spawn (GUI-F-A1), and the spawn path would also refuse it', async () => {
    const mode = resolveHostMode(true, missing, existsSync);
    expect(mode.mode).toBe('error');
    expect(isFixtureMode(mode)).toBe(false);
    const outcome = await runKriya({ executable: missing, argv: ARGV, env: { HOME: dir }, cwd: dir });
    expect(outcome.kind).toBe('error');
    expect(outcome.code).toBe('HOST_ERROR');
    expect(outcome.message).toMatch(/ENOENT/);
  });
});
