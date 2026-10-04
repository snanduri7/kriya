/**
 * Measurement driver (06 §A2, P-35), active only with KRIYA_UI_MEASURE=1. Drives the renderer through the exposed
 * AppDriver, reads app.getAppMetrics() for EVERY Electron process (main, renderer, GPU, utility), collects long
 * tasks (>50 ms; flagged over 200 ms) and time-to-populate per selection, writes JSON into standalone/measurements/.
 */
import { app, type BrowserWindow } from 'electron';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { arch, cpus, release, totalmem } from 'node:os';
import { execFileSync } from 'node:child_process';

interface ProcessSample { type: string; pid: number; workingSetKB: number; peakWorkingSetKB: number; privateKB: number | null; cpuPercent: number }
interface Sample { label: string; at_ms: number; processes: ProcessSample[]; totalWorkingSetMB: number; rendererJsHeapMB: number | null }

function sample(label: string, t0: number, rendererJsHeapBytes: number | null): Sample {
  const processes = app.getAppMetrics().map((m) => ({ type: m.type, pid: m.pid, workingSetKB: m.memory.workingSetSize, peakWorkingSetKB: m.memory.peakWorkingSetSize, privateKB: m.memory.privateBytes ?? null, cpuPercent: m.cpu.percentCPUUsage }));
  return { label, at_ms: Math.round(performance.now() - t0), processes, totalWorkingSetMB: Math.round(processes.reduce((s, p) => s + p.workingSetKB, 0) / 1024), rendererJsHeapMB: rendererJsHeapBytes === null ? null : Math.round((rendererJsHeapBytes / 1048576) * 10) / 10 };
}

function percentile(xs: number[], p: number): number { const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] ?? 0; }

