#!/usr/bin/env node
/** Generates capture-live.json: the ONE live GUI smoke (GUI M8.6). Drives the PACKAGED Kriya.app over a disposable scratch project; the real workflow, a real local model.
 *  The scripted chooser replaces ONLY the native dialog call (used when the macOS session cannot run the real dialog). Samples the page's event-loop lag while the run executes. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const MAX_MS = Number(process.env.LIVE_MAX_MS ?? 45 * 60 * 1000);
const W = `const W=(f,ms=${MAX_MS})=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},50);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const tab = (n) => `[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()==='${n}').click();`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Active run"]')`;
const startBtn = `[...document.querySelectorAll('button')].find(b=>b.textContent==='Start Run')`;
const GOAL = 'Update greet(name) so it returns "Hello, <name>!" while preserving the public function signature and keeping the project tests passing.';
const steps = []; const add = (s) => steps.push(s);
add({ wait: 800, file: '01-welcome.png', width: 1536, height: 960 });
add({ name: 'open_folder_to_workspace_ms', js: `(async()=>{${W}const t0=performance.now();document.querySelector('main[aria-label=Welcome] button').click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 600 });
add({ js: `(()=>{${nav('Workspace')}})()`, wait: 800 });
add({ name: 'preflight_text', js: `(async()=>{${W}await W(()=>document.body.innerText.includes('Configuration digest'),60000);return document.querySelector('#ck-goal').closest('section').innerText})()`, wait: 400, file: '02-workspace-goal-preflight.png' });
add({ name: 'workspace_text_before', js: `(()=>document.body.innerText)()`, wait: 100 });
add({ js: `(()=>{${goalSet(GOAL)}})()`, wait: 500, file: '03-goal-entered.png' });
add({ name: 'start_click_to_banner_ms', js: `(async()=>{${W}const t0=performance.now();${startBtn}.click();const ok=await W(()=>${banner},60000);window.__lag=[];let last=performance.now();window.__lagTimer=setInterval(()=>{const n=performance.now();window.__lag.push(Math.round(n-last-100));last=n},100);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 3000, file: '04-launching.png' });
for (let i = 0; i < 6; i += 1) add({ name: `banner_sample_${i}`, js: `(()=>${banner}?${banner}.innerText:null)()`, wait: 25000, file: `05-running-${i}.png` });
add({ name: 'terminal_after_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}&&${banner}.dataset.stage==='finished');return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 2500, file: '06-terminal.png' });
add({ name: 'terminal_banner_text', js: `(()=>${banner}.innerText)()`, wait: 100 });
add({ name: 'timing', js: `(()=>JSON.parse(${banner}.dataset.timing))()`, wait: 100 });
add({ name: 'event_loop_lag_ms', js: `(()=>{clearInterval(window.__lagTimer);const l=window.__lag.slice().sort((a,b)=>a-b);return {samples:l.length,max:l[l.length-1],p95:l[Math.floor(l.length*0.95)],median:l[Math.floor(l.length/2)]}})()`, wait: 100 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 1200, file: '07-run-overview.png' });
for (const [i, name] of ['Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence'].entries()) {
  add({ name: `tab_${name.toLowerCase()}_text`, js: `(async()=>{${W}${tab(name)}await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>10,30000);return document.querySelector('[role=tabpanel]').innerText.slice(0,1500)})()`, wait: 900, file: `${String(8 + i).padStart(2, '0')}-${name.toLowerCase()}.png` });
}
add({ name: 'runs_list_text', js: `(()=>document.querySelector('.ck-list')?document.querySelector('.ck-list').innerText:null)()`, wait: 100 });
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-live.json'), JSON.stringify(steps, null, 1));
console.log(`capture-live.json: ${steps.length} steps (max wait ${MAX_MS} ms)`);
