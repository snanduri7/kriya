#!/usr/bin/env node
/** Regenerates assets/Kriya.icns from assets/Kriya.svg (macOS only: Electron renders the SVG with transparency, sips resizes, iconutil packs).
 *  Usage: node make-icon.mjs   (then `npm run package:mac` picks assets/Kriya.icns up). */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const svg = join(HERE, 'assets', 'Kriya.svg');
const icns = join(HERE, 'assets', 'Kriya.icns');
if (!existsSync(svg)) throw new Error(`missing ${svg}`);
const electron = createRequire(import.meta.url)('electron');
const work = mkdtempSync(join(process.env.TMPDIR ?? '/tmp', 'kriya-icon-'));
try {
  const master = join(work, 'master.png');
  execFileSync(electron, [join(HERE, 'make-icon-render.cjs'), svg, master], { stdio: 'inherit' });
  const set = join(work, 'Kriya.iconset');
  mkdirSync(set);
  for (const [name, px] of [['16x16', 16], ['16x16@2x', 32], ['32x32', 32], ['32x32@2x', 64], ['128x128', 128], ['128x128@2x', 256], ['256x256', 256], ['256x256@2x', 512], ['512x512', 512], ['512x512@2x', 1024]]) {
    execFileSync('/usr/bin/sips', ['-z', String(px), String(px), master, '--out', join(set, `icon_${name}.png`)], { stdio: 'pipe' });
  }
  execFileSync('/usr/bin/iconutil', ['-c', 'icns', set, '-o', icns], { stdio: 'inherit' });
  console.log(`wrote ${icns}`);
} finally {
  rmSync(work, { recursive: true, force: true });
}
