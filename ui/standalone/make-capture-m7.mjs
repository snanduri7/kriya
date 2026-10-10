#!/usr/bin/env node
/** Generates capture-m7.json: the product walk-through (development evidence): REAL cockpit build, REAL Kriya read process, CONTROLLED workflow body. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const W = `const W=(f,ms=90000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},20);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const tab = (n) => `[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()==='${n}').click();`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Active run"]')`;
const startBtn = `[...document.querySelectorAll('button')].find(b=>b.textContent==='Start Run')`;
const ready = `W(()=>document.body.innerText.includes('Configuration digest'))`;
const dismiss = `const d=[...document.querySelectorAll('button')].find(b=>b.textContent==='Dismiss');if(d)d.click();`;

const steps = [];
const add = (s) => steps.push(s);
add({ wait: 800, file: '01-welcome.png', width: 1536, height: 960 });
add({ name: 'open_folder_to_workspace_ms', js: `(async()=>{${W}const t0=performance.now();document.querySelector('main[aria-label=Welcome] button').click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'));return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 600 });
add({ name: 'open_folder_to_runs_ms', js: `(async()=>{${W}return await W(()=>document.body.innerText.includes('Runs'))})()`, wait: 400, file: '02-runs-empty-existing-project.png' });
add({ js: `(()=>{${nav('Workspace')}})()`, wait: 600, file: '03-workspace.png' });
add({ name: 'preflight_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await ${ready};${goalSet('Update greet(name) so it returns "Hello, <name>!" while keeping the tests passing.')}return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 600, file: '04-goal-preflight.png' });
add({ name: 'start_click_to_banner_ms', js: `(async()=>{${W}const t0=performance.now();${startBtn}.click();const ok=await W(()=>${banner});return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 200 });
add({ name: 'launch_record_seen_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}.dataset.stage==='running');return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 2600, file: '05-running.png' });
add({ name: 'terminal_after_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}.dataset.stage==='finished');return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 1500, file: '06-terminal-success.png' });
add({ name: 'timing_success_run', js: `(()=>JSON.parse(${banner}.dataset.timing))()`, wait: 100 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 800, file: '07-run-overview-timeline-why.png' });
for (const [i, name] of ['Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence'].entries()) {
  add({ name: `tab_${name.toLowerCase()}_first_render_ms`, js: `(async()=>{${W}const t0=performance.now();${tab(name)}await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>20);return Math.round(performance.now()-t0)})()`, wait: 700, file: `${String(8 + i).padStart(2, '0')}-${name.toLowerCase()}.png` });
}
add({ js: `(()=>{${tab('Overview')}})()`, wait: 300 });
add({ width: 1180, height: 900, wait: 700, file: '14-responsive-1180.png' });
add({ width: 1100, height: 900, wait: 700, file: '15-responsive-1100.png' });
add({ width: 1536, height: 960, wait: 500 });
// a second run: durable failure
add({ js: `(async()=>{${W}${dismiss}${nav('Workspace')}await ${ready};${goalSet('Add input validation to greet. [controlled:failure]')}await W(()=>${startBtn}&&!${startBtn}.disabled);${startBtn}.click();await W(()=>${banner}&&${banner}.dataset.stage==='finished')})()`, wait: 1500, file: '16-terminal-failure.png' });
// a third: a decision the desktop cannot make
add({ js: `(async()=>{${W}${dismiss}${goalSet('Update the CI workflow. [controlled:approval]')}await W(()=>${startBtn}&&!${startBtn}.disabled);${startBtn}.click();await W(()=>${banner}&&${banner}.dataset.stage==='finished')})()`, wait: 1500, file: '17-approval-required.png' });
add({ name: 'approval_banner_text', js: `(()=>${banner}.innerText)()`, wait: 100 });
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-m7.json'), JSON.stringify(steps, null, 1));
console.log(`capture-m7.json: ${steps.length} steps`);
