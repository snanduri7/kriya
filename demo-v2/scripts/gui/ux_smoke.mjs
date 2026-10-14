#!/usr/bin/env node
/** Writes the UX-correction smoke steps: node ux_smoke.mjs <out.json>.
 *  It drives the evidence-packaged desktop app over a DISPOSABLE scratch project with the product's CONTROLLED workflow body (ui/standalone/test/fixtures/controlled_kriya.py: real Kriya
 *  launch, records, polling and the REAL change-approval path; no model), through the owner journey: Open Folder -> goal -> Start Run -> what Kriya is doing -> ACTION REQUIRED ->
 *  another page with the run active -> back -> Approve & Continue -> the outcome -> history -> details. It also MEASURES where Start Run sits at several window sizes.
 *  Nothing here is a Demo V2 scenario; the scripted chooser replaces ONLY the native folder dialog. */
import { writeFileSync } from 'node:fs';

const [outFile] = process.argv.slice(2);
if (!outFile) { console.error('usage: ux_smoke.mjs <out.json>'); process.exit(2); }
const GOAL = 'Add a clamp(value, low, high) function with tests.';
const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},40);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const nav = (n) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]').click();`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;
const run = `document.querySelector('[aria-label="Current run"]')`;
const strip = `document.querySelector('[aria-label="Active run"]')`;
const review = `document.querySelector('section[aria-label="Change review"]')`;
/** Where the primary action sits: it must be fully inside the window with no scrolling, and nothing may be scrolled out of view above it. */
const layout = `(()=>{const q=(s)=>document.querySelector(s);const r=(e)=>{if(!e)return null;const b=e.getBoundingClientRect();return {top:Math.round(b.top),bottom:Math.round(b.bottom),left:Math.round(b.left),right:Math.round(b.right)}};
 const start=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Start Run');const goal=q('#ck-goal');const main=q('main');
 return {innerWidth:innerWidth,innerHeight:innerHeight,start:r(start),goal:r(goal),startFullyVisible:!!start&&start.getBoundingClientRect().bottom<=innerHeight&&start.getBoundingClientRect().top>=0&&start.getBoundingClientRect().right<=innerWidth,goalFullyVisible:!!goal&&goal.getBoundingClientRect().bottom<=innerHeight,mainScrollTop:main?main.scrollTop:null,mainScrollable:main?main.scrollHeight>main.clientHeight:null,pageScrollable:document.documentElement.scrollHeight>document.documentElement.clientHeight,runStripShown:!!q('[aria-label="Active run"]'),railLabels:[...document.querySelectorAll('nav[aria-label=Destinations] button')].map(b=>b.textContent.trim())}})()`;

