/**
 * Screenshot driver, active only with KRIYA_UI_CAPTURE=<output dir> (development evidence for the cockpit shell). It loads the
 * already-built renderer, runs a list of DOM steps from the JSON file named by KRIYA_UI_CAPTURE_STEPS (each: optional `js` to run
 * in the page, `wait` ms, and a `file` name to write a PNG of the window), then exits. It adds no capability to the renderer.
 */
import { app, type BrowserWindow } from 'electron';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

interface Step { js?: string; name?: string; wait?: number; file?: string; width?: number; height?: number }

export async function runCapture(win: BrowserWindow, outDir: string, stepsFile: string): Promise<string[]> {
  const steps = JSON.parse(readFileSync(stepsFile, 'utf8')) as Step[];
  mkdirSync(outDir, { recursive: true });
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const written: string[] = [];
  const results: Record<string, unknown> = {};
  await sleep(2500);
  for (const step of steps) {
    if (step.width && step.height) { win.setContentSize(step.width, step.height); await sleep(400); }
    if (step.js) {
      const value: unknown = await win.webContents.executeJavaScript(step.js, true);
      if (step.name) results[step.name] = value; // e.g. measured latencies, written to results.json beside the PNGs
    }
    await sleep(step.wait ?? 600);
    if (step.file) {
      const image = await win.webContents.capturePage();
      const path = join(outDir, step.file);
      writeFileSync(path, image.toPNG());
      written.push(path);
    }
  }
  writeFileSync(join(outDir, 'results.json'), JSON.stringify(results, null, 1));
  app.exit(0);
  return written;
}
