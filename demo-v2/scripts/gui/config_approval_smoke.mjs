#!/usr/bin/env node
/** Writes the configuration-approval smoke steps: node config_approval_smoke.mjs <out.json>.
 *  It drives the evidence-packaged desktop app over a DISPOSABLE scratch project whose kriya.yaml carries operator-controlled settings (one of them an MCP server with a SECRET env value
 *  and a URL with credentials, to prove neither is ever shown), with the product's CONTROLLED workflow body (no model): Open Folder -> ACTION REQUIRED -> goal typed -> Review & Approve ->
 *  Approve Configuration -> readiness re-read -> Ready, goal still there, Start Run enabled -> Start Run. No terminal step exists in this journey. Nothing here is a Demo V2 scenario. */
import { writeFileSync } from 'node:fs';

const [outFile] = process.argv.slice(2);
if (!outFile) { console.error('usage: config_approval_smoke.mjs <out.json>'); process.exit(2); }
const GOAL = 'Add a clamp(value, low, high) function with tests.';
const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},40);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;
const card = `document.querySelector('section[aria-label="Configuration approval"]')`;
const dlg = `document.querySelector('[role=dialog][aria-label="Configuration approval"]')`;
const run = `document.querySelector('[aria-label="Current run"]')`;
const layout = `(()=>{const s=${btn('Start Run')};const b=s&&s.getBoundingClientRect();return {innerWidth:innerWidth,innerHeight:innerHeight,startBottom:b?Math.round(b.bottom):null,startFullyVisible:!!b&&b.top>=0&&b.bottom<=innerHeight}})()`;

const steps = [];
const add = (s) => steps.push(s);
add({ wait: 600, file: '00-no-project.png', width: 1280, height: 800 });
add({ name: 'open_folder', js: `(async()=>{${W}[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();return await W(()=>${card}&&/operator-controlled/.test(${card}.innerText),30000)})()`, wait: 600, file: '01-project-approval-required.png' });
add({ name: 'approval_required_card', js: `(()=>{const c=${card};const composer=document.querySelector('section[aria-label="Start a run"]');return {text:c?c.innerText:null,reviewButton:!!${btn('Review & Approve')},cannotStartShown:/Cannot start a run/.test(document.body.innerText),terminalShown:/kriya authority approve|in a terminal/.test((c?c.innerText:'')+(composer?composer.innerText:'')),page:document.querySelector('.ck-shell').dataset.page,startDisabled:!!${btn('Start Run')}&&${btn('Start Run')}.disabled,startReason:(document.querySelector('#ck-start-why')||{}).innerText}})()`, wait: 50 });
add({ name: 'layout_1280x800', js: layout, wait: 100 });
add({ name: 'layout_1400x900', js: layout, wait: 100, width: 1400, height: 900 });
add({ name: 'layout_1100x700', js: layout, wait: 100, width: 1100, height: 700 });
add({ js: `(()=>{})()`, wait: 100, width: 1280, height: 800 });
add({ js: `(()=>{${goalSet(GOAL)}})()`, wait: 300 });
add({ name: 'review_open', js: `(async()=>{${W}${btn('Review & Approve')}.click();await W(()=>${dlg}&&/Protected settings/.test(${dlg}.innerText)&&${btn('Approve Configuration')},20000);const d=${dlg};return {found:!!d,text:d?d.innerText:null,focusOnCancel:document.activeElement&&document.activeElement.textContent.trim()==='Cancel',approveEnabled:!!${btn('Approve Configuration')}&&!${btn('Approve Configuration')}.disabled}})()`, wait: 500, file: '02-review-configuration.png' });
add({ name: 'review_secret_check', js: `(()=>{const t=document.body.innerText+document.body.innerHTML;return {secretShown:/tok-9f8e7d6c5b4a|hunter2|operator:hunter|\\/usr\\/local\\/bin/.test(t)}})()`, wait: 50 });
add({ name: 'approve_click', js: `(async()=>{${W}${btn('Approve Configuration')}.click();const seen=[];const stop=Date.now()+30000;while(Date.now()<stop&&${dlg}){await new Promise(r=>setTimeout(r,25));}return {dialogClosed:!${dlg}}})()`, wait: 60, file: '03-approved-refreshing.png' });
add({ name: 'ready_after_approval', js: `(async()=>{${W}const ok=await W(()=>!${card}&&/✓ Ready/.test(document.body.innerText)&&!!${btn('Start Run')}&&!${btn('Start Run')}.disabled,30000);return {ok,note:/Configuration approved/.test(document.body.innerText),goalKept:(document.querySelector('#ck-goal')||{}).value===${JSON.stringify(GOAL)},startEnabled:!!${btn('Start Run')}&&!${btn('Start Run')}.disabled,approvalCardGone:!${card},headerBadge:(document.querySelector('.ck-project-head')||{}).innerText}})()`, wait: 500, file: '04-ready-start-enabled.png' });
add({ name: 'start_click', js: `(async()=>{${W}${btn('Start Run')}.click();return await W(()=>${run},30000)})()`, wait: 800, file: '05-run-started.png' });
add({ name: 'finished', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);return {ok,state:${run}?${run}.dataset.state:null}})()`, wait: 700, file: '06-run-completed.png' });
writeFileSync(outFile, JSON.stringify(steps, null, 1));
console.log(`${outFile}: ${steps.length} steps`);
