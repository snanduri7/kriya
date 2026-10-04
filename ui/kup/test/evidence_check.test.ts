/** kup-evidence-check (tools/evidence_check.mjs): structural consistency of explicitly supplied KUP records. Every
 * diagnostic carries a registered rule, a classification, file + pointer (resolvable, except marked absent fields),
 * the observed value, related pointers, an explanation and its evidence basis. Fixtures: a clean consistent set, a
 * dense conflicting record, duplicates/ambiguity, progression across snapshots, a partial export, unknown fields,
 * malformed/unsupported inputs, plus the serializer-generated fixture. Nothing here is a gate verdict. */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
// @ts-expect-error - plain ESM tool, no declarations by design (no build step)
import { CLASSIFICATIONS, DETECTS, EXIT_CODES, LABEL, RULES, UNSUPPORTED_CHECKS, buildCheck, main, metadataDiffers, parseArgs, renderMarkdown, resolvePointer, stableStringify } from '../tools/evidence_check.mjs';

const FX = join(__dirname, 'fixtures', 'evidence_check');
const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');
const TOOL = join(__dirname, '..', 'tools', 'evidence_check.mjs');
const S0 = '20261004T083000000000Z-f1c70000', S1 = '20261004T093000000000Z-f1c70001', S2 = '20261004T103000000000Z-f1c70002', S9 = '20261003T000000000000Z-f1c70009';
const input = (dir: string, name: string) => ({ file: name, text: readFileSync(join(dir, name), 'utf8') });
const fx = (...names: string[]) => names.map((n) => input(FX, n));
const CLEAN = ['clean.detail.json', 'clean.list.json', 'clean.snapshots.json', 'clean.verify.json', 'clean.acquire.json', 'clean.prune.json', 'clean.workspace.json', 'clean.capabilities.json', 'clean.error.json'];
/** A variant of a fixture built in memory (the committed fixtures stay small). */
const variant = (name: string, file: string, mutate: (doc: any) => void) => { const doc = JSON.parse(readFileSync(join(FX, name), 'utf8')); mutate(doc); return { file, text: JSON.stringify(doc) }; };

type Observed = { kind: string; value?: unknown; detail?: string };
type Related = { file: string; pointer: string; observed: Observed };
type Diagnostic = { rule_id: string; classification: string; detects: string; file: string; pointer: string; observed: Observed; related: Related[]; explanation: string; basis: string[] };
type Rule = { title: string; classification: string; detects: string; basis: string[]; applies_to: string; requires: string };
interface Report {
  summary: { exit_code: number; actionable: number; diagnostics: number; checked: number; inputs_not_checked: { file: string; status: string }[]; by_rule: Record<string, number>; by_classification: Record<string, number> };
  coverage: { inputs: { file: string; status: string; unknown_fields: number; diagnostics: number; snapshot_id: string | null; run_id: string | null; sections: Record<string, string | null> | null }[]; snapshots: { snapshot_id: string; resolution: string; listed_in: string[]; referenced_by: string[] }[]; runs: { run_id: string; snapshot_ids: (string | null)[]; records: number }[] };
  diagnostics: Diagnostic[]; rules: Record<string, Rule>; unsupported_checks: string[];
}
const check = (...inputs: { file: string; text: string }[]) => buildCheck(inputs) as Report;
const checkFx = (...names: string[]) => check(...fx(...names));
const ruleIds = (r: Report) => [...new Set(r.diagnostics.map((d) => d.rule_id))].sort();
const only = (r: Report, id: string) => r.diagnostics.filter((d) => d.rule_id === id);
const one = (r: Report, id: string) => { const xs = only(r, id); expect(xs, id).toHaveLength(1); return xs[0]!; };
const unescapedPipes = (line: string) => line.split(/(?<!\\)\|/).length - 1;

