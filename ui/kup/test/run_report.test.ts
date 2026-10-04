/** kup-run-report (tools/run_report.mjs): offline, explicit inputs only, every fact sourced, deterministic. Fixtures:
 * the generated KUP fixtures (serializer-produced run_events, unknown fields, unavailable sections, error envelopes)
 * plus small synthetic inputs for malformed and unsupported shapes. */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
// @ts-expect-error - plain ESM tool, no declarations by design (no build step)
import { EVENT_KEYS, LABEL, analyzeFile, buildReport, main, parseArgs, pointer, renderMarkdown, stableStringify } from '../tools/run_report.mjs';

const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');
const TOOL = join(__dirname, '..', 'tools', 'run_report.mjs');
const read = (f: string) => readFileSync(join(GEN, f), 'utf8');
const input = (f: string) => ({ file: f, text: read(f) });
const SERIALIZER = 'history.detail.run-serializer-events.json';
type Fact = { value: unknown; source: { file: string; pointer: string } };
// The tool is plain ESM without declarations, so its results are typed here for the assertions (an intersection with
// its `any` return type would collapse to `any`).
interface Report { tool: string; label: string; runs: Record<string, any>[]; diagnostics: { severity: string; file: string; pointer: string; message: string }[]; inputs: Record<string, any>[]; errors: Record<string, any>[]; run_summaries: Record<string, any>[] }

const walkFacts = (x: unknown, out: Fact[] = []): Fact[] => {
  if (Array.isArray(x)) x.forEach((v) => walkFacts(v, out));
  else if (typeof x === 'object' && x !== null) {
    const o = x as Record<string, unknown>;
    if ('value' in o && 'source' in o && typeof (o.source as Record<string, unknown>)?.pointer === 'string') out.push(o as Fact);
    else Object.values(o).forEach((v) => walkFacts(v, out));
  }
  return out;
};

describe('JSON pointers and stable output', () => {
  it('escapes pointer tokens per RFC 6901 and sorts keys recursively', () => {
    expect(pointer('data', 'a/b', 'c~d', 0)).toBe('/data/a~1b/c~0d/0');
    expect(pointer()).toBe('');
    expect(stableStringify({ b: [{ z: 1, a: 2 }], a: null }, 0)).toBe('{"a":null,"b":[{"a":2,"z":1}]}');
  });
});

