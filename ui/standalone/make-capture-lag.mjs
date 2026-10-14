#!/usr/bin/env node
/** Generates capture-lag.json: the CLEAN event-loop measurement (GUI final). No screenshot is taken at any step (no capturePage), so nothing but the app itself runs in the
 *  window: a sampler inside the page records the event-loop lag every 50 ms across (1) a period of idle, (2) ordinary navigation through every page and tab, and (3) a
 *  deterministic active run (the controlled workflow body: real Kriya launch, records and polling, no model). Segments are marked so they can be read separately. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const W = `const W=(f,ms=120000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},50);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const DEEP = ['Context', 'Plan', 'Attempts', 'Evidence'];
const tab = (n) => `{const find=(x)=>[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()===x);${DEEP.includes(n) ? "const d=find('Details');if(d)d.click();" : ''}const t=find('${n}');if(t)t.click();}`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Current run"]')`;
const startBtn = `[...document.querySelectorAll('button')].find(b=>b.textContent==='Start Run')`;
const mark = (name) => `window.__marks.push(['${name}', window.__lag.length]);`;
const stats = `const pct=(a,p)=>a.length?a.slice().sort((x,y)=>x-y)[Math.min(a.length-1,Math.floor(a.length*p))]:null;const seg=(s,e)=>{const a=window.__lag.slice(s,e);return {samples:a.length,median:pct(a,.5),p95:pct(a,.95),p99:pct(a,.99),max:a.length?Math.max(...a):null,over100ms:a.filter(x=>x>100).length}};`;

const steps = [];
const add = (s) => steps.push(s);
add({ wait: 800 });
add({ name: 'install_sampler', js: `(()=>{window.__lag=[];window.__marks=[];let last=performance.now();window.__t=setInterval(()=>{const n=performance.now();window.__lag.push(Math.max(0,Math.round(n-last-50)));last=n},50);${mark('start')}return 'sampler installed'})()`, wait: 100 });
add({ name: 'idle', js: `(()=>{${mark('idle_begin')}return 'idle'})()`, wait: 10000 });
add({ name: 'open_folder', js: `(async()=>{${W}${mark('open_folder_begin')}[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);${mark('open_folder_end')}return ok})()`, wait: 1500 });
// ordinary navigation: every destination and every central tab, several times, with human-scale pauses (no screenshots)
for (let round = 0; round < 3; round += 1) {
  for (const dest of ['Runs', 'Workspace', 'Settings', 'Runs']) add({ js: `(()=>{${nav(dest)}})()`, wait: 700 });
}
add({ name: 'navigation_marks', js: `(()=>{${mark('navigation_end')}return 'nav done'})()`, wait: 100 });
add({ js: `(()=>{${nav('Project')}})()`, wait: 800 });
add({ js: `(()=>{${goalSet('Add a clamp(value, low, high) function with tests.')}})()`, wait: 600 });
add({ name: 'start_run', js: `(async()=>{${W}${mark('run_begin')}${startBtn}.click();return await W(()=>${banner},60000)})()`, wait: 300 });
add({ name: 'run_to_terminal', js: `(async()=>{${W}const ok=await W(()=>${banner}&&${banner}.dataset.stage==='finished',300000);${mark('run_end')}return ok?${banner}.dataset.outcome:'TIMEOUT'})()`, wait: 1500 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 1500 });
for (const t of ['Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence']) add({ js: `(()=>{${tab(t)}})()`, wait: 900 });
add({ name: 'after_run_navigation', js: `(()=>{${mark('after_end')}return 'done'})()`, wait: 3000 });
add({ name: 'event_loop_lag', js: `(()=>{clearInterval(window.__t);${stats}const m=Object.fromEntries(window.__marks);const out={all:seg(0,window.__lag.length),idle:seg(m.idle_begin,m.open_folder_begin),open_folder:seg(m.open_folder_begin,m.open_folder_end),navigation:seg(m.open_folder_end,m.navigation_end),active_run:seg(m.run_begin,m.run_end),after_run:seg(m.run_end,m.after_end)};return out})()`, wait: 100 });
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-lag.json'), JSON.stringify(steps, null, 1));
console.log(`capture-lag.json: ${steps.length} steps (no screenshots)`);