describe('rule registry and vocabulary', () => {
  it('every rule has a stable id, a classification, what it detects, a non-empty evidence basis, applicability and required inputs', () => {
    const ids = Object.keys(RULES as Record<string, Rule>);
    expect(ids.length).toBeGreaterThanOrEqual(30);
    for (const id of ids) {
      const r = (RULES as Record<string, Rule>)[id]!;
      expect(id).toMatch(/^EVC-[A-Z]+-\d{3}$/);
      expect(CLASSIFICATIONS).toContain(r.classification);
      expect(DETECTS).toContain(r.detects);
      expect(r.basis.length).toBeGreaterThan(0); for (const b of r.basis) expect(b).toMatch(/kriya\/|ui\/kup\/schema|serializer|compatibility/);
      expect(r.title.length).toBeGreaterThan(10); expect(r.applies_to.length).toBeGreaterThan(0); expect(r.requires.length).toBeGreaterThan(0);
    }
    expect(CLASSIFICATIONS).toEqual(['structural_error', 'consistency_conflict', 'unresolved_reference', 'ambiguity', 'informational']);
    expect(EXIT_CODES).toEqual({ no_actionable_diagnostics: 0, diagnostics: 1, usage_or_output_failure: 2 });
    expect(LABEL).toContain('Not a workflow gate verdict');
  });
  it('checks without a documented invariant are listed as not checked, never implemented', () => {
    const text = (UNSUPPORTED_CHECKS as string[]).join('\n');
    for (const needle of ['cursor is opaque', 'gate_outcomes', 'to_gate_outcome', 'EvidenceRecord.to_dict carries no identifier', 'prompt_rendered', 'never inferred']) expect(text).toContain(needle);
    expect(Object.keys(RULES)).not.toContain('EVC-GATE-001');
  });
  it('metadata_differs is ported exactly: null when a side is missing or carries an error, else any of the six keys differ', () => {
    const m = { size: 1, mtime_ns: 2, inode: 3, wal_size: null, shm_size: null, journal_size: null };
    expect(metadataDiffers(m, { ...m })).toBe(false);
    expect(metadataDiffers(m, { ...m, wal_size: 4096 })).toBe(true);
    expect(metadataDiffers(null, m)).toBeNull(); expect(metadataDiffers(m, { ...m, error: 'PermissionError' })).toBeNull();
    expect(metadataDiffers({ size: 1, mtime_ns: 2, inode: 3 }, { ...m })).toBe(false); // missing keys read as null, like dict.get
  });
});

describe('a clean, consistent set', () => {
  const report = checkFx(...CLEAN);
  it('yields no diagnostic, exit code 0, every input checked (an error envelope included), snapshot scope resolved', () => {
    expect(report.diagnostics).toEqual([]);
    expect(report.summary).toMatchObject({ exit_code: 0, actionable: 0, diagnostics: 0, checked: 9, inputs_not_checked: [] });
    expect(report.coverage.inputs.map((i) => i.status)).toEqual([...Array(8).fill('checked'), 'kup_error']);
    expect(report.coverage.inputs[0]).toMatchObject({ snapshot_id: S1, run_id: 'r-clean', sections: { run_events: 'recorded', attribution: 'not_recorded', output: 'not_recorded' } });
    expect(report.coverage.snapshots.map((s) => [s.snapshot_id, s.resolution])).toEqual([[S0, 'not_referenced'], [S1, 'listed']]); // the error envelope's S9 is not a reference
    expect(report.coverage.runs.map((r) => r.run_id)).toEqual(['r-clean', 'r-null-ts', 'r-other']);
  });
  it('is deterministic: the same inputs give byte-identical JSON and Markdown, with no clock field', () => {
    const again = checkFx(...CLEAN);
    expect(stableStringify(again)).toBe(stableStringify(report));
    expect(renderMarkdown(again)).toBe(renderMarkdown(report));
    expect(stableStringify(report)).not.toMatch(/generated_at|checked_at|"now"/);
  });
});

