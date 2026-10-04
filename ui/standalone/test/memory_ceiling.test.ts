/** Owner decision 2026-10-04: the measured idle footprint (393 MB) is accepted for M1 and the 550 MB post-warm-up
 * ceiling is RETAINED as a regression gate. This test binds that ceiling to the committed soak evidence and to the soak
 * defaults, so a later soak that breaches it, or a change that silently raises the ceiling, fails here. */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { DEFAULT_SOAK, slopeMBPerHour } from '../src/main/soak';

const DIR = join(__dirname, '..', 'measurements');
const latestSoak = () => readdirSync(DIR).filter((f) => f.startsWith('soak-') && f.endsWith('.json')).sort().at(-1);

describe('memory regression ceiling (retained by the owner, 2026-10-04)', () => {
  it('the soak defaults keep the 550 MB ceiling, two hours, 1,000 selections, 5-minute samples, no forced GC', () => {
    expect(DEFAULT_SOAK).toEqual({ seconds: 7200, minSelections: 1000, sampleSeconds: 300, warmupSeconds: 600, ceilingMB: 550 });
  });
  it('the latest committed soak evidence is complete and under the ceiling with no sustained growth', () => {
    const file = latestSoak();
    expect(file, 'a committed soak-*.json is required').toBeTruthy();
    const d = JSON.parse(readFileSync(join(DIR, file!), 'utf8'));
    expect(d.done).toBe(true);
    expect(d.forced_gc).toBe(false);
    expect(d.selections).toBeGreaterThanOrEqual(1000);
    expect(d.errors_count).toBe(0);
    expect(d.options.ceilingMB).toBe(550);
    expect(d.ceiling_check.pass).toBe(true);
    expect(d.ceiling_check.max_total_working_set_after_warmup_MB).toBeLessThanOrEqual(550);
    const post = d.samples.filter((s: { at_s: number }) => s.at_s >= d.options.warmupSeconds);
    expect(post.length).toBeGreaterThanOrEqual(12);
    const slope = slopeMBPerHour(post.map((s: { at_s: number; totalWorkingSetMB: number }) => ({ at_s: s.at_s, mb: s.totalWorkingSetMB })));
    expect(slope).not.toBeNull();
    expect(slope!).toBeLessThan(10); // MB per hour: a real leak at this selection rate shows as tens of MB per hour
  });
  it('slopeMBPerHour measures growth (negative control: a 60 MB/h ramp is reported as such)', () => {
    const ramp = Array.from({ length: 12 }, (_, i) => ({ at_s: i * 300, mb: 400 + i * 5 }));
    expect(slopeMBPerHour(ramp)).toBe(60);
    expect(slopeMBPerHour([{ at_s: 0, mb: 1 }])).toBeNull();
  });
});