export async function runMeasurement(win: BrowserWindow, outDir: string, cycles = 100): Promise<string> {
  const t0 = performance.now();
  const samples: Sample[] = [];
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const js = (code: string) => Promise.race([
    win.webContents.executeJavaScript(code, true) as Promise<unknown>,
    new Promise((_r, reject) => setTimeout(() => reject(new Error(`executeJavaScript timed out after 20 s: ${code.slice(0, 80)}`)), 20_000)),
  ]);
  // Discriminator for "memory stays flat" (rule 21): a working-set rise can be V8 GC lag rather than retention, so every
  // mark is sampled twice - before and after a forced full GC through the DevTools protocol - plus the renderer JS heap.
  const dbg = win.webContents.debugger;
  let gcAvailable = true; let gcError: string | null = null;
  try { dbg.attach('1.3'); } catch (e) { gcAvailable = false; gcError = String(e); }
  const heap = async () => (await js('performance.memory ? performance.memory.usedJSHeapSize : null')) as number | null;
  const forceGc = async () => { if (!gcAvailable) return; try { await dbg.sendCommand('HeapProfiler.collectGarbage'); } catch (e) { gcError = String(e); } await sleep(300); };
  const mark = async (label: string) => { samples.push(sample(`${label}_pre_gc`, t0, await heap())); await forceGc(); samples.push(sample(`${label}_post_gc`, t0, await heap())); };
  await sleep(3000);
  await mark('idle_after_load_3s');
  await js(`window.__kriyaLongTasks = []; new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__kriyaLongTasks.push({ start: e.startTime, duration: e.duration }); }).observe({ type: 'longtask', buffered: true });
    window.__kriyaFrameGaps = []; (function () { let last = performance.now(); function loop() { const now = performance.now(); if (now - last > 100) window.__kriyaFrameGaps.push({ at: Math.round(now), gap: Math.round(now - last) }); last = now; requestAnimationFrame(loop); } requestAnimationFrame(loop); })(); true`);
  let runIds: string[] = [];
  for (let i = 0; i < 50 && runIds.length === 0; i++) { runIds = (await js('window.__kriyaDriver ? window.__kriyaDriver.listRunIds() : []')) as string[]; if (!runIds.length) await sleep(200); }
  if (!runIds.length) throw new Error('driver has no runs; the fixture list did not load');
  const populate: { runId: string; ms: number }[] = [];
  const specials = ['run-big-events', 'run-diff-2000', 'run-unknown-fields', 'run-incomplete-context', 'run-avail-unreadable'];
  for (let i = 0; i < cycles; i++) {
    const id = i < specials.length ? specials[i]! : runIds[i % runIds.length]!;
    const r = (await js(`window.__kriyaDriver.selectRun(${JSON.stringify(id)})`)) as { populatedMs: number };
    populate.push({ runId: id, ms: Math.round(r.populatedMs * 10) / 10 });
    if (i === 0) samples.push(sample('after_first_selection', t0, await heap()));
    if (i === 9) await mark('after_10_cycles');
    if (i === 49) await mark('after_50_cycles');
  }
  await mark(`after_${cycles}_cycles`);
  await sleep(3000);
  await mark('idle_after_cycles_3s');
  const longTasks = (await js('window.__kriyaLongTasks')) as { start: number; duration: number }[];
  const frameGaps = (await js('window.__kriyaFrameGaps')) as { at: number; gap: number }[];

  // Keyboard-only selection in the real Chromium: focus the runs listbox, press ArrowDown three times, Home, End.
  await js(`document.querySelector('.runs .vlist').focus(); true`);
  const key = async (keyCode: string) => { win.webContents.sendInputEvent({ type: 'keyDown', keyCode }); win.webContents.sendInputEvent({ type: 'keyUp', keyCode }); await sleep(60); };
  await key('Home'); await key('Down'); await key('Down'); await key('Down');
  await sleep(400);
  const keyboard = (await js(`({ focusedRole: document.activeElement && document.activeElement.getAttribute('role'), selectedPos: (document.querySelector('.runs [role=option][aria-selected=true]') || {}).getAttribute ? document.querySelector('.runs [role=option][aria-selected=true]').getAttribute('aria-posinset') : null, heading: (document.querySelector('.run-header .goal') || {}).textContent || null })`)) as Record<string, unknown>;
  await key('Tab'); await sleep(50);
  const tabFocus = (await js(`({ tag: document.activeElement && document.activeElement.tagName, text: document.activeElement && document.activeElement.textContent, focusVisible: document.activeElement && document.activeElement.matches(':focus-visible'), outlineStyle: document.activeElement && getComputedStyle(document.activeElement).outlineStyle, outlineWidth: document.activeElement && getComputedStyle(document.activeElement).outlineWidth })`)) as Record<string, unknown>;

  // Accessibility snapshot: every interactive control has an accessible name; landmark/role inventory.
  const a11y = (await js(`(() => { const ctl = [...document.querySelectorAll('button, [role=tab], [role=listbox], [role=option], input')]; const unnamed = ctl.filter((el) => !(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.textContent.trim() || el.getAttribute('placeholder') || el.getAttribute('title') || (el.labels && el.labels.length))); return { controls: ctl.length, unnamed: unnamed.map((e) => e.outerHTML.slice(0, 80)), roles: Object.fromEntries(['region','listbox','option','tablist','tab','tabpanel','status','alert','group','searchbox'].map((r) => [r, document.querySelectorAll('[role=' + r + ']' + (r === 'searchbox' ? ', input[type=search]' : '')).length])), headings: [...document.querySelectorAll('h1,h2,h3')].length }; })()`)) as Record<string, unknown>;

  // Screenshots (evidence for the P-21 layout) at the wide size with the big-events run selected, then narrow.
  mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  await js(`window.__kriyaDriver.selectRun('run-diff-2000')`); await sleep(500);
  await js(`document.querySelectorAll('[role=tab]').forEach((t) => { if (t.textContent.startsWith('Diff')) t.click(); }); true`); await sleep(400);
  await js(`const c = document.querySelector('.diff-files .chip'); if (c) c.click(); true`); await sleep(600);
  writeFileSync(join(outDir, `screenshot-wide-${stamp}.png`), (await win.webContents.capturePage()).toPNG());
  // Resizing: at 800 px wide the inspector becomes a tab beside the timeline (P-21).
  win.setSize(800, 700); await sleep(600);
  writeFileSync(join(outDir, `screenshot-narrow-${stamp}.png`), (await win.webContents.capturePage()).toPNG());
  const narrow = (await js(`({ width: innerWidth, narrowToggleDisplay: getComputedStyle(document.querySelector('.narrow-only')).display, inspectorDisplay: getComputedStyle(document.querySelector('.inspector')).display, columns: getComputedStyle(document.querySelector('.layout')).gridTemplateColumns, horizontalOverflow: document.documentElement.scrollWidth > innerWidth })`)) as Record<string, unknown>;
  win.setSize(1400, 900); await sleep(600);
  const wide = (await js(`({ width: innerWidth, narrowToggleDisplay: getComputedStyle(document.querySelector('.narrow-only')).display, inspectorDisplay: getComputedStyle(document.querySelector('.inspector')).display, columns: getComputedStyle(document.querySelector('.layout')).gridTemplateColumns })`)) as Record<string, unknown>;

  // Independent OS-level cross-check of the same PIDs (RSS from ps).
  const pids = app.getAppMetrics().map((m) => m.pid);
  let psCrossCheck = 'unavailable';
  try { psCrossCheck = execFileSync('/bin/ps', ['-o', 'pid=,rss=,command=', '-p', pids.join(',')], { encoding: 'utf8' }).split('\n').filter(Boolean).map((l) => l.trim().replace(/\s+/g, ' ').slice(0, 140)).join('\n'); } catch (e) { psCrossCheck = `ps failed: ${String(e)}`; }
  const psTotalRssMB = Math.round(psCrossCheck.split('\n').map((l) => Number(l.split(' ')[1]) || 0).reduce((a, b) => a + b, 0) / 1024);
  const byLabel = (l: string) => samples.find((s) => s.label === l)?.totalWorkingSetMB;
  const heapAt = (l: string) => samples.find((s) => s.label === l)?.rendererJsHeapMB;
  const report = {
    measured_at: new Date().toISOString(),
    electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node,
    host: { platform: process.platform, arch: arch(), os_release: release(), cpus: cpus().length, total_memory_gb: Math.round((totalmem() / 1e9) * 10) / 10 },
    cycles, runs_available: runIds.length,
    samples,
    long_tasks: { count: longTasks.length, over_200ms: longTasks.filter((t) => t.duration > 200), max_ms: Math.max(0, ...longTasks.map((t) => t.duration)) },
    frame_gaps_over_100ms: { count: frameGaps.length, over_200ms: frameGaps.filter((g) => g.gap > 200), max_ms: Math.max(0, ...frameGaps.map((g) => g.gap)), first_10: frameGaps.slice(0, 10) },
    gc: { forced_via_devtools_protocol: gcAvailable, error: gcError },
    populate: { max_ms: Math.max(...populate.map((p) => p.ms)), p95_ms: percentile(populate.map((p) => p.ms), 95), median_ms: percentile(populate.map((p) => p.ms), 50), over_2000ms: populate.filter((p) => p.ms > 2000), first_12: populate.slice(0, 12) },
    memory_flatness_MB: {
      working_set_pre_gc: { idle: byLabel('idle_after_load_3s_pre_gc'), after_10: byLabel('after_10_cycles_pre_gc'), after_50: byLabel('after_50_cycles_pre_gc'), after_cycles: byLabel(`after_${cycles}_cycles_pre_gc`), idle_after: byLabel('idle_after_cycles_3s_pre_gc') },
      working_set_post_gc: { idle: byLabel('idle_after_load_3s_post_gc'), after_10: byLabel('after_10_cycles_post_gc'), after_50: byLabel('after_50_cycles_post_gc'), after_cycles: byLabel(`after_${cycles}_cycles_post_gc`), idle_after: byLabel('idle_after_cycles_3s_post_gc') },
      renderer_js_heap_post_gc: { idle: heapAt('idle_after_load_3s_post_gc'), after_10: heapAt('after_10_cycles_post_gc'), after_50: heapAt('after_50_cycles_post_gc'), after_cycles: heapAt(`after_${cycles}_cycles_post_gc`), idle_after: heapAt('idle_after_cycles_3s_post_gc') },
    },
    keyboard_only_selection: { ...keyboard, after_tab: tabFocus },
    accessibility_snapshot: a11y,
    resize: { narrow_800px: narrow, wide_1400px: wide },
    ps_cross_check: { rss_total_MB: psTotalRssMB, rows: psCrossCheck.split('\n') },
    pids_for_cross_check: samples[samples.length - 1]?.processes.map((p) => `${p.type}:${p.pid}`),
  };
  mkdirSync(outDir, { recursive: true });
  const file = join(outDir, `measure-${report.measured_at.replace(/[:.]/g, '-')}.json`);
  writeFileSync(file, JSON.stringify(report, null, 2));
  return file;
}