describe('one dense conflicting record (identity, kind, metadata, sections, events, tokens, references, escaping)', () => {
  const report = checkFx('conflicts.detail.json', 'clean.snapshots.json');
  const F = 'conflicts.detail.json';
  it('emits exactly the expected rules, each anchored at the right pointer with the observed and related values', () => {
    expect(ruleIds(report)).toEqual(['EVC-AMB-002', 'EVC-EVT-001', 'EVC-EVT-002', 'EVC-EVT-003', 'EVC-ID-001', 'EVC-ID-002', 'EVC-ID-004', 'EVC-ID-006', 'EVC-REF-001', 'EVC-SEC-001', 'EVC-SEC-002', 'EVC-TOK-001', 'EVC-UNK-001']);
    expect(one(report, 'EVC-ID-001')).toMatchObject({ classification: 'consistency_conflict', detects: 'contradiction', file: F, pointer: '/source/snapshot_id', observed: { kind: 'value', value: S1 }, related: [{ file: F, pointer: '/consistency/snapshot_id', observed: { kind: 'value', value: S0 } }] });
    expect(one(report, 'EVC-ID-002')).toMatchObject({ pointer: '/source/snapshot_directory', related: [{ pointer: '/source/snapshot_id' }] });
    expect(one(report, 'EVC-ID-004')).toMatchObject({ pointer: '/consistency/kind', observed: { value: 'live_observation' }, related: [{ pointer: '/operation', observed: { value: 'history.detail' } }] });
    const meta = one(report, 'EVC-ID-006');
    expect(meta).toMatchObject({ pointer: '/consistency/source_metadata_changed', observed: { kind: 'value', value: false } });
    expect(meta.explanation).toContain('gives true'); expect(meta.related.map((r) => r.pointer)).toEqual(['/consistency/source_metadata_at_acquisition', '/consistency/source_metadata_now']);
    expect(one(report, 'EVC-SEC-001')).toMatchObject({ classification: 'structural_error', pointer: '/data/model_hops/data', observed: { kind: 'value', value: [] }, related: [{ pointer: '/data/model_hops/availability', observed: { value: 'not_recorded' } }] });
    expect(only(report, 'EVC-SEC-002').map((d) => d.pointer)).toEqual(['/data/fields/gate_outcomes/availability', '/data/fields/model_hops/availability', '/data/fields/run_events/data']);
    expect(only(report, 'EVC-SEC-002')[2]!.observed).toEqual({ kind: 'value', value: { omitted_text: true, length: 4473 } }); // stored text is not copied
    expect(one(report, 'EVC-EVT-003')).toMatchObject({ classification: 'informational', pointer: '/data/run_events/data/0/authority', observed: { value: 'oracle | <script>alert(1)</script> `tick`\nline2' } });
    expect(one(report, 'EVC-EVT-002')).toMatchObject({ pointer: '/data/run_events/data/2/created_at', observed: { value: 1e300 } });
    expect(only(report, 'EVC-EVT-001').map((d) => [d.pointer, d.observed])).toEqual([['/data/run_events/data/4/created_at', { kind: 'absent' }], ['/data/run_events/data/4/kind', { kind: 'absent' }]]);
    expect(only(report, 'EVC-TOK-001').map((d) => [d.pointer, d.observed.value])).toEqual([['/data/run_events/data/1/details/prefill_seconds', 'fast'], ['/data/run_events/data/1/details/t1_tokens', -5]]);
    expect(one(report, 'EVC-AMB-002')).toMatchObject({ classification: 'ambiguity', pointer: '/data/run_events/data/0/details/tiers/0/path', observed: { value: 'src/mod1/a.py' }, related: [{ pointer: '/data/run_events/data/0/details/tiers/2/path' }] });
    const ref = one(report, 'EVC-REF-001');
    expect(ref).toMatchObject({ classification: 'unresolved_reference', detects: 'incomplete_coverage', pointer: '/data/attribution/data/evidence_ids', observed: { value: ['ev-1', 'ev-2'] }, related: [{ pointer: '/data/evidence_records/availability', observed: { value: 'not_recorded' } }] });
    expect(ref.explanation).toContain('cannot resolve from supplied inputs'); expect(ref.explanation).toContain('EvidenceRecord.to_dict');
    expect(ref.basis.join(' ')).toContain('kriya/workflow/evidence.py');
  });
  it('unknown fields are informational and uninterpreted: mystery_tokens is never a token-domain error; the run row keeps its novel field', () => {
    const unk = only(report, 'EVC-UNK-001');
    expect(unk.map((d) => [d.pointer, d.observed.value])).toEqual([['/data/run_events/data/1/details/mystery_tokens', 9], ['/data/run/novel_run_field', 'kept | <b>x</b>\n`y`']]);
    expect(unk.every((d) => d.classification === 'informational')).toBe(true);
    expect(only(report, 'EVC-TOK-001').some((d) => d.pointer.endsWith('mystery_tokens'))).toBe(false);
    expect(report.coverage.inputs[0]!.unknown_fields).toBe(2);
  });
  it('the summary counts actionable diagnostics and sets exit code 1; the snapshot stays resolvable despite the identity conflict', () => {
    expect(report.summary.exit_code).toBe(1);
    expect(report.summary.actionable).toBe(report.diagnostics.filter((d) => d.classification !== 'informational').length);
    expect(report.coverage.snapshots.find((s) => s.snapshot_id === S1)!.resolution).toBe('listed');
  });
  it('the Markdown escapes pipes, newlines, markup and backticks inside cells and keeps every table row at its column count', () => {
    const md = renderMarkdown(report);
    // observed strings are shown JSON-quoted (a recorded newline is the JSON escape \n), then cell-escaped (its backslash doubled)
    expect(md).toContain('| "oracle \\| \\<script\\>alert(1)\\</script\\> \\`tick\\`\\\\nline2" |'); // the observed authority, escaped inside its cell
    expect(md).toContain('| "kept \\| \\<b\\>x\\</b\\>\\\\n\\`y\\`" |'); // the observed unknown run field
    expect(md).not.toContain('<script>');
    const rows = md.split('\n').filter((l: string) => l.startsWith('| EVC-'));
    expect(rows.length).toBeGreaterThan(20); // diagnostics (8 columns) plus the registry (7 columns)
    for (const row of rows) expect([7, 8], row.slice(0, 80)).toContain(unescapedPipes(row) - 1);
    expect(md).toContain('## Not checked (no documented invariant; never strengthened silently)');
    expect(md).toContain('never workflow gate verdicts');
  });
});

