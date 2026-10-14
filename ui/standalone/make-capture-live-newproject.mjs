#!/usr/bin/env node
/** capture-live-newproject.json: the ONE live smoke of the end-user path (end-user experience, Phase 28). The PACKAGED evidence app, the real read process, the real `kriya generate`
 *  workflow and the already-configured local model. Flow: Welcome -> New Project (scripted chooser stands in for the native dialog ONLY) -> Kriya creates the folder -> readiness ->
 *  goal -> Start -> leave to Settings -> "Return to run" -> candidate and gates -> "Go to review" -> read Changes and Validation -> Approve -> durable outcome. Every step guards on what is
 *  really on screen: a run that never reaches a review is recorded as exactly that, never forced. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const MAX_MS = Number(process.env.LIVE_MAX_MS ?? 45 * 60 * 1000);
const W = `const W=(f,ms=${MAX_MS})=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},50);setTimeout(()=>{clearInterval(i);r(false)},ms)});const S=(ms)=>new Promise(r=>setTimeout(r,ms));`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const DEEP = ['Context', 'Plan', 'Attempts', 'Evidence'];
const tab = (n) => `{const find=(x)=>[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()===x);${DEEP.includes(n) ? "const d=find('Details');if(d)d.click();" : ''}const t=find('${n}');if(t)t.click();}`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const banner = `document.querySelector('[aria-label="Current run"]')`;
const review = `document.querySelector('[aria-label="Change review"]')`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;
const confirmBtn = `([...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Confirm approval')||[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Approve without viewing diff'))`;
const GOAL = 'Create greet.py with a function greet(name) that returns "Hello, <name>!" and test_greet.py with two pytest tests for it.';
const steps = []; const add = (s) => steps.push(s);
add({ wait: 800, file: '01-welcome.png', width: 1536, height: 960 });
add({ name: 'new_project_dialog', js: `(async()=>{${W}${btn('New Project…')}.click();const ok=await W(()=>document.querySelector('[role=dialog][aria-label="New Project"]'),30000);return ok?document.querySelector('[role=dialog]').innerText:'TIMEOUT'})()`, wait: 600, file: '02-new-project.png' });
add({ name: 'create_project', js: `(async()=>{${W}const f=document.querySelector('#ck-project-name');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(f,'hello-greenfield');f.dispatchEvent(new Event('input',{bubbles:true}));await S(150);const t0=performance.now();${btn('Create project')}.click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 600 });
add({ js: `(()=>{${nav('Project')}})()`, wait: 800 });
add({ name: 'readiness_text', js: `(async()=>{${W}await W(()=>document.querySelector('section[aria-label=Readiness]')&&/Answering|Not reachable|not served|Not checked/.test(document.querySelector('section[aria-label=Readiness]').innerText),60000);return document.querySelector('section[aria-label=Readiness]').innerText})()`, wait: 400, file: '03-readiness.png' });
add({ js: `(()=>{${goalSet(GOAL)}})()`, wait: 500, file: '04-goal-entered.png' });
add({ name: 'start_click_to_banner_ms', js: `(async()=>{${W}const t0=performance.now();${btn('Start Run')}.click();const ok=await W(()=>${banner},60000);return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 400, file: '05-launching.png' });
add({ name: 'wander_then_return', js: `(async()=>{${W}${nav('Settings')}await S(800);const r=${btn('Return to run')};if(!r)return 'NO RETURN BUTTON (run already finished?)';r.click();await S(800);return {page:document.querySelector('.ck-shell').getAttribute('data-page')}})()`, wait: 600, file: '06-returned-to-run.png' });
add({ name: 'wait_review_or_finish', js: `(async()=>{${W}await W(()=>${review}||(${banner}&&${banner}.dataset.stage==='finished'));return ${review}?'REVIEW_PENDING':'FINISHED_WITHOUT_REVIEW:'+${banner}.dataset.outcome})()`, wait: 1500, file: '07-review-or-finish.png' });
add({ name: 'go_to_review', js: `(async()=>{${W}if(!${review})return 'no review';${nav('Settings')}await S(500);const g=[...document.querySelectorAll('button')].find(b=>/approval waiting/.test(b.textContent));if(!g)return 'NO INDICATOR';g.click();await S(700);return {focused:document.activeElement&&document.activeElement.getAttribute('aria-label')}})()`, wait: 600 });
add({ name: 'review_panel_text', js: `(()=>${review}?${review}.innerText:null)()`, wait: 200 });
add({ name: 'open_changes_first_file', js: `(async()=>{${W}${btn('Review changes')}.click();await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>20,30000);const b=document.querySelector('[role=tabpanel] ul button');if(b){b.click();await S(800)}return document.querySelector('[role=tabpanel]').innerText})()`, wait: 1500, file: '08-review-changes.png' });
add({ name: 'open_validation', js: `(async()=>{${W}${tab('Validation')}await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>20,30000);return document.querySelector('[role=tabpanel]').innerText})()`, wait: 1500, file: '09-review-validation.png' });
add({ name: 'back_to_review', js: `(async()=>{${W}const strip=document.querySelector('[aria-label="Active run"]');const r=strip&&[...strip.querySelectorAll('button')].find(b=>b.textContent.trim()==='Review');if(r)r.click();return await W(()=>${review},10000)})()`, wait: 600, file: '08b-back-to-review.png' });
add({ name: 'approve_click', js: `(async()=>{${W}if(!${review})return 'no review';${btn('Approve & Continue')}.click();await W(()=>${confirmBtn},5000);return 'confirm shown'})()`, wait: 600, file: '10-approve-confirm.png' });
add({ name: 'approve_confirm', js: `(async()=>{${W}const c=${confirmBtn};if(!c)return 'no confirm';const t0=performance.now();c.click();await W(()=>!${review}||/recorded/i.test(${review}.innerText),30000);return Math.round(performance.now()-t0)})()`, wait: 800, file: '11-approval-recorded.png' });
add({ name: 'wait_terminal', js: `(async()=>{${W}const t0=performance.now();const ok=await W(()=>${banner}&&${banner}.dataset.stage==='finished');return ok?{ms:Math.round(performance.now()-t0),outcome:${banner}.dataset.outcome}:'TIMEOUT'})()`, wait: 2500, file: '12-terminal.png' });
add({ name: 'terminal_banner_text', js: `(()=>${banner}.innerText)()`, wait: 100 });
add({ js: `(()=>{${nav('Runs')}})()`, wait: 1500, file: '13-run-list.png' });
for (const [i, name] of ['Overview', 'Validation', 'Changes', 'Context', 'Evidence'].entries()) {
  add({ name: `tab_${name.toLowerCase()}_text`, js: `(async()=>{${W}${tab(name)}await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>10,30000);return document.querySelector('[role=tabpanel]').innerText})()`, wait: 900, file: `${14 + i}-final-${name.toLowerCase()}.png` });
}
add({ name: 'runs_list_text', js: `(()=>document.querySelector('.ck-list')?document.querySelector('.ck-list').innerText:null)()`, wait: 100 });
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-live-newproject.json'), JSON.stringify(steps, null, 1));
console.log(`capture-live-newproject.json: ${steps.length} steps (max wait ${MAX_MS} ms)`);
