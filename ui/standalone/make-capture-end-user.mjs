/** Generates capture-end-user.json (end-user experience, Phases 22/23): the real packaged app (evidence package) over a real recorder-written state and the REAL Kriya, every NEW
 *  surface at five window sizes: Welcome, the New Project dialog, the readiness page of a project created through Kriya, Settings, and Runs with filters. At each width a DOM
 *  geometry audit records horizontal page scroll, controls clipped outside the window, text clipped by overflow:hidden, and focusable controls without an accessible name.
 *  Behaviour checks (focus on open, Escape closes, dialog semantics, keyboard-reachable create) run once. Screenshots at 1536, 1360 and 1100. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},25);setTimeout(()=>{clearInterval(i);r(false)},ms)});const S=(ms)=>new Promise(r=>setTimeout(r,ms));`;
const AUDIT = `window.__audit=window.__audit||[];window.__checks=window.__checks||[];const audit=(label)=>{const vw=window.innerWidth,vh=window.innerHeight;const clipped=[];const nameless=[];const textClipped=[];for(const el of document.querySelectorAll('button,[role=tab],a[href],input,select,textarea,summary,[tabindex]')){const r=el.getBoundingClientRect();if(r.width===0&&r.height===0)continue;const cs=getComputedStyle(el);if(cs.visibility==='hidden'||cs.display==='none')continue;if(r.right>vw+1||r.left<-1)clipped.push((el.textContent||el.getAttribute('aria-label')||el.tagName).trim().slice(0,30));const name=(el.getAttribute('aria-label')||el.textContent||el.getAttribute('title')||'').trim();if(!name&&!(el.labels&&el.labels.length)&&el.tagName!=='INPUT')nameless.push(el.tagName)}for(const el of document.querySelectorAll('div,span,li,button,td,th,h1,h2,h3,strong,pre,code,p')){const cs=getComputedStyle(el);if((cs.overflowX==='hidden'||cs.textOverflow==='ellipsis')&&el.scrollWidth>el.clientWidth+2&&el.clientWidth>0)textClipped.push((el.textContent||'').trim().slice(0,30))}window.__audit.push({label,vw,vh,hscroll:Math.max(0,document.documentElement.scrollWidth-vw),clippedControls:[...new Set(clipped)].slice(0,6),namelessControls:nameless.length,textClippedByHidden:[...new Set(textClipped)].slice(0,4),nodes:document.querySelectorAll('*').length})};window.__audit_fn=audit;window.__check=(name,ok,detail)=>window.__checks.push({name,ok:!!ok,detail:detail??null});`;
const btn = (text) => `[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)})`;
const type = (sel, text) => `{const f=document.querySelector(${JSON.stringify(sel)});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(f,${JSON.stringify(text)});f.dispatchEvent(new Event('input',{bubbles:true}))}`;
const SIZES = [[1536, 1024], [1440, 960], [1360, 900], [1180, 860], [1100, 820]];
const SHOT = new Set([1536, 1360, 1100]);
const steps = []; const add = (s) => steps.push(s);
const shot = (w, label) => (SHOT.has(w) ? { file: `${label}-${w}.png` } : {});

add({ wait: 700, width: 1100, height: 820 });
add({ name: 'install', js: `(()=>{${AUDIT}return 'ok'})()`, wait: 200 });
for (const [w, h] of SIZES) {
  add({ wait: 350, width: w, height: h });
  add({ name: `welcome@${w}`, js: `(async()=>{${W}await W(()=>document.querySelector('main[aria-label=Welcome]'));${'await S(300);'}window.__audit_fn('welcome@${w}');window.__check('welcome has New Project and Open Folder at ${w}',!!(${btn('New Project…')})&&!!(${btn('Open Folder…')}));return 'ok'})()`, wait: 150, ...shot(w, 'welcome') });
}
// New Project: native chooser (scripted: the first capture choice is the PARENT folder), dialog, behaviour checks
add({ wait: 300, width: 1536, height: 1024 });
add({ name: 'open_dialog', js: `(async()=>{${W}${btn('New Project…')}.click();await W(()=>document.querySelector('[role=dialog][aria-label="New Project"]'));await S(300);const d=document.querySelector('[role=dialog][aria-label="New Project"]');window.__check('dialog is modal',d.getAttribute('aria-modal')==='true');window.__check('name field focused on open',document.activeElement&&document.activeElement.id==='ck-project-name',document.activeElement&&document.activeElement.tagName);window.__check('Create disabled with no name',${btn('Create project')}.disabled);return 'ok'})()`, wait: 200 });
for (const [w, h] of SIZES) {
  add({ wait: 350, width: w, height: h });
  add({ name: `dialog@${w}`, js: `(async()=>{await new Promise(r=>setTimeout(r,250));window.__audit_fn('new-project-dialog@${w}');return 'ok'})()`, wait: 150, ...shot(w, 'new-project') });
}
add({ name: 'dialog_escape_and_reopen', js: `(async()=>{${W}const d=document.querySelector('[role=dialog][aria-label="New Project"]');d.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await S(300);window.__check('Escape closes the dialog',!document.querySelector('[role=dialog][aria-label="New Project"]'));return 'ok'})()`, wait: 200 });
add({ name: 'refused_then_created', js: `(async()=>{${W}${btn('New Project…')}.click();await W(()=>document.querySelector('[role=dialog][aria-label="New Project"]'));await S(200);${type('#ck-project-name', '../escape')}await S(100);${btn('Create project')}.click();await W(()=>document.querySelector('#ck-project-error'),20000);const err=document.querySelector('#ck-project-error');window.__check('invalid name refused with Kriya\\'s code',/invalid_name/.test(err.textContent),err.textContent.slice(0,120));window.__check('dialog stays open after a refusal',!!document.querySelector('[role=dialog][aria-label="New Project"]'));window.__audit_fn('new-project-error@1536');${type('#ck-project-name', 'Audit Project ü')}await S(100);const f=document.querySelector('#ck-project-name');f.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);await S(500);window.__check('project opened after Enter',!!document.querySelector('nav[aria-label=Destinations]'));window.__check('dialog closed after create',!document.querySelector('[role=dialog][aria-label="New Project"]'));return 'ok'})()`, wait: 600 });
// readiness page of the new project
for (const [w, h] of SIZES) {
  add({ wait: 350, width: w, height: h });
  add({ name: `workspace@${w}`, js: `(async()=>{${W}const b=document.querySelector('nav[aria-label=Destinations] button[aria-label=Workspace]');if(b)b.click();await W(()=>document.querySelector('section[aria-label=Readiness]'),30000);await W(()=>/Answering|Not reachable|not served|Not checked/.test(document.querySelector('section[aria-label=Readiness]').innerText),20000);for(const d of document.querySelectorAll('details.ck-setup'))d.open=true;await S(500);window.__audit_fn('readiness@${w}');return 'ok'})()`, wait: 150, ...shot(w, 'readiness') });
}
add({ name: 'readiness_content', js: `(()=>{const r=document.querySelector('section[aria-label=Readiness]').innerText;window.__check('readiness lists Git',/Git \\(needed by every run\\)/.test(r));window.__check('readiness lists Model server',/Model server/.test(r));window.__check('routing table has 7 roles',document.querySelectorAll('table[aria-label="Model routing"] tbody tr').length===7);window.__check('skills say automatic',/Selected automatically by Kriya/.test(document.body.innerText));window.__check('no control to edit skills/tools',![...document.querySelectorAll('details.ck-setup')].slice(0,3).some(d=>d.querySelector('input,textarea,select')));return 'ok'})()`, wait: 100 });
// Settings
for (const [w, h] of SIZES) {
  add({ wait: 350, width: w, height: h });
  add({ name: `settings@${w}`, js: `(async()=>{${W}const b=document.querySelector('nav[aria-label=Destinations] button[aria-label=Settings]');if(b)b.click();await S(500);window.__audit_fn('settings@${w}');window.__check('settings has no free-text field at ${w}',document.querySelectorAll('main input[type=text],main textarea').length===0);return 'ok'})()`, wait: 150, ...shot(w, 'settings') });
}
// Runs with filters in the existing (big) workspace: open it through the toolbar switcher (second scripted choice)
add({ wait: 300, width: 1536, height: 1024 });
add({ name: 'open_big', js: `(async()=>{${W}document.querySelector('button[aria-label="Switch workspace"]').click();await S(200);[...document.querySelectorAll('[role=menuitem]')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();await W(()=>{const b=document.querySelector('nav[aria-label=Destinations] button[aria-label=Runs]');return b&&!document.querySelector('.ck-state[role=status]')},60000);const b=document.querySelector('nav[aria-label=Destinations] button[aria-label=Runs]');if(b)b.click();await W(()=>document.querySelectorAll('.ck-list button').length>0||[...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='Acquire snapshot'),30000);const a=${btn('Acquire snapshot')};if(a)a.click();await W(()=>document.querySelectorAll('.ck-list button').length>0,60000);return document.querySelectorAll('.ck-list button').length})()`, wait: 600 });
for (const [w, h] of SIZES) {
  add({ wait: 350, width: w, height: h });
  add({ name: `runs_filters@${w}`, js: `(async()=>{${W}const sel=document.querySelector('select[aria-label=Status]');sel.value='success';sel.dispatchEvent(new Event('change',{bubbles:true}));await S(300);const status=document.querySelector('[role=status][aria-live],.ck-secondary [role=status]');window.__audit_fn('runs-filters@${w}');window.__check('filter shows a count at ${w}',/of \\d+ loaded run\\(s\\) match/.test(document.querySelector('.ck-secondary').innerText),document.querySelector('.ck-secondary').innerText.slice(-120));sel.value='all';sel.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()`, wait: 150, ...shot(w, 'runs-filters') });
}
add({ name: 'results', js: `(()=>({audit:window.__audit,checks:window.__checks}))()`, wait: 100 });
for (const st of steps) if (st.file) st.wait = Math.max(st.wait ?? 0, 900);
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-end-user.json'), JSON.stringify(steps, null, 1));
console.log(`capture-end-user.json: ${steps.length} steps`);