describe('absent fields, explicit null and unavailable sections are kept distinct', () => {
  it('a recorded section with null data is informational (observed null); a null status against a recorded one is a conflict with observed null; an absent field is marked absent', () => {
    const nullData = variant('clean.detail.json', 'null-data.json', (d) => { d.data.attribution = { availability: 'recorded', provenance: 'x', reason: null, data: null }; });
    const nullStatus = variant('clean.detail.json', 'null-status.json', (d) => { d.data.run.status = null; });
    const r = check(nullData, nullStatus, input(FX, 'clean.list.json'));
    expect(one(r, 'EVC-SEC-003')).toMatchObject({ classification: 'informational', file: 'null-data.json', pointer: '/data/attribution/data', observed: { kind: 'null' } });
    const conf = only(r, 'EVC-CONF-001');
    expect(conf.map((d) => [d.file, d.pointer, d.observed])).toEqual([['null-data.json', '/data/run/status', { kind: 'value', value: 'SUCCESS' }]]);
    expect(conf[0]!.related.map((x) => [x.file, x.observed])).toEqual([['null-status.json', { kind: 'null' }]]); // the list row agrees with null-data, only null-status differs
    const sans = variant('clean.detail.json', 'sans-id.json', (d) => { delete d.consistency.snapshot_id; });
    expect(one(check(sans), 'EVC-ID-005')).toMatchObject({ classification: 'informational', pointer: '/consistency/snapshot_id', observed: { kind: 'absent' } });
  });
});

describe('duplicates, ambiguity and order', () => {
  it('a repeated snapshot_id in one listing is a conflict, and the differing manifest fact is reported beside it', () => {
    const r = checkFx('dup.snapshots.json');
    expect(ruleIds(r)).toEqual(['EVC-CONF-002', 'EVC-DUP-001']);
    expect(one(r, 'EVC-DUP-001')).toMatchObject({ pointer: '/data/snapshots/0/snapshot_id', observed: { value: S1 }, related: [{ pointer: '/data/snapshots/2/snapshot_id' }] });
    expect(one(r, 'EVC-CONF-002')).toMatchObject({ pointer: '/data/snapshots/0/rows', observed: { value: 120 }, related: [{ pointer: '/data/snapshots/2/rows', observed: { value: 999 } }] });
  });
  it('a repeated run_id in one page is a conflict (primary key), its differing columns are conflicts, and a page out of serializer order is informational', () => {
    const r = checkFx('dup.list.json');
    expect(ruleIds(r)).toEqual(['EVC-CONF-001', 'EVC-DUP-002', 'EVC-ORD-001', 'EVC-REF-003']); // REF-003: no listing supplied with this one page
    expect(one(r, 'EVC-DUP-002')).toMatchObject({ classification: 'consistency_conflict', pointer: '/data/runs/0/run_id', related: [{ pointer: '/data/runs/1/run_id' }] });
    expect(only(r, 'EVC-CONF-001').map((d) => d.pointer)).toEqual(['/data/runs/0/failure_category', '/data/runs/0/status']);
    expect(one(r, 'EVC-ORD-001')).toMatchObject({ classification: 'informational', pointer: '/data/runs/2/run_id', related: [{ pointer: '/data/runs/1/run_id' }] });
  });
  it('a snapshot both removed and kept by one prune is a conflict', () => {
    expect(one(checkFx('dup.prune.json'), 'EVC-DUP-003')).toMatchObject({ pointer: '/data/removed/0', observed: { value: S0 }, related: [{ pointer: '/data/kept/1', observed: { value: S0 } }] });
  });
  it('the same run_id in records without a snapshot scope is an ambiguity, never a conflict; a repeated path or event is never flagged as a duplicate', () => {
    const r = checkFx('scopeless-a.list.json', 'scopeless-b.list.json');
    expect(ruleIds(r)).toEqual(['EVC-AMB-001', 'EVC-ID-005']);
    const amb = one(r, 'EVC-AMB-001');
    expect(amb).toMatchObject({ classification: 'ambiguity', file: 'scopeless-a.list.json', pointer: '/data/runs/0/run_id', related: [{ file: 'scopeless-b.list.json', pointer: '/data/runs/0/run_id' }] });
    expect(amb.explanation).toContain('cannot be resolved from the supplied inputs');
    expect(only(r, 'EVC-CONF-001')).toEqual([]);
    const twoEvents = variant('clean.detail.json', 'two-events.json', (d) => { d.data.run_events.data.push({ ...d.data.run_events.data[0] }); d.data.fields.run_events.data = JSON.stringify(d.data.run_events.data); });
    expect(ruleIds(check(twoEvents))).toEqual(['EVC-REF-003']); // identical events carry no id: not a duplicate (REF-003: no listing supplied)
  });
});

