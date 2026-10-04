/** kup-run-compare (tools/run_compare.mjs): two caller-selected history.detail records, recorded facts only, every
 * value sourced on both sides, explicit-key matching with ambiguity listed, deterministic output, explicit overwrite. */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
// @ts-expect-error - plain ESM tool, no declarations by design
import { LABEL, NUMERIC_UNITS, buildComparison, main, matchByKey, parseArgs, renderMarkdown, stableStringify } from '../tools/run_compare.mjs';

const FX = join(__dirname, 'fixtures', 'run_compare');
const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');
const TOOL = join(__dirname, '..', 'tools', 'run_compare.mjs');
const input = (name: string) => ({ file: `${name}.json`, text: readFileSync(join(FX, `${name}.json`), 'utf8') });
const compare = (l: string, r: string) => { const b = buildComparison(input(l), input(r)); expect(b.ok, JSON.stringify(b.problems)).toBe(true); return b.report as Report; };
type Fact = { value?: unknown; source: { file: string; pointer: string; absent?: boolean } };
type Item = { key: string; outcome: string; left: Fact | null; right: Fact | null; difference: { right_minus_left: number; unit: string } | null; both_not_recorded?: boolean };
interface Report { left: { file: string }; right: { file: string }; comparison: Record<string, any>; diagnostics: unknown[] }

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
const walkSourced = (x: unknown, out: Fact[] = []): Fact[] => {
  if (Array.isArray(x)) x.forEach((v) => walkSourced(v, out));
  else if (typeof x === 'object' && x !== null) { const o = x as Record<string, unknown>; if (o.source && typeof (o.source as Record<string, unknown>).pointer === 'string' && 'file' in (o.source as object)) out.push(o as Fact); else Object.values(o).forEach((v) => walkSourced(v, out)); }
  return out;
};
const outcomes = (items: Item[]) => items.map((i) => [i.key, i.outcome]);

describe('matchByKey: explicit keys only, ambiguity listed instead of guessed', () => {
  it('matches unique keys, reports one-sided keys, and quarantines keys that repeat on a side', () => {
    const m = matchByKey([{ k: 'a', v: 1 }, { k: 'b', v: 2 }, { k: 'd', v: 1 }, { k: 'd', v: 2 }, { k: null }], [{ k: 'a', v: 1 }, { k: 'c', v: 3 }, { k: 'd', v: 9 }], (x: { k: string | null }) => x.k);
    expect(m.matched.map((x: { key: string }) => x.key)).toEqual(['a']);
    expect(m.onlyLeft.map((x: { key: string }) => x.key)).toEqual(['b']);
    expect(m.onlyRight.map((x: { key: string }) => x.key)).toEqual(['c']);
    expect(m.ambiguous).toEqual([{ side: 'left', key: 'd', count: 2, items: [{ k: 'd', v: 1 }, { k: 'd', v: 2 }] }]);
    expect(m.unkeyed).toEqual({ left: 1, right: 0 });
  });
  it('numeric differences exist only for documented fields with a unit', () => {
    expect(NUMERIC_UNITS.prompt_tokens_reported).toBe('tokens, provider-reported');
    expect(NUMERIC_UNITS.skills_tokens).toContain('estimated by Kriya');
    expect(NUMERIC_UNITS).not.toHaveProperty('mystery_tokens'); expect(NUMERIC_UNITS).not.toHaveProperty('wall_ms'); expect(NUMERIC_UNITS).not.toHaveProperty('prefix_break');
  });
});

describe('identical records', () => {
  it('every compared fact is equal, numeric differences are 0 with units, nothing is added, removed or ambiguous', () => {
    const r = compare('left', 'right-identical');
    const c = r.comparison;
    expect(c.identity.every((i: Item) => i.outcome === 'equal')).toBe(true);
    expect(c.identity.find((i: Item) => i.key === 'attempts').difference).toEqual({ right_minus_left: 0, unit: 'attempts (run row count)' });
    expect(c.events.outcome).toBe('equal');
    expect(c.context.tiers.every((i: Item) => i.outcome === 'equal') && c.context.tiers.length).toBe(2);
    expect(c.context.omissions.map((i: Item) => i.outcome)).toEqual(['equal']);
    expect(c.context.ambiguous).toEqual([]); expect(c.tokens.ambiguous).toEqual([]); expect(c.gates.ambiguous).toEqual([]);
    expect(c.tokens.items.every((i: Item) => i.outcome === 'equal')).toBe(true);
    expect(c.tokens.items.find((i: Item) => i.key === 'attempt 1 | prompt_tokens_reported').difference).toEqual({ right_minus_left: 0, unit: 'tokens, provider-reported' });
    expect(c.gates.items.map((g: Item) => g.outcome)).toEqual(['equal', 'equal', 'equal']);
    expect(c.failures.failure_report.outcome).toBe('equal');
    const md: string = renderMarkdown(r);
    expect(md).toContain(LABEL); expect(md).toContain('Not a controlled experiment');
    expect((md.match(/\| (changed|added|removed|unavailable) \|/g) ?? []).length).toBe(0);
    expect(md).not.toMatch(/\b(better|worse|improved|regressed|because|caused|succeeded)\b/i);
  });
});

