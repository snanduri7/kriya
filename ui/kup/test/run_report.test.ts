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

/** A summarized fact (gate output: length, sha256, head) stands for a recorded string it must describe exactly. */
const checkValue = (f: any, actual: unknown) => { if (f.summarized) { expect(typeof actual).toBe('string'); expect((actual as string).length).toBe(f.value.length); expect((actual as string).startsWith(f.value.head)).toBe(true); } else if ('value' in f) expect(f.value, f.source.pointer).toEqual(actual); };
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
    expect(run.status_as_recorded).toEqual({ value: 'success', source: { file: SERIALIZER, pointer: '/data/run/status' } });
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
    // gate records as Kriya's writers record them (fixtures/serializer_gates.py): type, the success boolean, output by pointer
    expect(run.gates.map((g: any) => [g.type.value, g.success.value, g.result])).toEqual([['compile', false, 'failure'], ['compile', true, 'success'], ['test_selection', false, 'failure'], ['test', true, 'success'], ['targeted_test', true, 'success'], ['run_verification', false, 'failure'], ['run_verification', true, 'success'], ['regression_test', true, 'success'], ['goal_spec_compliance', true, 'success'], ['test', false, 'failure']]);
    expect(run.gates[0].record.source.pointer).toBe('/data/gate_outcomes/data/0');
    expect(run.gates[0].output).toMatchObject({ summarized: true, value: { length: 63, head: "src/mod1/a.py:12:5: error: name 'audit' is not defined\n1 error\n" }, source: { pointer: '/data/gate_outcomes/data/0/output' } });
    expect(run.gates[8]).toMatchObject({ status: { value: 'UNAVAILABLE' }, reason_code: { value: 'VERIFIER_REQUEST_REFUSED' } }); // success true beside status UNAVAILABLE: both shown, neither inferred
    expect(run.gates[6]).toMatchObject({ graded_by: { value: 'process_exit' }, deterministic_result: { value: 'PASS' } });
    expect(run.gates.every((g: any) => g.unknown_fields.length === 0 && g.conflicts.length === 0)).toBe(true); // writer-shaped records carry no unknown field
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
    expect(md).toContain('status (literal; not mapped to success or failure) | success |');
    expect(md).toContain(`\`${SERIALIZER}#/data/run_events/data/0/details/tiers/0/tier\``);
    expect(md).toContain('| provider-reported count | prompt_tokens_reported | 4190 |');
    expect(md).toContain('| estimated by Kriya (len/4) | skills_tokens |');
    expect(md).toContain('| provider-reported timing | prefill_seconds | 1.83 |');
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

// ---- quality pass (fixture-only): explicit token mapping, escaping, pointer validity ----------------------------------
// @ts-expect-error - plain ESM tool
import { PROMPT_COMPOSITION_FIELDS, cell } from '../tools/run_report.mjs';

const QA_FILE = 'unknown_token_fields_and_escaping.json';
const qaText = readFileSync(join(__dirname, 'fixtures', 'run_report', QA_FILE), 'utf8');

/** RFC 6901 resolution against the original document; returns { found, value }. */
function resolve(doc: unknown, ptr: string): { found: boolean; value?: unknown } {
  if (ptr === '') return { found: true, value: doc };
  let cur: unknown = doc;
  for (const raw of ptr.split('/').slice(1)) {
    const key = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(cur)) { const i = Number(key); if (!Number.isInteger(i) || i < 0 || i >= cur.length) return { found: false }; cur = cur[i]; }
    else if (typeof cur === 'object' && cur !== null) { if (!(key in (cur as object))) return { found: false }; cur = (cur as Record<string, unknown>)[key]; }
    else return { found: false };
  }
  return { found: true, value: cur };
}
const walkSourced = (x: unknown, out: { value?: unknown; source: { file: string; pointer: string; absent?: boolean } }[] = []) => {
  if (Array.isArray(x)) x.forEach((v) => walkSourced(v, out));
  else if (typeof x === 'object' && x !== null) {
    const o = x as Record<string, unknown>;
    if ('source' in o && typeof (o.source as Record<string, unknown>)?.pointer === 'string' && 'file' in (o.source as object)) out.push(o as never);
    else Object.values(o).forEach((v) => walkSourced(v, out));
  }
  return out;
};

