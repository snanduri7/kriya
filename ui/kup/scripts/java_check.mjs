#!/usr/bin/env node
/**
 * Java usability check (gate A-2 P-R4, 06 Phase B items 2 and 4): compile the GENERATED Java types with plain javac
 * (no Maven/Gradle - D-9) against pinned, digest-verified Jackson jars, then run RoundTrip over the golden fixtures.
 * Exit 1 on any failure; exit 3 with a clear message when no JDK is available (reported, never faked as a pass).
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const JAVA = join(ROOT, 'java'), LIB = join(JAVA, 'lib'), GEN = join(ROOT, 'generated', 'java', 'kup'), OUT = join(JAVA, 'build');
const FIXTURES = process.argv[2] ?? join(ROOT, '..', 'fixtures', 'generated');
const MAVEN = 'https://repo1.maven.org/maven2/com/fasterxml/jackson/core';

const which = (cmd) => spawnSync('/usr/bin/which', [cmd], { encoding: 'utf8' }).status === 0;
if (!which('javac') || !which('java')) { console.error('java_check: no JDK on PATH (javac/java) - the Java check cannot run on this machine'); process.exit(3); }

const lock = readFileSync(join(JAVA, 'JACKSON.lock'), 'utf8').split('\n').filter((l) => l && !l.startsWith('#')).map((l) => l.trim().split(/\s+/));
mkdirSync(LIB, { recursive: true });
for (const [jar, sha256] of lock) {
  const path = join(LIB, jar);
  if (!existsSync(path)) {
    const artifact = jar.replace(/-\d.*$/, ''), version = jar.replace(/^.*-(\d[^-]*)\.jar$/, '$1');
    const res = await fetch(`${MAVEN}/${artifact}/${version}/${jar}`);
    if (!res.ok) { console.error(`java_check: download of ${jar} failed: ${res.status}`); process.exit(1); }
    writeFileSync(path, Buffer.from(await res.arrayBuffer()));
  }
  const digest = createHash('sha256').update(readFileSync(path)).digest('hex');
  if (digest !== sha256) { console.error(`java_check: ${jar} digest ${digest} != pinned ${sha256}`); process.exit(1); }
}
const cp = lock.map(([jar]) => join(LIB, jar)).join(':');
rmSync(OUT, { recursive: true, force: true }); mkdirSync(OUT, { recursive: true });
const sources = [...readdirSync(GEN).filter((f) => f.endsWith('.java')).map((f) => join(GEN, f)), join(JAVA, 'RoundTrip.java')];
const javac = spawnSync('javac', ['-Xlint:-options', '--release', '17', '-cp', cp, '-d', OUT, ...sources], { encoding: 'utf8' });
if (javac.status !== 0) { console.error('java_check: javac failed\n' + javac.stdout + javac.stderr); process.exit(1); }
const javaVersion = spawnSync('javac', ['-version'], { encoding: 'utf8' });
const run = spawnSync('java', ['-cp', `${OUT}:${cp}`, 'RoundTrip', FIXTURES], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
process.stdout.write(`java_check: ${(javaVersion.stdout || javaVersion.stderr).trim()}; compiled ${sources.length} sources with --release 17\n${run.stdout}${run.stderr}`);
process.exit(run.status ?? 1);