const steps = [];
const add = (s) => steps.push(s);
add({ wait: 700, file: '01-no-project.png', width: 1280, height: 800 });
add({ name: 'open_folder_ms', js: `(async()=>{${W}const t0=performance.now();[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();const ok=await W(()=>document.querySelector('nav[aria-label=Destinations]'));return ok?Math.round(performance.now()-t0):'TIMEOUT'})()`, wait: 300 });
add({ name: 'landing', js: `(async()=>{${W}await W(()=>/Ready|Cannot start/.test(document.body.innerText));return {page:document.querySelector('.ck-shell').dataset.page,current:document.querySelector('nav[aria-label=Destinations] button[aria-current=page]').getAttribute('aria-label'),heading:document.querySelector('h1').innerText,hasGoal:!!document.querySelector('#ck-goal'),compactReady:document.body.innerText.match(/✓ Ready[^\\n]*/)?.[0]??null,detailsOpen:document.querySelector('details.ck-setup').open}})()`, wait: 500, file: '02-project-open.png' });
// where Start Run sits, at the spec's viewport and at the sizes a real window can have
add({ name: 'layout_1280x800', js: layout, wait: 100 });
add({ name: 'layout_1400x900', js: layout, wait: 100, width: 1400, height: 900 });
add({ name: 'layout_1280x720', js: layout, wait: 100, width: 1280, height: 720 });
add({ name: 'layout_1100x700', js: layout, wait: 100, width: 1100, height: 700 });
add({ name: 'layout_1536x960', js: layout, wait: 100, width: 1536, height: 960 });
add({ js: `(()=>{})()`, wait: 100, width: 1280, height: 800 });
// history is empty before the first run: it says so and points back to Project
add({ js: `(async()=>{${W}${nav('Runs')}await W(()=>document.body.innerText.includes('No runs yet'));})()`, wait: 400, file: '03-runs-empty.png' });
add({ name: 'runs_empty_text', js: `document.querySelector('main[aria-label="Run workspace"]').innerText + ' | ' + document.querySelector('.ck-secondary').innerText`, wait: 50 });
add({ js: `(()=>{${nav('Project')}})()`, wait: 400 });
add({ js: `(()=>{${goalSet(GOAL)}})()`, wait: 400, file: '04-goal-entered.png' });
add({ name: 'start_enabled', js: `(()=>{const s=${btn('Start Run')};return {enabled:!!s&&!s.disabled,shortcutHint:/⌘ Enter|Ctrl Enter/.test(document.body.innerText)}})()`, wait: 50 });
add({ name: 'start_click_to_current_run_ms', js: `(async()=>{${W}window.__seen=[];window.__timer=setInterval(()=>{const r=${run};const s=r&&r.dataset.state;if(s&&window.__seen[window.__seen.length-1]!==s)window.__seen.push(s)},50);const t0=performance.now();${btn('Start Run')}.click();const ok=await W(()=>${run});return ok?{ms:Math.round(performance.now()-t0),state:${run}.dataset.state,composerGone:!document.querySelector('#ck-goal')}:'TIMEOUT'})()`, wait: 200, file: '05-run-starting.png' });
// follow the run: the states shown, in order, until Kriya waits for the person (sampled; every one must come from the records)
add({ name: 'wait_editing', js: `(async()=>{${W}return await W(()=>${run}&&['PLANNING','EDITING','VALIDATING'].includes(${run}.dataset.state),30000)})()`, wait: 400, file: '05b-run-active.png' });
add({ name: 'states_until_review', js: `(async()=>{${W}await W(()=>${review},90000);clearInterval(window.__timer);return window.__seen})()`, wait: 600, file: '06-approval-waiting.png' });
add({ name: 'approval_panel', js: `(()=>{const r=${review};return r?{text:r.innerText,focusable:r.tabIndex,actionRequiredFlag:r.innerText.includes('ACTION REQUIRED'),buttons:[...r.querySelectorAll('button')].map(b=>b.textContent.trim()),runState:${run}.dataset.state,stripShown:!!${strip}}:null})()`, wait: 50 });
// another page while Kriya needs the person: the compact strip says so, outranks the page, and returns to the review
add({ js: `(async()=>{${W}${nav('Skills')}await W(()=>document.querySelector('table[aria-label=Skills]')||document.body.innerText.includes('Skills'));})()`, wait: 600, file: '07-skills-while-action-required.png' });
add({ name: 'strip_on_skills', js: `(()=>{const s=${strip};return s?{text:s.innerText,attention:s.classList.contains('ck-attention'),state:s.dataset.state,firstChildTop:Math.round(s.getBoundingClientRect().top)}:null})()`, wait: 50 });
add({ js: `(async()=>{${W}${nav('Tools')}await W(()=>${strip});})()`, wait: 400 });
add({ name: 'strip_on_tools', js: `(()=>{const s=${strip};return s?s.innerText:null})()`, wait: 50 });
add({ name: 'back_via_review', js: `(async()=>{${W}const b=[...${strip}.querySelectorAll('button')].find(x=>x.textContent.trim()==='Review');b.click();const ok=await W(()=>${review});await new Promise(x=>setTimeout(x,300));return {ok,page:document.querySelector('.ck-shell').dataset.page,focused:document.activeElement===${review}}})()`, wait: 300, file: '08-back-at-review.png' });
add({ name: 'approve_click', js: `(async()=>{${W}${btn('Approve & Continue')}.click();await W(()=>document.querySelector('[role=alertdialog]'),5000);const d=document.querySelector('[role=alertdialog]');return d?{text:d.innerText,focusedCancel:document.activeElement&&document.activeElement.textContent.trim()==='Cancel'}:'no confirmation'})()`, wait: 500, file: '09-approve-confirm.png' });
add({ name: 'approve_confirm', js: `(async()=>{${W}const c=${btn('Confirm approval')}||${btn('Approve without viewing diff')};c.click();await W(()=>!${review}||/recorded/i.test(${review}.innerText),30000);return {state:${run}.dataset.state}})()`, wait: 700, file: '10-approval-recorded.png' });
add({ name: 'states_after_approval_until_finished', js: `(async()=>{${W}const seen=[];const stop=Date.now()+90000;while(Date.now()<stop&&${run}.dataset.stage!=='finished'){const s=${run}.dataset.state;if(seen[seen.length-1]!==s)seen.push(s);await new Promise(x=>setTimeout(x,60));}return {seen,outcome:${run}.dataset.outcome}})()`, wait: 2500 });
add({ name: 'completed', js: `(async()=>{${W}await W(()=>document.querySelector('[aria-label="Changed files"]')||/could not be read|not available here/.test(${run}.innerText),20000);return ${run}.innerText})()`, wait: 600, file: '11-run-completed.png' });
add({ name: 'completed_buttons', js: `(()=>[...${run}.querySelectorAll('button')].map(b=>b.textContent.trim()))()`, wait: 50 });
add({ js: `(async()=>{${W}${nav('Runs')}await W(()=>document.querySelector('.ck-list'));})()`, wait: 600, file: '12-runs-history.png' });
add({ name: 'runs_history_text', js: `document.querySelector('.ck-secondary').innerText`, wait: 50 });
add({ js: `(async()=>{${W}const b=document.querySelector('.ck-list button');if(b)b.click();await W(()=>document.querySelector('[role=tablist][aria-label="Run documents"]'));})()`, wait: 900, file: '13-run-details-overview.png' });
add({ name: 'run_document_tabs', js: `[...document.querySelectorAll('[role=tablist][aria-label="Run documents"] [role=tab]')].map(t=>t.textContent.trim())`, wait: 50 });
add({ js: `(()=>{const d=[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.trim()==='Details');if(d)d.click();})()`, wait: 700, file: '14-run-details-under-details.png' });
add({ name: 'details_tabs', js: `[...document.querySelectorAll('[role=tablist][aria-label="Details"] [role=tab]')].map(t=>t.textContent.trim())`, wait: 50 });
add({ js: `(()=>{${nav('Project')}})()`, wait: 700, file: '15-project-after-run.png' });
add({ name: 'new_goal', js: `(async()=>{${W}${btn('New Goal')}.click();return await W(()=>document.querySelector('#ck-goal'),5000)})()`, wait: 500, file: '16-new-goal.png' });
writeFileSync(outFile, JSON.stringify(steps, null, 1));
console.log(`${outFile}: ${steps.length} steps`);
