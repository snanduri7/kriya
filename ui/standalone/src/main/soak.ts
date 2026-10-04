/**
 * Phase E soak (owner instruction 2026-10-04): a long run with NO forced GC - at least 1,000 selections over two hours,
 * all-process metrics sampled every five minutes, then a ceiling check (550 MB total working set after warm-up) and a
 * sustained-growth check (least-squares slope of the post-warm-up samples). Progress is flushed at every sample so an
 * interruption still leaves evidence. Active only with KRIYA_UI_SOAK=1.
 */
import { app, type BrowserWindow } from 'electron';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { arch, release } from 'node:os';

export interface SoakOptions { seconds: number; minSelections: number; sampleSeconds: number; warmupSeconds: number; ceilingMB: number }
/** Owner-agreed parameters (2026-10-04): two hours, >= 1,000 selections, 5-minute samples, 15-minute warm-up, 550 MB ceiling. */
export const DEFAULT_SOAK: SoakOptions = { seconds: 7200, minSelections: 1000, sampleSeconds: 300, warmupSeconds: 900, ceilingMB: 550 };

interface SoakSample { at_s: number; selections: number; totalWorkingSetMB: number; byType: Record<string, number>; psRssTotalMB: number | null; errors: number }

function metrics(): { total: number; byType: Record<string, number>; pids: number[] } {
  const byType: Record<string, number> = {};
  let total = 0; const pids: number[] = [];
  for (const m of app.getAppMetrics()) { const mb = m.memory.workingSetSize / 1024; total += mb; byType[m.type] = Math.round((byType[m.type] ?? 0) + mb); pids.push(m.pid); }
  return { total: Math.round(total), byType, pids };
}

function psRss(pids: number[]): number | null {
  try { return Math.round(execFileSync('/bin/ps', ['-o', 'rss=', '-p', pids.join(',')], { encoding: 'utf8' }).split('\n').map((l) => Number(l.trim()) || 0).reduce((a, b) => a + b, 0) / 1024); } catch { return null; }
}

/** Least-squares slope in MB per hour over (t seconds, MB) points. */
export function slopeMBPerHour(points: { at_s: number; mb: number }[]): number | null {
  if (points.length < 2) return null;
  const n = points.length, mx = points.reduce((s, p) => s + p.at_s, 0) / n, my = points.reduce((s, p) => s + p.mb, 0) / n;
  const sxx = points.reduce((s, p) => s + (p.at_s - mx) ** 2, 0);
  if (sxx === 0) return null;
  return Math.round((points.reduce((s, p) => s + (p.at_s - mx) * (p.mb - my), 0) / sxx) * 3600 * 100) / 100;
}

export async function runSoak(win: BrowserWindow, outDir: string, opts: SoakOptions): Promise<string> {
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const js = (code: string) => Promise.race([win.webContents.executeJavaScript(code, true) as Promise<unknown>, new Promise((_r, rej) => setTimeout(() => rej(new Error('executeJavaScript timed out')), 30_000))]);
  mkdirSync(outDir, { recursive: true });
  const startedIso = new Date().toISOString();
  const file = join(outDir, `soak-${startedIso.replace(/[:.]/g, '-')}.json`);
  const t0 = Date.now();
  const samples: SoakSample[] = [];
  const errors: { at_s: number; runId: string; message: string }[] = [];
  let runIds: string[] = [];
  for (let i = 0; i < 100 && runIds.length === 0; i++) { runIds = (await js('window.__kriyaDriver ? window.__kriyaDriver.listRunIds() : []')) as string[]; if (!runIds.length) await sleep(200); }
  if (!runIds.length) throw new Error('soak: the fixture list did not load');
  const specials = ['run-big-events', 'run-diff-2000', 'run-unknown-fields', 'run-incomplete-context'];
  const pool = [...specials, ...runIds];
  const intervalMs = Math.floor((opts.seconds * 1000) / opts.minSelections * 0.85); // finishes the minimum with margin
  let selections = 0; let lastSample = 0;
  const flush = (done: boolean) => {
    const elapsed = (Date.now() - t0) / 1000;
    const post = samples.filter((s) => s.at_s >= opts.warmupSeconds);
    const maxAfterWarmup = post.length ? Math.max(...post.map((s) => s.totalWorkingSetMB)) : null;
    const report = {
      kind: 'soak', started_at: startedIso, done, elapsed_s: Math.round(elapsed), options: opts,
      electron: process.versions.electron, chrome: process.versions.chrome, host: { platform: process.platform, arch: arch(), os_release: release() },
      forced_gc: false, selection_interval_ms: intervalMs, selections, errors_count: errors.length, errors: errors.slice(0, 50),
      samples,
      ceiling_check: { ceiling_MB: opts.ceilingMB, max_total_working_set_after_warmup_MB: maxAfterWarmup, pass: maxAfterWarmup === null ? null : maxAfterWarmup <= opts.ceilingMB, post_warmup_samples: post.length },
      sustained_growth: { slope_MB_per_hour_after_warmup: slopeMBPerHour(post.map((s) => ({ at_s: s.at_s, mb: s.totalWorkingSetMB }))), first_post_warmup_MB: post[0]?.totalWorkingSetMB ?? null, last_MB: post.at(-1)?.totalWorkingSetMB ?? null },
    };
    writeFileSync(file, JSON.stringify(report, null, 2));
  };
  const sample = () => { const m = metrics(); samples.push({ at_s: Math.round((Date.now() - t0) / 1000), selections, totalWorkingSetMB: m.total, byType: m.byType, psRssTotalMB: psRss(m.pids), errors: errors.length }); flush(false); };
  sample();
  while ((Date.now() - t0) / 1000 < opts.seconds || selections < opts.minSelections) {
    const id = pool[selections % pool.length]!;
    const tSel = Date.now();
    try { await js(`window.__kriyaDriver.selectRun(${JSON.stringify(id)})`); } catch (e) { errors.push({ at_s: Math.round((Date.now() - t0) / 1000), runId: id, message: e instanceof Error ? e.message : String(e) }); }
    selections++;
    if (Date.now() - lastSample >= opts.sampleSeconds * 1000) { sample(); lastSample = Date.now(); }
    const wait = intervalMs - (Date.now() - tSel);
    if (wait > 0) await sleep(wait);
  }
  sample();
  flush(true);
  return file;
}
