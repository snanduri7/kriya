// Electron main script: renders assets/Kriya.svg to a 1024x1024 PNG with a transparent background. Used only by make-icon.mjs.
const { app, BrowserWindow } = require('electron');
const { readFileSync, writeFileSync } = require('node:fs');
const [svgPath, pngPath] = process.argv.slice(-2);
app.disableHardwareAcceleration();
app.whenReady().then(() => {
  const win = new BrowserWindow({ width: 1024, height: 1024, show: false, transparent: true, frame: false, webPreferences: { offscreen: true } });
  const svg = readFileSync(svgPath, 'utf8');
  win.webContents.on('did-finish-load', () => {
    setTimeout(async () => {
      const image = await win.webContents.capturePage();
      const size = image.getSize();
      if (size.width !== 1024 || size.height !== 1024) { console.error(`unexpected capture size ${size.width}x${size.height}`); app.exit(3); return; }
      writeFileSync(pngPath, image.toPNG());
      app.exit(0);
    }, 800);
  });
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:1024px;height:1024px}</style>${svg}`));
  setTimeout(() => app.exit(2), 20000);
});