describe('changed records', () => {
  const r = compare('left', 'right-changed');
  const c = r.comparison;
  it('identity, context, tokens, gates and failures show changed/added/removed with sources on both sides and unit-bearing differences', () => {
    expect(outcomes(c.identity)).toEqual([['run_id', 'changed'], ['goal', 'equal'], ['status (literal)', 'changed'], ['attempts', 'changed'], ['failure_category', 'removed'], ['timestamp (as stored)', 'equal'], ['duration_sec', 'changed'], ['files_modified (raw)', 'equal']]);
    expect(c.identity[3].difference).toEqual({ right_minus_left: 1, unit: 'attempts (run row count)' });
    expect(c.identity[6].difference).toEqual({ right_minus_left: 14.75, unit: 'seconds (run row duration_sec)' });
    expect(c.identity[2].left.source).toEqual({ file: 'left.json', pointer: '/data/run/status' });
    expect(c.identity[2].right.source).toEqual({ file: 'right-changed.json', pointer: '/data/run/status' });
    expect(outcomes(c.context.tiers)).toEqual([['attempt 1 | src/a.py', 'changed'], ['attempt 1 | src/b.py', 'removed'], ['attempt 1 | src/c.py', 'added']]);
    expect(outcomes(c.context.omissions)).toEqual([['attempt 1 | src/b.py', 'added'], ['attempt 1 | src/big.py', 'changed']]);
    expect(c.context.tiered_on_one_side_omitted_on_the_other.map((x: { key: string }) => x.key)).toEqual(['attempt 1 | src/b.py']);
    expect(c.context.tiers[0].left.source.pointer).toBe('/data/run_events/data/0/details/tiers/0/tier');
    expect(c.context.tiers[0].right.source.pointer).toBe('/data/run_events/data/0/details/tiers/0/tier');
    const tok = (k: string) => c.tokens.items.find((i: Item) => i.key === k);
    expect(tok('attempt 1 | prompt_tokens_reported')).toMatchObject({ outcome: 'changed', difference: { right_minus_left: 910, unit: 'tokens, provider-reported' } });
    expect(tok('attempt 1 | code_context_tokens')).toMatchObject({ outcome: 'changed', difference: { right_minus_left: 40, unit: 'tokens, estimated by Kriya (len/4)' } });
    expect(tok('attempt 1 | prefix_shared_tokens')).toMatchObject({ outcome: 'removed', difference: null }); // null on the right: no subtraction
    expect(tok('attempt 3 | prompt_tokens_reported')).toMatchObject({ outcome: 'added', difference: null });
    expect(c.tokens.items.some((i: Item) => i.key.includes('mystery_tokens'))).toBe(false); // unknown: never compared
    expect(c.tokens.unknown_uninterpreted).toEqual([expect.objectContaining({ side: 'right', attempt: '1', field: 'mystery_tokens' })]);
    expect(c.gates.items.map((g: Item) => [g.key, g.outcome])).toEqual([['attempt 1 | compile', 'equal'], ['attempt 2 | compile', 'equal'], ['attempt 2 | tests', 'changed'], ['attempt 3 | tests', 'added']]);
    expect(c.failures.failure_category.outcome).toBe('removed');
    expect(c.failures.failure_report.outcome).toBe('changed');
    expect(c.failures.events_with_failure_type.items.map((i: Item) => [i.key, i.outcome])).toEqual([['attempt 2 | candidate_gates.passed | compile_error', 'removed']]);
    expect(c.events).toMatchObject({ outcome: 'changed', left_count: 3, right_count: 4 });
  });
  it('the Markdown keeps original identities (event indices in pointers), escapes keys with pipes, and never ranks', () => {
    const md: string = renderMarkdown(r);
    expect(md).toContain('| attempt 1 \\| src/a.py | member_exact | full | changed | - |');
    expect(md).toContain('| attempt 1 \\| prompt_tokens_reported | 4190 | 5100 | changed | +910 tokens, provider-reported |');
    expect(md).toContain('| attempt 1 \\| src/c.py | (no record) | member_exact | added; member_id added | - |');
    expect(md).toContain('#/data/run_events/data/2/details/prompt_tokens_reported'); // attempt 3 composition keeps its recorded event index
    expect(md).toContain('| right | 1 | mystery_tokens | 9 |');
    expect(md).not.toMatch(/\b(better|worse|improved|regressed|because|caused|succeeded)\b/i);
    const tables = md.split('\n\n').filter((b) => b.startsWith('|'));
    for (const t of tables) { const rows = t.split('\n').filter((l) => l.startsWith('|')); const n = (rows[0]!.match(/(?<!\\)\|/g) ?? []).length; for (const row of rows) expect((row.match(/(?<!\\)\|/g) ?? []).length, row).toBe(n); }
  });
  it('every fact on either side resolves in its own input to the same value; absent fields are really absent', () => {
    const docs: Record<string, unknown> = { 'left.json': JSON.parse(input('left').text), 'right-changed.json': JSON.parse(input('right-changed').text) };
    const facts = walkSourced(r.comparison);
    expect(facts.length).toBeGreaterThan(80);
    for (const f of facts) {
      const res = resolve(docs[f.source.file], f.source.pointer);
      if (f.source.absent) expect(res.found, f.source.pointer).toBe(false);
      else { expect(res.found, `${f.source.file}#${f.source.pointer}`).toBe(true); if ('value' in f) expect(f.value, f.source.pointer).toEqual(res.value); }
    }
  });
});

