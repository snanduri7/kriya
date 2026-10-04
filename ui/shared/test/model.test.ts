import { describe, expect, it } from 'vitest';
import { availabilityLabel, isRecorded } from '../src/model/availability';
import { checkEnvelope, eventsByAttempt, formatEventTime, normalizeDetail, parseStoredJson, unknownEventKeys } from '../src/model/normalize';
import { AVAILABILITY_STATES } from '../src/model/kup';
import { sanitizeText } from '../src/render/sanitize';
import { diffLines } from '../src/render/diff';

const envelope = (over: Record<string, unknown> = {}) => ({ schema_version: 1, operation: 'history.list', request_id: 'r', observed_at: '2026-10-04T09:00:00Z', source: null, consistency: null, data: {}, error: null, ...over });

describe('envelope acceptance (P-27)', () => {
  it('accepts a well-formed v1 envelope', () => { expect(checkEnvelope(envelope()).ok).toBe(true); });
  it('refuses another schema version with UNSUPPORTED_SCHEMA_VERSION, never a guessed rendering', () => {
    const r = checkEnvelope(envelope({ schema_version: 2 }));
    expect(r.ok).toBe(false); if (!r.ok) expect(r.code).toBe('UNSUPPORTED_SCHEMA_VERSION');
  });
  it('refuses malformed compatible JSON with INVALID_RESPONSE', () => {
    for (const bad of [null, [], 'x', envelope({ observed_at: 5 }), envelope({ error: 'oops' }), { schema_version: 1 }]) {
      const r = checkEnvelope(bad); expect(r.ok).toBe(false); if (!r.ok) expect(r.code).toBe('INVALID_RESPONSE');
    }
  });
});

describe('availability (P-24, D-5)', () => {
  it('shows data only for recorded + data', () => {
    expect(isRecorded({ availability: 'recorded', data: [] })).toBe(true);
    expect(isRecorded({ availability: 'recorded', data: null })).toBe(false);
    for (const s of AVAILABILITY_STATES.filter((x) => x !== 'recorded')) expect(isRecorded({ availability: s, data: [1] })).toBe(false);
  });
  it('labels every state and never maps an unknown value to success', () => {
    expect(availabilityLabel({ availability: 'unreadable' })).toBe('unreadable');
    expect(availabilityLabel({ availability: 'definitely_fine' })).toContain('unknown availability');
    expect(availabilityLabel(undefined)).toBe('not recorded');
  });
});

describe('stored values (P-25, P-30)', () => {
  it('keeps a non-JSON stored string verbatim', () => { expect(parseStoredJson('a,b')).toEqual({ parsed: 'a,b', raw: 'a,b', isJson: false }); });
  it('preserves unknown event keys; the serializer keys are the known set', () => {
    expect(unknownEventKeys({ kind: 'x', created_at: 1, attempt: 1, zebra: 1, alpha: 2 })).toEqual(['alpha', 'zebra']);
    expect(unknownEventKeys({ kind: 'x', created_at: 1, attempt: 1, source: 's', authority: 'advisory', message: 'm', failure_type: null, operation: null, details: {} })).toEqual([]);
    // the invented shape of the first draft is UNKNOWN to this UI - it would be surfaced as unknown fields, never read silently
    expect(unknownEventKeys({ kind: 'x', created_at: 1, event: 'x', at: 't', payload: {} })).toEqual(['at', 'event', 'payload']);
  });
  it('groups events by recorded attempt in recorded order, attempt-less events in their own group', () => {
    const g = eventsByAttempt([{ kind: 'a', created_at: 1, attempt: 2 }, { kind: 'b', created_at: 2 }, { kind: 'c', created_at: 3, attempt: 2 }]);
    expect([...g.keys()]).toEqual(['attempt 2', 'no attempt recorded']);
    expect(g.get('attempt 2')?.map((e) => e.kind)).toEqual(['a', 'c']);
  });
  it('renders created_at (epoch seconds) as labelled UTC, never local time; anything else is "time not recorded"', () => {
    expect(formatEventTime(1791097200.25)).toBe('2026-10-04 07:00:00.250 UTC');
    expect(formatEventTime(0)).toBe('1970-01-01 00:00:00.000 UTC');
    for (const bad of [null, undefined, '2026-10-04', Number.NaN, Number.POSITIVE_INFINITY, {}]) expect(formatEventTime(bad)).toBe('time not recorded');
  });
  it('normalizeDetail fills every missing optional section as not_recorded and keeps unknown top-level keys', () => {
    const d = normalizeDetail({ run: { run_id: 'r1' }, mystery: 42 });
    expect(d?.context.availability).toBe('not_recorded');
    expect(d?.comparisons.availability).toBe('not_recorded');
    expect((d as Record<string, unknown>).mystery).toBe(42);
    expect(normalizeDetail({ nope: true })).toBeNull();
  });
});

describe('sanitize (P-32)', () => {
  it('escapes control characters but keeps newlines and tabs', () => {
    expect(sanitizeText('a\u0007b\u001b[31mred\n\tok z')).toBe('a\\u0007b\\u001b[31mred\n\tok\\u2028z');
  });
});

describe('diff', () => {
  it('produces an exact line diff with numbering', () => {
    const { lines, exact } = diffLines('a\nb\nc', 'a\nx\nc');
    expect(exact).toBe(true);
    expect(lines.map((l) => l.op)).toEqual(['equal', 'del', 'add', 'equal']);
    expect(lines[1]).toMatchObject({ before: 2, after: null, text: 'b' });
  });
  it('handles 2000-line inputs quickly', () => {
    const a = Array.from({ length: 2000 }, (_, i) => `line ${i}`).join('\n');
    const b = Array.from({ length: 2000 }, (_, i) => (i % 50 === 0 ? `changed ${i}` : `line ${i}`)).join('\n');
    const t = performance.now(); const { lines, exact } = diffLines(a, b); const ms = performance.now() - t;
    expect(exact).toBe(true); expect(lines.filter((l) => l.op !== 'equal').length).toBe(80); expect(ms).toBeLessThan(200);
  });
});
