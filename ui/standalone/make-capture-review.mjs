#!/usr/bin/env node
/** Generates capture-review.json: the final live GUI smoke (GUI final). Drives the PACKAGED Kriya.app over a disposable scratch project with the real workflow and a real local
 *  model, THROUGH the human review: Start -> (model candidate, gates) -> Approval required -> read the review -> open Changes and Validation -> Approve -> Kriya resumes ->
 *  the durable outcome. The scripted chooser replaces ONLY the native dialog call. Every step guards on what is actually on screen, so a run that never reaches a review
 *  (the model produced a bad candidate and Kriya safe-failed) is recorded as exactly that, never forced. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const MAX_MS = Number(process.env.LIVE_MAX_MS ?? 45 * 60 * 1000);
const W = `const W=(f,ms=${MAX_MS})=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},50);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const tab = (n) => `{const t=[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()==='${n}');if(t)t.click();}`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Active run"]')`;
const review = `document.querySelector('[aria-label="Change review"]')`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='${label}')`;
const GOAL = 'Update greet(name) so it returns "Hello, <name>!" while preserving the public function signature and keeping the project tests passing.';
const steps = [];
const add = (s) => steps.push(s);
add({ wait: 800, file: '01-welcome.png', width: 1536, height: 960 });
add({ name: 'open_folder_to_workspace_ms', js: `(async()=>{${W}const t0=performance.now();document.querySelector('main[aria-label=Welcome] button').click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 600 });
add({ js: `(()=>{${nav('Workspace')}})()`, wait: 800 });
add({ name: 'preflight_text', js: `(async()=>{${W}await W(()=>document.body.innerText.includes('Configuration digest'),60000);return document.querySelector('#ck-goal').closest('section').innerText})()`, wait: 400, file: '02-workspace-goal-preflight.png' });
add({ js: `(()=>{${goalSet(GOAL)}})()`, wait: 500, file: '03-goal-entered.png' });
add({ name: 'start_click_to_banner_ms', js: `(async()=>{${W}const t0=performance.now();${btn('Start Run')}.click();const ok=await W(()=>${banner},60000);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 400, file: '04-launching.png' });
add({ name: 'wait_review_or_finish', js: `(async()=>{${W}await W(()=>${review}||(${banner}&&${banner}.dataset.stage==='finished'));return ${review}?'REVIEW_PENDING':'FINISHED_WITHOUT_REVIEW:'+${banner}.dataset.outcome})()`, wait: 1500, file: '05-review-or-finish.png' });
add({ name: 'review_panel_text', js: `(()=>${review}?${review}.innerText:null)()`, wait: 200 });
add({ name: 'banner_text_at_review', js: `(()=>${banner}?${banner}.innerText:null)()`, wait: 200 });
add({ name: 'open_changes', js: `(async()=>{${W}if(!${review})return 'no review';${btn('Review changes')}.click();await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>20,30000);return document.querySelector('[role=tabpanel]').innerText})()`, wait: 1500, file: '06-review-changes.png' });
add({ name: 'open_changes_first_file', js: `(async()=>{${W}if(!${review})return 'no review';const b=document.querySelector('[role=tabpanel] ul button');if(b){b.click();await W(()=>/diff|@@|\\+\\+\\+/.test(document.querySelector('[role=tabpanel]').innerText),20000)}return document.querySelector('[role=tabpanel]').innerText})()`, wait: 1200, file: '07-review-changes-detail.png' });
add({ name: 'open_validation', js: `(async()=>{${W}if(!${review})return 'no review';${btn('Review validation')}.click();await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>20,30000);return document.querySelector('[role=tabpanel]').innerText})()`, wait: 1500, file: '08-review-validation.png' });
add({ name: 'approve_click', js: `(async()=>{${W}if(!${review})return 'no review';const t0=performance.now();${btn('Approve changes')}.click();await W(()=>${btn('Confirm approval')},5000);return 'confirm shown'})()`, wait: 600, file: '09-approve-confirm.png' });
add({ name: 'approve_confirm', js: `(async()=>{${W}const c=${btn('Confirm approval')};if(!c)return 'no confirm';const t0=performance.now();c.click();await W(()=>!${review}||/recorded/i.test(${review}.innerText),30000);return Math.round(performance.now()-t0)})()`, wait: 800, file: '10-approval-recorded.png' });
add({ name: 'wait_terminal', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}&&${banner}.dataset.stage==='finished');return ok?{ms:Math.round(performance.now()-t0),outcome:${banner}.dataset.outcome}:'TIMEOUT'})()`, wait: 2500, file: '11-terminal.png' });
add({ name: 'terminal_banner_text', js: `(()=>${banner}.innerText)()`, wait: 100 });
add({ name: 'timing', js: `(()=>JSON.parse(${banner}.dataset.timing))()`, wait: 100 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 1500, file: '12-run-overview.png' });
for (const [i, name] of ['Overview', 'Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence'].entries()) {
  add({ name: `tab_${name.toLowerCase()}_text`, js: `(async()=>{${W}${tab(name)}await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>10,30000);return document.querySelector('[role=tabpanel]').innerText})()`, wait: 900, file: `${13 + i}-final-${name.toLowerCase()}.png` });
}
add({ name: 'runs_list_text', js: `(()=>document.querySelector('.ck-list')?document.querySelector('.ck-list').innerText:null)()`, wait: 100 });
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-review.json'), JSON.stringify(steps, null, 1));
console.log(`capture-review.json: ${steps.length} steps (max wait ${MAX_MS} ms)`);