describe('unavailable, ambiguous and deterministic', () => {
  it('a side whose section is not recorded makes the dependent comparisons "unavailable" with both availabilities; the rest is still compared', () => {
    const c = compare('left', 'right-unavailable').comparison;
    expect(c.events.outcome).toBe('unavailable');
    expect(c.events.right).toMatchObject({ availability: { value: 'not_recorded', source: { file: 'right-unavailable.json', pointer: '/data/run_events/availability' } }, reason: { value: 'column is NULL in the stored row' } });
    expect(c.context.tiers.outcome).toBe('unavailable'); expect(c.context.omissions.outcome).toBe('unavailable'); expect(c.context.section_items.outcome).toBe('unavailable');
    expect(c.tokens.outcome).toBe('unavailable');
    expect(c.gates).toMatchObject({ outcome: 'unavailable', right: { availability: { value: 'unreadable' } } });
    expect(c.failures.failure_report.outcome).toBe('equal'); // recorded on both sides
    expect(c.failures.events_with_failure_type.outcome).toBe('unavailable');
    expect(outcomes(c.identity).filter(([, o]) => o !== 'equal')).toEqual([['run_id', 'changed']]);
    const md: string = renderMarkdown(compare('left', 'right-unavailable'));
    expect(md).toContain('| run_events | recorded | not_recorded (column is NULL in the stored row) | unavailable |');
    expect(md).toContain('| gate outcomes | recorded | unreadable (stored text is not valid JSON) | unavailable |');
  });
  it('a key that repeats on one side is listed as ambiguous with every source and is never matched', () => {
    const c = compare('left', 'right-ambiguous').comparison;
    expect(c.context.ambiguous).toEqual([expect.objectContaining({ side: 'right', what: 'context tier', key: 'attempt 1 | src/a.py', count: 2 })]);
    expect(c.context.tiers.map((i: Item) => i.key)).toEqual(['attempt 1 | src/b.py']); // src/a.py excluded from matching, not guessed
    expect(c.tokens.ambiguous).toEqual([expect.objectContaining({ side: 'right', what: 'developer.prompt_composition', key: 'attempt 1', count: 2 })]);
    expect(c.tokens.items).toEqual([]);
    expect(c.gates.ambiguous).toEqual([expect.objectContaining({ side: 'right', what: 'gate outcome', key: 'attempt 1 | compile', count: 2 })]);
    expect(c.gates.items.map((g: Item) => [g.key, g.outcome])).toEqual([['attempt 2 | compile', 'removed'], ['attempt 2 | tests', 'equal']]);
    expect(c.failures.events_with_failure_type.items.map((i: Item) => [i.key, i.outcome])).toEqual([['attempt 2 | attempt.failed | compile_error', 'added'], ['attempt 2 | candidate_gates.passed | compile_error', 'equal']]);
    const md: string = renderMarkdown(compare('left', 'right-ambiguous'));
    expect(md).toContain('Ambiguous (same key more than once on one side; listed, never matched):');
    expect(md).toContain('| right | context tier | attempt 1 \\| src/a.py | 2 |');
    expect(md).toContain('#/data/run_events/data/0/details/tiers/1/tier'); // both occurrences are pointed at
  });
  it('is deterministic and refuses inputs that are not valid history.detail records', () => {
    const a = compare('left', 'right-changed'), b = compare('left', 'right-changed');
    expect(stableStringify(a)).toBe(stableStringify(b)); expect(renderMarkdown(a)).toBe(renderMarkdown(b));
    const bad = buildComparison(input('left'), { file: 'capabilities.json', text: readFileSync(join(GEN, 'capabilities.json'), 'utf8') });
    expect(bad.ok).toBe(false); expect(bad.problems[0]).toMatch(/Right \(capabilities\.json\) is not a valid KUP history\.detail record: status not_a_run_record/);
    const garbage = buildComparison({ file: 'g.json', text: '{' }, input('left'));
    expect(garbage.ok).toBe(false); expect(garbage.problems[0]).toMatch(/Left \(g\.json\).*malformed_json/);
  });
});