describe('two snapshots of one run', () => {
  it('different snapshots with different states are informational only (progression is not a contradiction) and exit 0', () => {
    const r = checkFx('progress-s1.detail.json', 'progress-s2.detail.json', 'clean.snapshots.json');
    expect(ruleIds(r)).toEqual(['EVC-INFO-001', 'EVC-REF-002']); // S2 is not in the supplied listing: an unresolved reference, not a conflict
    const info = one(r, 'EVC-INFO-001');
    expect(info).toMatchObject({ classification: 'informational', file: 'progress-s1.detail.json', pointer: '/data/run/run_id', observed: { value: 'r-prog' }, related: [{ file: 'progress-s2.detail.json', pointer: '/data/run/run_id' }] });
    expect(info.explanation).toContain(S1); expect(info.explanation).toContain(S2); expect(info.explanation).toContain('not a contradiction');
    expect(r.summary.by_classification).toEqual({ informational: 1, unresolved_reference: 1 });
    expect(r.coverage.runs).toEqual([{ run_id: 'r-prog', snapshot_ids: [S1, S2], records: 2, files: ['progress-s1.detail.json', 'progress-s2.detail.json'] }]);
    const alone = checkFx('progress-s1.detail.json', 'progress-s2.detail.json');
    expect(ruleIds(alone)).toEqual(['EVC-INFO-001', 'EVC-REF-003']); expect(alone.summary.exit_code).toBe(0); // informational only
  });
  it('the same snapshot recorded twice with different states is a conflict per differing column, and no progression note is added', () => {
    const r = checkFx('progress-s1.detail.json', 'progress-s1-again.detail.json', 'clean.snapshots.json');
    expect(ruleIds(r)).toEqual(['EVC-CONF-001']);
    expect(only(r, 'EVC-CONF-001').map((d) => [d.pointer, d.observed.value, d.related[0]!.observed.kind === 'null' ? null : d.related[0]!.observed.value])).toEqual([['/data/run/attempts', 2, 3], ['/data/run/failure_category', 'quality_gate_failed', null], ['/data/run/status', 'FAILED', 'SUCCESS']]);
    expect(r.summary.exit_code).toBe(1);
  });
});

describe('partial exports and references', () => {
  it('unavailable sections are coverage, not errors; the recorded attribution reference is unresolved; a snapshot missing from the supplied listing is unresolved with the listing related', () => {
    const r = checkFx('partial.detail.json', 'clean.snapshots.json');
    expect(ruleIds(r)).toEqual(['EVC-REF-001', 'EVC-REF-002']);
    expect(r.coverage.inputs[0]!.sections).toMatchObject({ run_events: 'unreadable', evidence_records: 'not_recorded', generation_metrics: 'unsupported', failure_report: 'excluded', attribution: 'recorded' });
    const ref = one(r, 'EVC-REF-002');
    expect(ref).toMatchObject({ classification: 'unresolved_reference', file: 'partial.detail.json', pointer: '/source/snapshot_id', observed: { value: S9 }, related: [{ file: 'clean.snapshots.json', pointer: '/data/snapshots' }] });
    expect(ref.explanation).toContain('not proof that Kriya lost evidence');
    expect(r.coverage.snapshots.find((s) => s.snapshot_id === S9)).toMatchObject({ resolution: 'not_listed', referenced_by: ['partial.detail.json'], listed_in: [] });
    expect(r.summary.exit_code).toBe(1);
  });
  it('without a listing of its directory the snapshot id is "cannot resolve from supplied inputs" (informational), not unresolved', () => {
    const r = checkFx('partial.detail.json');
    expect(ruleIds(r)).toEqual(['EVC-REF-001', 'EVC-REF-003']);
    const ref = one(r, 'EVC-REF-003');
    expect(ref).toMatchObject({ classification: 'informational', pointer: '/source/snapshot_id' });
    expect(ref.explanation).toContain('cannot resolve from supplied inputs');
    expect(r.coverage.snapshots.find((s) => s.snapshot_id === S9)!.resolution).toBe('no_listing_supplied');
  });
});