describe('token classification follows kriya/workflow/prompt_composition.py exactly', () => {
  it('the explicit mapping is the documented field set', () => {
    expect([...PROMPT_COMPOSITION_FIELDS.estimated_by_kriya]).toEqual(['t0_member_tokens', 't0_header_tokens', 'sibling_signatures_tokens', 't1_tokens', 't2_tokens', 't3_tokens', 'skills_tokens', 'code_context_tokens', 'prefix_shared_tokens']);
    expect([...PROMPT_COMPOSITION_FIELDS.provider_reported]).toEqual(['prompt_tokens_reported']);
    expect([...PROMPT_COMPOSITION_FIELDS.provider_timing]).toEqual(['prefill_seconds', 'load_seconds']);
    expect([...PROMPT_COMPOSITION_FIELDS.recorded_other]).toEqual(['prefix_break']);
  });
  it('an unknown *_tokens field is reported uninterpreted, never as an estimate or a provider count; missing documented fields stay not recorded', () => {
    const r = buildReport([{ file: QA_FILE, text: qaText }]) as Report;
    const [pc1, pc2] = r.runs[0]!.token_accounting.prompt_compositions;
    expect(Object.keys(pc1.unknown_uninterpreted).sort()).toEqual(['futuristic_count', 'mystery_tokens']);
    expect(Object.keys(pc1.estimated_by_kriya)).not.toContain('mystery_tokens');
    expect(pc1.estimated_by_kriya.prefix_shared_tokens.value).toBeNull(); // recorded as null, not absent
    expect(pc1.estimated_by_kriya.prefix_shared_tokens.source.absent).toBeUndefined();
    expect(pc1.provider_timing.load_seconds.value).toBeNull();
    expect(Object.keys(pc2.estimated_by_kriya)).toEqual(['skills_tokens']); // only what the record has
    expect(pc2.provider_reported.prompt_tokens_reported.value).toBe(81);
    expect(r.diagnostics.some((d) => d.pointer === '/data/run_events/data/2/details' && /lacks documented field/.test(d.message) && /t0_member_tokens/.test(d.message))).toBe(true);
    const md = renderMarkdown(r);
    expect(md).toContain('| unknown field (uninterpreted) | mystery_tokens | 9 |');
    expect(md).not.toMatch(/estimated by Kriya \(len\/4\) \| mystery_tokens/);
    expect(md).toContain('generation_metrics as recorded (keys not interpreted): {"wall_ms":1234,"custom_tokens":7}');
  });
});

