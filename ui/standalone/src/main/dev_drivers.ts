/**
 * Development and evidence drivers: the screenshot/DOM-step capture driver and its scripted folder chooser, the measurement and soak drivers, request tracing, and the
 * fixture stand-in (D-9). Everything here is reachable ONLY through environment variables of the process that launches the application.
 *
 * This module is NOT part of the shipped product. main.ts loads it with a guarded `require`; the product package (`mac-app.mjs package`) omits this file and the three it
 * imports, and `scripts-check-bundle.mjs` fails the build if any of them, or any of their environment variable names, reaches a product package. An evidence package
 * (`package --evidence`) keeps them so the packaged-app evidence runs can still be driven.
 */
import { app, type BrowserWindow } from 'electron';
import { join } from 'node:path';
import { runCapture } from './capture';
import { runMeasurement } from './measure';
import { DEFAULT_SOAK, runSoak } from './soak';
import { runKriya, type RunOutcome } from './kriya_process';

export interface DevDrivers {
  /** D-9: the development gate that lifts the fixture stand-in (never consulted by a packaged product). */
  allowReal: boolean;
  /** Scripted answers for the native folder chooser while the capture driver is active (null = the person cancelled); null when the driver is not active. */
  captureChoices: Array<string | null> | null;
  captureActive: boolean;
  trace(text: string): void;
  traceLine: ((line: string) => void) | undefined;
  windowArgs: string[];
  /** Extra webPreferences for evidence runs only: a window that is hidden or occluded while a long measurement runs must not have its timers throttled (that would measure the operating system, not the app). */
  webPreferences: { backgroundThrottling?: boolean };
  /** The fixture stand-in for a KUP read (development only). */
  runFake(argv: readonly string[], cwd: string): Promise<RunOutcome>;
  attachWindow(win: BrowserWindow): void;
  /** Runs the soak, capture or measurement driver the environment asked for (and exits the app when it is done). */
  run(win: BrowserWindow): Promise<void>;
}

export function createDevDrivers(env: NodeJS.ProcessEnv): DevDrivers {
  const MEASURE = env.KRIYA_UI_MEASURE === '1';
  const SOAK = env.KRIYA_UI_SOAK === '1';
  const TRACE = env.KRIYA_UI_TRACE === '1';
  const CAPTURE_DIR = env.KRIYA_UI_CAPTURE;
  const CAPTURE_STEPS = env.KRIYA_UI_CAPTURE_STEPS;
  // The folders the scripted chooser returns in place of the native dialog, a JSON array where null means the person cancelled. Everything after the dialog - registry,
  // approval, Kriya's validation - is the production path.
  const captureChoices: Array<string | null> | null = CAPTURE_DIR && env.KRIYA_UI_CAPTURE_CHOOSE ? (JSON.parse(env.KRIYA_UI_CAPTURE_CHOOSE) as Array<string | null>) : null;
  const fakeKriya = join(__dirname, '..', '..', 'fake-kriya', 'fake_kriya.mjs');
  /** Only the fixture knobs reach the stand-in; never the ambient environment (P-31). */
  const fakeEnv = (): Record<string, string> => {
    const out: Record<string, string> = {};
    if (env.KRIYA_FAKE_BEHAVIOR) out.KRIYA_FAKE_BEHAVIOR = env.KRIYA_FAKE_BEHAVIOR;
    if (env.KRIYA_FAKE_FIXTURES) out.KRIYA_FAKE_FIXTURES = env.KRIYA_FAKE_FIXTURES;
    out.KRIYA_FAKE_STATE = env.KRIYA_FAKE_STATE ?? join(app.getPath('userData'), 'fake-kriya-snapshots.json');
    return out;
  };
  const trace = (text: string) => { if (TRACE) process.stderr.write(text); };
  return {
    allowReal: env.KRIYA_UI_ALLOW_REAL_KRIYA === '1',
    captureChoices, captureActive: Boolean(CAPTURE_DIR), trace,
    traceLine: TRACE ? (line: string) => process.stderr.write(`[kriya-ui] ${line}\n`) : undefined,
    windowArgs: MEASURE || SOAK ? ['--kriya-ui-measure'] : [],
    webPreferences: CAPTURE_DIR || MEASURE || SOAK ? { backgroundThrottling: false } : {},
    runFake: (argv, cwd) => runKriya({ executable: process.execPath, argv: [fakeKriya, ...argv], nodeScript: true, env: fakeEnv(), cwd }),
    attachWindow(win) {
      if (!MEASURE) return;
      // Diagnostics for the measurement run only (stderr): load lifecycle, preload errors, renderer console.
      const log = (m: string) => process.stderr.write(`[measure] ${m}\n`);
      win.webContents.on('did-finish-load', () => log('did-finish-load'));
      win.webContents.on('did-fail-load', (_e, code, desc, url) => log(`did-fail-load ${code} ${desc} ${url}`));
      win.webContents.on('preload-error', (_e, path, error) => log(`preload-error ${path}: ${error.message}`));
      win.webContents.on('console-message', (event) => log(`console[${event.level}] ${event.message} (${event.sourceId}:${event.lineNumber})`));
      win.webContents.on('render-process-gone', (_e, details) => log(`render-process-gone ${details.reason}`));
    },
    async run(win) {
      if (SOAK) {
        try {
          const n = (k: string, d: number) => Number(env[k] ?? d);
          const file = await runSoak(win, join(__dirname, '..', '..', 'measurements'), { seconds: n('KRIYA_UI_SOAK_SECONDS', DEFAULT_SOAK.seconds), minSelections: n('KRIYA_UI_SOAK_MIN_SELECTIONS', DEFAULT_SOAK.minSelections), sampleSeconds: n('KRIYA_UI_SOAK_SAMPLE_SECONDS', DEFAULT_SOAK.sampleSeconds), warmupSeconds: n('KRIYA_UI_SOAK_WARMUP_SECONDS', DEFAULT_SOAK.warmupSeconds), ceilingMB: n('KRIYA_UI_SOAK_CEILING_MB', DEFAULT_SOAK.ceilingMB) });
          process.stdout.write(`soak written: ${file}\n`);
          app.exit(0);
        } catch (e) {
          process.stderr.write(`soak failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
          app.exit(1);
        }
      } else if (CAPTURE_DIR && CAPTURE_STEPS) {
        try {
          const files = await runCapture(win, CAPTURE_DIR, CAPTURE_STEPS);
          process.stdout.write(`captured: ${files.join(', ')}\n`);
        } catch (e) {
          process.stderr.write(`capture failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
          app.exit(1);
        }
      } else if (MEASURE) {
        try {
          const file = await runMeasurement(win, join(__dirname, '..', '..', 'measurements'), Number(env.KRIYA_UI_MEASURE_CYCLES ?? 100));
          process.stdout.write(`measurement written: ${file}\n`);
          app.exit(0);
        } catch (e) {
          process.stderr.write(`measurement failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
          app.exit(1);
        }
      }
    },
  };
}