describe('malformed and unsupported inputs', () => {
  it('each malformed input gets its own structural diagnostic and stays visible in coverage while the other files are still checked', () => {
    const r = checkFx('malformed.json', 'schema2.json', 'not-envelope.json', 'bad-payload.json', 'error-with-data.json', ...CLEAN);
    expect(r.summary).toMatchObject({ checked: 10, exit_code: 1 }); // the nine clean inputs plus the error envelope (fully checked, no payload)
    expect(r.summary.inputs_not_checked).toEqual([{ file: 'malformed.json', status: 'malformed_json' }, { file: 'schema2.json', status: 'unsupported_schema_version' }, { file: 'not-envelope.json', status: 'not_a_kup_envelope' }, { file: 'bad-payload.json', status: 'malformed_payload' }]);
    expect(one(r, 'EVC-ENV-001')).toMatchObject({ classification: 'structural_error', detects: 'malformed_input', file: 'malformed.json', pointer: '', observed: { kind: 'unparsable' } });
    expect(one(r, 'EVC-ENV-002')).toMatchObject({ file: 'schema2.json', pointer: '/schema_version', observed: { value: 2 } });
    expect(only(r, 'EVC-ENV-003').map((d) => [d.pointer, d.observed.kind])).toEqual([['/consistency', 'absent'], ['/data', 'absent'], ['/error', 'absent'], ['/observed_at', 'absent'], ['/request_id', 'absent'], ['/source', 'absent']]);
    expect(one(r, 'EVC-ENV-004')).toMatchObject({ file: 'bad-payload.json', pointer: '/data/runs', observed: { value: 'not a list' } });
    expect(one(r, 'EVC-ENV-005')).toMatchObject({ classification: 'structural_error', file: 'error-with-data.json', pointer: '/data', related: [{ pointer: '/error/code', observed: { value: 'STORE_BUSY' } }] });
    expect(only(r, 'EVC-ENV-006').map((d) => [d.classification, d.pointer])).toEqual([['informational', '/consistency'], ['informational', '/source']]);
    expect(r.diagnostics.filter((d) => CLEAN.includes(d.file))).toEqual([]);
  });
  it('a non-object JSON value is malformed, not an envelope', () => {
    const r = check({ file: 'array.json', text: '[1,2]' }, { file: 'string.json', text: '"x"' });
    expect(only(r, 'EVC-ENV-001').map((d) => d.explanation)).toEqual(['top-level JSON value is array, not an object', 'top-level JSON value is string, not an object']);
  });
});

describe('unknown fields and the generated fixtures', () => {
  it('unknown fields are preserved: every pointer resolves to the untouched value, nothing becomes a structural error, and the evidence_id linkage of the fixtures is reported as unresolved, not resolved', () => {
    const name = 'history.detail.run-unknown-fields.json';
    const doc = JSON.parse(readFileSync(join(GEN, name), 'utf8'));
    const r = check(input(GEN, name));
    expect(ruleIds(r)).toEqual(['EVC-REF-001', 'EVC-REF-003', 'EVC-UNK-001']);
    expect(r.coverage.inputs[0]!.unknown_fields).toBe(121);
    for (const d of only(r, 'EVC-UNK-001')) for (const p of [d.pointer, ...d.related.map((x) => x.pointer)]) expect(resolvePointer(doc, p).found, p).toBe(true);
    expect(only(r, 'EVC-UNK-001').find((d) => d.pointer.endsWith('severity_v9'))!.observed).toEqual({ kind: 'value', value: 'novel' });
    expect(one(r, 'EVC-REF-001').explanation).toContain('a field named evidence_id in a fixture is not a documented target');
  });
  it('the serializer-produced run record, listing, verify, acquire and page validate without any identity, section, event or token diagnostic; the fixture workspace status trips the documented exit-code rule', () => {
    const r = check(...['history.detail.run-serializer-events.json', 'snapshot.list.json', 'snapshot.verify.json', 'snapshot.acquire.json', 'history.list.page1.json', 'workspace.status.json', 'capabilities.json'].map((n) => input(GEN, n)));
    expect(ruleIds(r)).toEqual(['EVC-ID-007', 'EVC-REF-001']);
    expect(one(r, 'EVC-ID-007')).toMatchObject({ file: 'workspace.status.json', pointer: '/data/exit_code', observed: { value: 0 }, related: [{ pointer: '/data/status', observed: { value: 'NO_RECOVERY_REQUIRED' } }] });
    expect(r.coverage.snapshots.find((s) => s.snapshot_id === S1)).toMatchObject({ resolution: 'listed', listed_in: ['snapshot.list.json'] });
  });
});

