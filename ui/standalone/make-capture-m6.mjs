#!/usr/bin/env node
/** Generates capture-m6.json: the Start Run walk-through (development evidence). Driven over the REAL cockpit build, the REAL Kriya read process and the CONTROLLED workflow body. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const W = `const W=(f,ms=90000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},20);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Current run"]')`;
const timing = `JSON.parse(${banner}.dataset.timing)`;
const startBtn = `[...document.querySelectorAll('button')].find(b=>b.textContent==='Start Run')`;
const ready = `W(()=>document.body.innerText.includes('Configuration digest'))`; // the preflight rendered

const steps = [];
const add = (s) => steps.push(s);
add({ wait: 800, file: '01-welcome.png', width: 1600, height: 1000 });
// 1. Open Folder (the scripted chooser stands in for the native dialog ONLY; everything after is the production path) -> an EMPTY folder
add({ name: 'open_empty_to_workspace_ms', js: `(async()=>{${W}const t0=performance.now();[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'));return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 500 });
add({ name: 'preflight_ready_ms', js: `(async()=>{${W}${nav('Project')}const t0=performance.now();const ok=await ${ready};return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 500, file: '02-goal-preflight-empty-folder.png' });
add({ js: `(()=>{${goalSet('Create a utility library with a clamp(value, low, high) function and unit tests.')}})()`, wait: 500, file: '03-goal-entered.png' });
// 2. Start Run: click -> acknowledgement (banner), then the run being observed
add({ name: 'start_click_to_banner_ms', js: `(async()=>{${W}const t0=performance.now();${startBtn}.click();const ok=await W(()=>${banner});return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 200, file: '04-launching.png' });
add({ name: 'launch_record_seen_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}.dataset.stage==='running');return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 2200, file: '05-running.png' });
add({ name: 'switch_disabled_while_running', js: `(()=>{const b=document.querySelector('button[aria-label="Switch workspace"]');return [b.disabled,b.title]})()`, wait: 2500, file: '06-running-more-records-switch-disabled.png' });
add({ name: 'terminal_after_ms', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}.dataset.stage==='finished');return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 1500, file: '07-terminal-success.png' });
add({ name: 'timing_run_1', js: `(()=>${timing})()`, wait: 100 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 700, file: '08-runs-after-terminal.png' });
add({ name: 'terminal_to_timeline_ms', js: `(async()=>{${W}const ok=await W(()=>document.querySelector('[aria-label="Execution timeline"] li'));return ok})()`, wait: 600, file: '09-finished-run-inspected.png' });
// 3. an existing project (a Git repository): the SAME flow, a scripted failure
add({ name: 'open_existing_menu', js: `(async()=>{${W}document.querySelector('button[aria-label="Switch workspace"]').click();await W(()=>document.querySelector('[role=menu]'));[...document.querySelectorAll('[role=menuitem]')].find(b=>b.textContent.startsWith('Open Folder')).click();const ok=await W(()=>document.querySelector('button[aria-label="Switch workspace"]').textContent.includes('existing'));return ok})()`, wait: 600 });
add({ js: `(async()=>{${W}${nav('Project')}await ${ready};${goalSet('Add input validation to the importer. [controlled:failure]')}})()`, wait: 700, file: '10-goal-preflight-existing-project.png' });
add({ js: `(async()=>{${W}${startBtn}.click();await W(()=>${banner}.dataset.stage==='finished')})()`, wait: 1200, file: '11-terminal-failure.png' });
// 4. a run whose process is killed: nothing is sealed, so it is NOT a success; Kriya then refuses a new run until the workspace is recovered
add({ js: `(async()=>{${W}[...document.querySelectorAll('button')].find(b=>b.textContent==='Dismiss').click();${goalSet('Refactor the parser. [controlled:die]')}await W(()=>${startBtn}&&!${startBtn}.disabled);${startBtn}.click();await W(()=>${banner}.dataset.stage==='finished')})()`, wait: 1500, file: '12-terminal-interrupted.png' });
add({ name: 'preflight_after_interruption', js: `(async()=>{${W}[...document.querySelectorAll('button')].find(b=>b.textContent==='Dismiss').click();await W(()=>document.body.innerText.includes('Kriya will not start a run here yet'));return document.querySelector('[role=alert]').innerText})()`, wait: 700, file: '13-preflight-blocked-after-interruption.png' });
add({ name: 'banner_text_final', js: `(()=>document.body.innerText.length)()`, wait: 100 });

writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-m6.json'), JSON.stringify(steps, null, 1));
console.log(`capture-m6.json: ${steps.length} steps`);