describe('CLI', () => {
  const tmp = () => mkdtempSync(join(tmpdir(), 'kup-run-compare-'));
  it('requires exactly --left, --right and --out; refuses extra inputs, non-detail inputs, existing outputs and input-as-output', () => {
    expect(parseArgs(['--left', 'a']).error).toMatch(/both --left/);
    expect(parseArgs(['--left', 'a', '--right', 'b']).error).toMatch(/--out/);
    expect(parseArgs(['--left', 'a', '--right', 'b', '--out', 'o', 'c.json']).error).toMatch(/exactly two inputs/);
    const out = join(tmp(), 'cmp');
    expect(main(['--left', join(FX, 'left.json'), '--right', join(GEN, 'capabilities.json'), '--out', out]).exitCode).toBe(2);
    expect(existsSync(join(out, 'compare.md'))).toBe(false); // nothing written for a refused comparison
    const ok = main(['--left', join(FX, 'left.json'), '--right', join(FX, 'right-changed.json'), '--out', out]);
    expect(ok.exitCode).toBe(0); expect(ok.message).toContain('identity facts not equal: 5');
    expect(JSON.parse(readFileSync(join(out, 'compare.json'), 'utf8')).tool).toBe('kup-run-compare');
    expect(main(['--left', join(FX, 'left.json'), '--right', join(FX, 'right-changed.json'), '--out', out]).message).toMatch(/refusing to overwrite/);
    expect(main(['--left', join(FX, 'left.json'), '--right', join(FX, 'right-changed.json'), '--out', out, '--overwrite']).exitCode).toBe(0);
    expect(main(['--left', join(out, 'compare.json'), '--right', join(FX, 'left.json'), '--out', out, '--overwrite']).message).toMatch(/overwrite the input/);
    const a = join(tmp(), 'a'), b = join(tmp(), 'b');
    for (const o of [a, b]) expect(main(['--left', join(FX, 'left.json'), '--right', join(FX, 'right-identical.json'), '--out', o]).exitCode).toBe(0);
    expect(readFileSync(join(a, 'compare.md'), 'utf8')).toBe(readFileSync(join(b, 'compare.md'), 'utf8'));
  });
  it('runs as a process with the documented usage', () => {
    const out = join(tmp(), 'cli');
    const text = execFileSync(process.execPath, [TOOL, '--left', join(FX, 'left.json'), '--right', join(FX, 'right-unavailable.json'), '--out', out], { encoding: 'utf8' });
    expect(text).toContain('wrote');
    let code = 0; let err = '';
    try { execFileSync(process.execPath, [TOOL, '--left', join(FX, 'left.json')], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }); } catch (e) { code = (e as { status: number }).status; err = String((e as { stderr: string }).stderr); }
    expect(code).toBe(2); expect(err).toContain('--right');
  });
});