describe('every emitted pointer resolves against its input (except marked absent fields)', () => {
  it('holds across all fixtures, including related pointers into other files', () => {
    const names = ['conflicts.detail.json', 'dup.snapshots.json', 'dup.list.json', 'dup.prune.json', 'scopeless-a.list.json', 'scopeless-b.list.json', 'progress-s1.detail.json', 'progress-s2.detail.json', 'progress-s1-again.detail.json', 'partial.detail.json', 'malformed.json', 'schema2.json', 'not-envelope.json', 'bad-payload.json', 'error-with-data.json', ...CLEAN];
    const r = checkFx(...names);
    const docs = new Map<string, unknown>();
    for (const n of names) { try { docs.set(n, JSON.parse(readFileSync(join(FX, n), 'utf8'))); } catch { /* malformed.json */ } }
    expect(r.diagnostics.length).toBeGreaterThan(40);
    const seen = new Set<string>();
    for (const d of r.diagnostics) {
      seen.add(d.rule_id);
      expect(Object.keys(RULES)).toContain(d.rule_id);
      for (const x of [{ file: d.file, pointer: d.pointer, observed: d.observed }, ...d.related]) {
        if (x.observed.kind === 'unparsable') { expect(x.pointer).toBe(''); expect(docs.has(x.file)).toBe(false); continue; }
        const res = resolvePointer(docs.get(x.file), x.pointer);
        if (x.observed.kind === 'absent') expect(res.found, `${x.file}#${x.pointer}`).toBe(false);
        else { expect(res.found, `${x.file}#${x.pointer}`).toBe(true); if (x.observed.kind === 'null') expect(res.value).toBeNull(); else if (typeof x.observed.value !== 'object') expect(res.value).toEqual(x.observed.value); }
      }
    }
    expect([...seen].sort()).toEqual(['EVC-AMB-001', 'EVC-AMB-002', 'EVC-CONF-001', 'EVC-CONF-002', 'EVC-DUP-001', 'EVC-DUP-002', 'EVC-DUP-003', 'EVC-ENV-001', 'EVC-ENV-002', 'EVC-ENV-003', 'EVC-ENV-004', 'EVC-ENV-005', 'EVC-ENV-006', 'EVC-EVT-001', 'EVC-EVT-002', 'EVC-EVT-003', 'EVC-ID-001', 'EVC-ID-002', 'EVC-ID-004', 'EVC-ID-005', 'EVC-ID-006', 'EVC-INFO-001', 'EVC-ORD-001', 'EVC-REF-001', 'EVC-REF-002', 'EVC-SEC-001', 'EVC-SEC-002', 'EVC-TOK-001', 'EVC-UNK-001']);
  });
  it('the remaining registered rules fire on in-memory variants (verify digest, acquire id, workspace run_active, capabilities version, recorded-null data, cannot-resolve scope)', () => {
    const r = check(
      variant('clean.verify.json', 'v.json', (d) => { d.data.sha256 = 'ABC'; d.data.snapshot_id = S0; }),
      variant('clean.acquire.json', 'a.json', (d) => { d.data.pruned = [d.data.snapshot_id]; }),
      variant('clean.workspace.json', 'w.json', (d) => { d.data.run_active = true; }),
      variant('clean.capabilities.json', 'c.json', (d) => { d.data.kup_versions = [2]; }),
      variant('clean.detail.json', 'd.json', (d) => { d.data.model_hops.data = null; }),
    );
    expect(ruleIds(r)).toEqual(['EVC-DUP-003', 'EVC-ID-003', 'EVC-ID-007', 'EVC-ID-008', 'EVC-REF-003', 'EVC-SEC-002', 'EVC-SEC-003', 'EVC-VER-001']);
    expect(one(r, 'EVC-VER-001')).toMatchObject({ pointer: '/data/sha256', observed: { value: 'ABC' } });
    expect(one(r, 'EVC-ID-003')).toMatchObject({ pointer: '/data/snapshot_id', observed: { value: S0 }, related: [{ pointer: '/source/snapshot_id', observed: { value: S1 } }] });
    expect(one(r, 'EVC-DUP-003')).toMatchObject({ file: 'a.json', pointer: '/data/pruned/0' });
    expect(one(r, 'EVC-ID-007')).toMatchObject({ file: 'w.json', pointer: '/data/run_active', observed: { value: true } });
    expect(one(r, 'EVC-ID-008')).toMatchObject({ pointer: '/data/kup_versions', observed: { value: [2] }, related: [{ pointer: '/schema_version', observed: { value: 1 } }] });
    expect(one(r, 'EVC-SEC-003')).toMatchObject({ pointer: '/data/model_hops/data', observed: { kind: 'null' } });
    const all = new Set([...Object.keys(RULES)]);
    for (const id of ['EVC-DUP-003', 'EVC-ID-003', 'EVC-ID-007', 'EVC-ID-008', 'EVC-REF-003', 'EVC-SEC-003', 'EVC-VER-001']) expect(all.has(id)).toBe(true);
  });
});