describe('Markdown cells and source pointers', () => {
  it('escapes pipes, newlines, tabs, backslashes, markup, backticks and control characters in recorded text', () => {
    expect(cell('a|b')).toBe('a\\|b');
    expect(cell('line\nbreak\r\nagain\ttab')).toBe('line\\nbreak\\nagain\\ttab');
    expect(cell('<b>x</b> `t` *e* [l]')).toBe('\\<b\\>x\\</b\\> \\`t\\` \\*e\\* \\[l\\]');
    expect(cell('back\\slash')).toBe('back\\\\slash');
    expect(cell('bell\u0007 del\u007f')).toBe('bell\\u0007 del\\u007f');
    const md: string = renderMarkdown(buildReport([{ file: QA_FILE, text: qaText }]) as Report);
    expect(md).toContain('| status (literal; not mapped to success or failure) | PART\\|IAL |');
    expect(md).toContain('QA \\| goal with \\<b\\>markup\\</b\\>, \\`tick\\`, line\\nbreak, tab\\t and \\u0007 bell');
    expect(md).toContain('| 1 | 1 | compile\\|failed | false | failure | 45 chars; line one \\| pipe \\`tick\\` \\<b\\>markup\\</b\\>\\nline two | - / - | - / - | - |');
    expect(md).toContain('| 2 | 2 | not recorded (field absent) | not recorded (field absent) | not_recorded | not recorded (field absent) | - / - | - / - | fixture_note, name, passed |'); // legacy record: nothing inferred, fields listed
    // a non-boolean success is shown literally, never read truthily; a disagreeing legacy passed is ambiguous, never resolved
    const doc = JSON.parse(qaText); doc.data.gate_outcomes.data[0].success = 'yes'; doc.data.gate_outcomes.data.push({ attempt: 3, type: 'test', success: true, output: 'ok', passed: false });
    const nb = buildReport([{ file: 'nb.json', text: JSON.stringify(doc) }]) as Report;
    expect(nb.runs[0]!.gates[0]).toMatchObject({ result: 'not_boolean', success: { value: 'yes' } });
    expect(nb.runs[0]!.gates[3]).toMatchObject({ result: 'ambiguous', conflicts: [{ value: false, source: { pointer: '/data/gate_outcomes/data/3/passed' } }] });
    expect(nb.diagnostics.map((d) => d.message)).toEqual(expect.arrayContaining([expect.stringContaining('is not a boolean: shown literally'), expect.stringContaining('conflicting result fields (success and passed): result reported as ambiguous')]));
    expect(renderMarkdown(nb)).toContain('| 4 | 3 | test | true | ambiguous (conflicting: passed=false) |');
    expect(md).toContain('| 1 | 1 | src/odd\\|name.py | A.run | member_exact |');
    expect(md).toContain('(not an object: not-an-object)');
    // every table row has the same number of cells as its header (no row broken by recorded text)
    const tables = md.split('\n\n').filter((b) => b.startsWith('|'));
    for (const t of tables) { const rows = t.split('\n').filter((l) => l.startsWith('|')); const n = (rows[0]!.match(/(?<!\\)\|/g) ?? []).length; for (const row of rows) expect((row.match(/(?<!\\)\|/g) ?? []).length, row).toBe(n); }
    expect(md.split('\n').some((l) => /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(l))).toBe(false);
  });
  it('every fact resolves to the original JSON field with the same value; absent fields are marked and really absent', () => {
    for (const file of [SERIALIZER, 'history.detail.run-incomplete-context.json', 'history.detail.run-unknown-fields.json', 'history.detail.run-avail-unreadable.json', 'history.list.page1.json', 'errors/STORE_BUSY.json']) {
      const text = read(file); const doc = JSON.parse(text);
      const facts = walkSourced(buildReport([{ file, text }]));
      expect(facts.length).toBeGreaterThan(0);
      for (const f of facts) {
        expect(f.source.file).toBe(file);
        const res = resolve(doc, f.source.pointer);
        if (f.source.absent) { expect(res.found, `${f.source.pointer} should be absent`).toBe(false); expect(resolve(doc, f.source.pointer.replace(/\/[^/]*$/, '')).found, `parent of ${f.source.pointer}`).toBe(true); expect(f.value).toBeNull(); }
        else { expect(res.found, f.source.pointer).toBe(true); checkValue(f, res.value); }
      }
    }
    const qa = JSON.parse(qaText);
    const facts = walkSourced(buildReport([{ file: QA_FILE, text: qaText }]));
    for (const f of facts) { const res = resolve(qa, f.source.pointer); if (f.source.absent) expect(res.found).toBe(false); else { expect(res.found, f.source.pointer).toBe(true); checkValue(f, res.value); } }
    const legacy = (buildReport([{ file: QA_FILE, text: qaText }]) as Report).runs[0]!.events[4];
    expect(legacy.record.source.pointer).toBe('/data/run_events/data/4');
    expect(legacy.kind.source).toEqual({ file: QA_FILE, pointer: '/data/run_events/data/4/kind', absent: true });
  });
  it('recorded order is kept and the output is deterministic across runs and input order changes only where supplied order changes', () => {
    const a = buildReport([{ file: QA_FILE, text: qaText }, input(SERIALIZER)]) as Report;
    const b = buildReport([{ file: QA_FILE, text: qaText }, input(SERIALIZER)]) as Report;
    expect(stableStringify(a)).toBe(stableStringify(b));
    expect(a.runs.map((r) => r.run_id.value)).toEqual(['run-qa-tokens', 'run-serializer-events']);
    expect(a.runs[0]!.events.map((e: any) => e.index)).toEqual([0, 1, 2, 3, 4]);
    expect(a.runs[0]!.gates.map((g: any) => g.index)).toEqual([0, 1, 2]);
  });
});