describe('a serializer-produced run record (RunEvent.to_dict through the KUP adapter)', () => {
  const report = buildReport([input(SERIALIZER)]) as Report;
  const run = report.runs[0]!;
  it('reports the literal outcome, attempts, events, context tiers/omissions, token accounting, gates and failures, each with file and pointer', () => {
    expect(report.inputs[0]).toMatchObject({ file: SERIALIZER, operation: 'history.detail', schema_version: 1, status: 'run_detail' });
    expect(run.status_as_recorded).toEqual({ value: 'SUCCESS', source: { file: SERIALIZER, pointer: '/data/run/status' } });
    expect(run.attempts.value).toBe(2);
    expect(run.events.map((e: any) => e.kind.value)).toEqual(['context.known_target_package', 'developer.prompt_composition', 'model.transition', 'model.role_metrics']);
    expect(run.events[0].created_at_utc).toBe('2026-10-04T07:00:00.250Z');
    expect(run.attempts_in_events).toEqual({ '1': 2, '2': 2 });
    // context tiers and omissions come from details.tiers / details.omitted of the context.known_target_package event
    expect(run.context.tiers.map((t: any) => [t.path.value, t.tier.value])).toEqual([['src/mod1/a.py', 'member_exact'], ['src/mod1/b.py', 'skeleton']]);
    expect(run.context.tiers[0].tier.source.pointer).toBe('/data/run_events/data/0/details/tiers/0/tier');
    expect(run.context.omissions[0]).toMatchObject({ path: { value: 'src/big.py' }, reason: { value: 'budget_exhausted', source: { pointer: '/data/run_events/data/0/details/omitted/0/reason' } } });
    // provider-reported vs estimated are never mixed
    const pc = run.token_accounting.prompt_compositions[0];
    expect(pc.provider_reported.prompt_tokens_reported).toEqual({ value: 4190, source: { file: SERIALIZER, pointer: '/data/run_events/data/1/details/prompt_tokens_reported' } });
    expect(Object.keys(pc.estimated_by_kriya)).toContain('skills_tokens');
    expect(Object.keys(pc.estimated_by_kriya)).not.toContain('prompt_tokens_reported');
    expect(Object.keys(pc.estimated_by_kriya).every((k) => k.endsWith('_tokens'))).toBe(true);
    expect(pc.token_counts_note.value).toContain('estimated (len/4) per section');
    expect(run.token_accounting.generation_metrics_as_recorded.source.pointer).toBe('/data/generation_metrics/data');
    // gates and failures
    expect(run.gates.map((g: any) => [g.gate.value, g.passed.value, g.reason_code.value])).toEqual([['compile', false, 'COMPILE_FAILED'], ['compile', true, null], ['tests', true, null]]);
    expect(run.gates[0].raw.source.pointer).toBe('/data/gate_outcomes/data/0');
    expect(run.failures.failure_category.value).toBeNull();
    expect(run.failures.events_with_failure_type).toEqual([]);
    // every fact anywhere in the run names this file and a pointer
    const facts = walkFacts(run);
    expect(facts.length).toBeGreaterThan(40);
    for (const f of facts) { expect(f.source.file).toBe(SERIALIZER); expect(f.source.pointer.startsWith('/')).toBe(true); }
    expect(report.diagnostics).toEqual([]);
  });
  it('the Markdown carries the historical label, the literal status wording, the sources and the provider/estimate separation', () => {
    const md = renderMarkdown(report);
    expect(md).toContain(LABEL);
    expect(md).toContain('status (literal; not mapped to success or failure) | SUCCESS |');
    expect(md).toContain(`\`${SERIALIZER}#/data/run_events/data/0/details/tiers/0/tier\``);
    expect(md).toContain('| provider-reported | prompt_tokens_reported | 4190 |');
    expect(md).toContain('| estimated (Kriya) | skills_tokens |');
    expect(md).toContain('not current state');
    expect(md).not.toMatch(/\b(root cause|caused by|because|therefore|succeeded|is current)\b/i); // observations only, no causal or status inference
  });
  it('is deterministic: the same inputs give byte-identical JSON and Markdown', () => {
    const a = buildReport([input(SERIALIZER), input('history.list.page1.json')]);
    const b = buildReport([input(SERIALIZER), input('history.list.page1.json')]);
    expect(stableStringify(a)).toBe(stableStringify(b));
    expect(renderMarkdown(a as Report)).toBe(renderMarkdown(b as Report));
    expect(stableStringify(a)).not.toMatch(/"generated_at"|new Date|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"(?!,)/); // no clock anywhere
  });
});

describe('missing, unknown and malformed inputs', () => {
  it('unavailable sections stay "not recorded" with their reason; unknown fields and sections are preserved and listed', () => {
    const r = buildReport([input('history.detail.run-avail-not_recorded.json'), input('history.detail.run-unknown-fields.json')]) as Report;
    const missing = r.runs[0]!;
    expect(missing.sections.run_events.availability.value).toBe('not_recorded');
    expect(missing.events).toBeNull(); expect(missing.gates).toBeNull(); expect(missing.attempts_in_events).toBeNull();
    expect(renderMarkdown(r)).toContain('Run events: not recorded, so no per-attempt event counts.');
    const unknown = r.runs[1]!;
    expect(unknown.unknown_sections.map((f: Fact) => f.source.pointer)).toEqual(['/data/novel_top_level_section']);
    expect(unknown.events[0].unknown_fields.map((f: Fact) => f.source.pointer.split('/').pop())).toEqual(expect.arrayContaining(['severity_v9']));
    expect(unknown.status_as_recorded.value).toBe('PARTIALLY_SETTLED_v9'); // literal, never mapped
    expect(renderMarkdown(r)).toContain('PARTIALLY_SETTLED_v9');
  });
  it('a KUP error envelope is reported typed; a non-run operation is listed only; a history.list gives summaries', () => {
    const r = buildReport([input('errors/STORE_BUSY.json'), input('capabilities.json'), input('history.list.page1.json')]) as Report;
    expect(r.inputs.map((i: any) => i.status)).toEqual(['kup_error', 'not_a_run_record', 'run_list']);
    expect(r.errors[0]).toMatchObject({ file: 'errors/STORE_BUSY.json', code: { value: 'STORE_BUSY' }, database_state: { value: 'hot_journal' } });
    expect(r.run_summaries.length).toBe(50);
    expect(r.run_summaries[0]!.run_id.source.pointer).toBe('/data/runs/0/run_id');
    expect(r.diagnostics.some((d) => d.file === 'capabilities.json' && d.severity === 'info')).toBe(true);
  });
  it('malformed inputs get explicit diagnostics and never silently vanish', () => {
    const detail = JSON.parse(read(SERIALIZER));
    detail.data.run_events.data.push({ event: 'legacy.shape', at: 'x' }); // the invented first-draft shape: kind/created_at missing
    const envelope2 = { ...JSON.parse(read('capabilities.json')), schema_version: 2 };
    const r = buildReport([
      { file: 'garbage.json', text: 'not json {' }, { file: 'array.json', text: '[1,2]' }, { file: 'v2.json', text: JSON.stringify(envelope2) },
      { file: 'bad-event.json', text: JSON.stringify(detail) }, { file: 'bad-detail.json', text: JSON.stringify({ ...JSON.parse(read(SERIALIZER)), data: { run: { run_id: 'r-x' } } }) },
      { file: 'not-envelope.json', text: JSON.stringify({ hello: 'world' }) },
    ]) as Report;
    expect(r.inputs.map((i: any) => i.status)).toEqual(['malformed_json', 'malformed_json', 'unsupported_schema_version', 'run_detail', 'malformed_run_detail', 'not_a_kup_envelope']);
    expect(r.diagnostics.find((d) => d.file === 'garbage.json')?.message).toMatch(/not JSON/);
    expect(r.diagnostics.find((d) => d.file === 'array.json')?.message).toMatch(/not an object/);
    expect(r.diagnostics.filter((d) => d.file === 'v2.json').length).toBeGreaterThan(0);
    const bad = r.runs.find((x) => x.run_id.value === 'run-serializer-events' && x.events?.length === 5)!;
    expect(bad.events[4].malformed).toBe(true);
    expect(bad.events[4].kind.value).toBeNull();
    expect(bad.events[4].unknown_fields.map((f: Fact) => f.source.pointer.split('/').pop())).toEqual(['at', 'event']);
    expect(r.diagnostics.some((d) => d.file === 'bad-event.json' && d.pointer === '/data/run_events/data/4' && /kind, created_at required/.test(d.message))).toBe(true);
    const malformedDetail = r.runs.find((x) => x.malformed)!;
    expect(malformedDetail.run_id.value).toBe('r-x');
    const md = renderMarkdown(r);
    expect(md).toContain('**Malformed history.detail payload**');
    expect(md).toContain('| 5 | (malformed, see Diagnostics)');
    expect(EVENT_KEYS).toEqual(['kind', 'attempt', 'source', 'authority', 'message', 'failure_type', 'operation', 'details', 'created_at']);
  });
  it('analyzeFile never throws on hostile text', () => {
    for (const text of ['', 'null', '"str"', '{"schema_version":1}', '{"schema_version":1,"operation":"history.detail","request_id":"r","observed_at":"t","source":null,"consistency":null,"data":null,"error":null}']) expect(() => analyzeFile('x.json', text)).not.toThrow();
  });
});

describe('CLI: explicit output location, no overwrite without --overwrite, inputs never overwritten', () => {
  const tmp = () => mkdtempSync(join(tmpdir(), 'kup-run-report-'));
  it('refuses to run without --out, without inputs, with a directory input, or with an unknown option', () => {
    expect(parseArgs([]).error).toMatch(/--out/);
    expect(parseArgs(['--out', '/tmp/x']).error).toMatch(/at least one/);
    expect(parseArgs(['--bogus', '--out', '/tmp/x', 'a.json']).error).toMatch(/unknown option/);
    expect(main(['--out', tmp(), GEN]).exitCode).toBe(2);
    expect(main(['--out', tmp(), join(GEN, 'does-not-exist.json')]).exitCode).toBe(2);
  });
  it('writes report.md and report.json, refuses to overwrite them unless asked, and refuses an output that is an input', () => {
    const out = join(tmp(), 'report');
    const first = main(['--out', out, join(GEN, SERIALIZER), join(GEN, 'errors', 'SNAPSHOT_CORRUPT.json')]);
    expect(first.exitCode).toBe(0); expect(first.message).toContain('1 run record(s)');
    expect(existsSync(join(out, 'report.md'))).toBe(true);
    const json = JSON.parse(readFileSync(join(out, 'report.json'), 'utf8'));
    expect(json.tool).toBe('kup-run-report'); expect(json.label).toBe(LABEL);
    expect(main(['--out', out, join(GEN, SERIALIZER)]).exitCode).toBe(2);
    expect(main(['--out', out, join(GEN, SERIALIZER)]).message).toMatch(/refusing to overwrite/);
    const before = readFileSync(join(out, 'report.md'), 'utf8');
    expect(main(['--out', out, '--overwrite', join(GEN, SERIALIZER)]).exitCode).toBe(0);
    expect(readFileSync(join(out, 'report.md'), 'utf8')).not.toBe(before); // one input fewer
    // an input that IS an output path is refused before anything is written
    const r = main(['--out', out, '--overwrite', join(out, 'report.json')]);
    expect(r.exitCode).toBe(2); expect(r.message).toMatch(/overwrite the input/);
    // the same inputs twice -> byte-identical files (determinism through the CLI)
    const a = join(tmp(), 'a'), b = join(tmp(), 'b');
    for (const o of [a, b]) expect(main(['--out', o, join(GEN, SERIALIZER), join(GEN, 'history.list.page1.json')]).exitCode).toBe(0);
    expect(readFileSync(join(a, 'report.json'), 'utf8')).toBe(readFileSync(join(b, 'report.json'), 'utf8'));
    expect(readFileSync(join(a, 'report.md'), 'utf8')).toBe(readFileSync(join(b, 'report.md'), 'utf8'));
  });
  it('runs as a process with the documented usage and exit codes', () => {
    const out = join(tmp(), 'cli');
    mkdirSync(out);
    writeFileSync(join(out, 'malformed.json'), '{');
    const ok = execFileSync(process.execPath, [TOOL, '--out', out, join(GEN, SERIALIZER), join(out, 'malformed.json')], { encoding: 'utf8' });
    expect(ok).toContain('1 run record(s)'); expect(ok).toContain('1 diagnostic(s)');
    expect(readFileSync(join(out, 'report.md'), 'utf8')).toContain('| error | ');
    let code = 0; let err = '';
    try { execFileSync(process.execPath, [TOOL, join(GEN, SERIALIZER)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }); } catch (e) { code = (e as { status: number }).status; err = String((e as { stderr: string }).stderr); }
    expect(code).toBe(2); expect(err).toContain('--out <dir>');
  });
});