describe('CLI through a subprocess: exit codes, outputs, overwrite refusal, never onto an input', () => {
  const run = (args: string[]) => { try { return { code: 0, out: execFileSync(process.execPath, [TOOL, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }), err: '' }; } catch (e) { const x = e as { status: number; stdout: string; stderr: string }; return { code: x.status, out: String(x.stdout), err: String(x.stderr) }; } };
  it('exit 0 for a clean set, exit 1 with both outputs for diagnostics, exit 2 for usage and refusals', () => {
    const out = mkdtempSync(join(tmpdir(), 'evc-'));
    const clean = run(['--out', join(out, 'clean'), ...CLEAN.map((n) => join(FX, n))]);
    expect(clean.code).toBe(0); expect(clean.out).toContain('0 diagnostic(s), 0 actionable');
    expect(existsSync(join(out, 'clean', 'evidence-check.json'))).toBe(true); expect(existsSync(join(out, 'clean', 'evidence-check.md'))).toBe(true);
    const conf = run(['--out', join(out, 'conf'), join(FX, 'conflicts.detail.json')]);
    expect(conf.code).toBe(1); expect(conf.out).toMatch(/\d+ actionable \(/);
    const json = JSON.parse(readFileSync(join(out, 'conf', 'evidence-check.json'), 'utf8')) as Report;
    expect(json.summary.exit_code).toBe(1); expect(json.diagnostics.length).toBeGreaterThan(10);
    expect(readFileSync(join(out, 'conf', 'evidence-check.md'), 'utf8')).toContain('# kup-evidence-check');
    const partial = run(['--out', join(out, 'partial'), join(FX, 'malformed.json'), join(FX, 'clean.detail.json')]);
    expect(partial.code).toBe(1); expect(partial.out).toContain('1 not checked');
    expect(run(['--out', join(out, 'conf'), join(FX, 'conflicts.detail.json')]).code).toBe(2); // existing outputs
    expect(run(['--out', join(out, 'conf'), '--overwrite', join(FX, 'conflicts.detail.json')]).code).toBe(1);
    const noOut = run([join(FX, 'clean.detail.json')]); expect(noOut.code).toBe(2); expect(noOut.err).toContain('--out');
    expect(run(['--out', join(out, 'x')]).code).toBe(2);
    expect(run(['--out', join(out, 'x'), join(FX, 'does-not-exist.json')]).code).toBe(2);
    expect(run(['--out', join(out, 'x'), FX]).code).toBe(2); // a directory is never read
    writeFileSync(join(out, 'evidence-check.json'), readFileSync(join(FX, 'clean.detail.json')));
    const onto = run(['--out', out, '--overwrite', join(out, 'evidence-check.json')]);
    expect(onto.code).toBe(2); expect(onto.err).toContain('would overwrite the input');
    expect(JSON.parse(readFileSync(join(out, 'evidence-check.json'), 'utf8')).operation).toBe('history.detail'); // untouched
  });
  it('parseArgs and main report usage errors without writing', () => {
    expect(parseArgs([])).toEqual({ error: 'an explicit output directory is required: --out <dir>' });
    expect(parseArgs(['--out'])).toEqual({ error: '--out needs a directory' });
    expect(parseArgs(['--out', 'o', '--bogus'])).toEqual({ error: 'unknown option --bogus' });
    expect(parseArgs(['--out', 'o', 'a.json', '--overwrite'])).toEqual({ out: 'o', overwrite: true, files: ['a.json'] });
    expect(main(['--out', 'o'])).toMatchObject({ exitCode: 2 });
  });
});
