#!/usr/bin/env node
/** Generates capture-responsive.json (hardening Phases H/I): the real packaged app (evidence package) over a real recorder-written state, every representative surface at five window
 *  sizes. At every width and surface a DOM geometry audit records: horizontal page scroll, controls clipped outside the window, elements whose text is clipped by overflow:hidden,
 *  the presence of the inspector pane and the number of focusable controls without an accessible name. Screenshots are taken at 1536, 1360 and 1100 wide. */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},25);setTimeout(()=>{clearInterval(i);r(false)},ms)});const S=(ms)=>new Promise(r=>setTimeout(r,ms));`;
const AUDIT = `window.__audit=window.__audit||[];const audit=(label)=>{const vw=window.innerWidth,vh=window.innerHeight;const clipped=[];const nameless=[];const textClipped=[];for(const el of document.querySelectorAll('button,[role=tab],a[href],input,select,textarea,summary,[tabindex]')){const r=el.getBoundingClientRect();if(r.width===0&&r.height===0)continue;const cs=getComputedStyle(el);if(cs.visibility==='hidden'||cs.display==='none')continue;if(r.right>vw+1||r.left<-1)clipped.push((el.textContent||el.getAttribute('aria-label')||el.tagName).trim().slice(0,30));const name=(el.getAttribute('aria-label')||el.textContent||el.getAttribute('title')||'').trim();if(!name&&!(el.labels&&el.labels.length)&&el.tagName!=='INPUT')nameless.push(el.tagName)}for(const el of document.querySelectorAll('div,span,li,button,td,th,h1,h2,h3,strong,pre,code')){const cs=getComputedStyle(el);if((cs.overflowX==='hidden'||cs.textOverflow==='ellipsis')&&el.scrollWidth>el.clientWidth+2&&el.clientWidth>0)textClipped.push((el.textContent||'').trim().slice(0,30))}window.__audit.push({label,vw,vh,hscroll:Math.max(0,document.documentElement.scrollWidth-vw),pageHeightOverflow:document.documentElement.scrollHeight>vh+2,clippedControls:[...new Set(clipped)].slice(0,6),namelessControls:nameless.length,textClippedByHidden:[...new Set(textClipped)].slice(0,4),inspector:!!document.querySelector('[aria-label="Inspector"],[data-module=timeline]'),nodes:document.querySelectorAll('*').length})};window.__audit_fn=audit;`;
const nav = (n) => `{const b=document.querySelector('nav[aria-label=Destinations] button[aria-label=${n}]');if(b)b.click()}`;
const tab = (n) => `{const t=[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()==='${n}');if(t)t.click()}`;
const run = (text) => `{const f=document.querySelector('input[aria-label="Filter runs"]');if(f){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(f,${JSON.stringify(text)});f.dispatchEvent(new Event('input',{bubbles:true}))}await S(250);const b=[...document.querySelectorAll('.ck-list button')].find(x=>x.textContent.includes(${JSON.stringify(text)}));if(!b)return 'ROW NOT FOUND: '+${JSON.stringify(text)};b.click()}`;
const settle = `await S(450);`;
const steps = []; const add = (s) => steps.push(s);
const SIZES = [[1536, 1024], [1440, 960], [1360, 900], [1180, 860], [1100, 820]];
const SHOT = new Set([1536, 1360, 1100]);

add({ wait: 700, width: 1100, height: 820 });
add({ name: 'install', js: `(()=>{${AUDIT}window.__audit_fn('welcome@1100');return 'ok'})()`, wait: 200, file: 'welcome-1100.png' });
add({ wait: 300, width: 1536, height: 1024 });
add({ name: 'open', js: `(async()=>{${W}document.querySelector('main[aria-label=Welcome] button').click();await W(()=>document.querySelector('nav[aria-label=Destinations]'));await W(()=>document.querySelectorAll('.ck-list button').length>0||[...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='Acquire snapshot'),30000);const a=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Acquire snapshot');if(a)a.click();await W(()=>document.querySelectorAll('.ck-list button').length>0);return document.querySelectorAll('.ck-list button').length})()`, wait: 600 });
for (const [w, h] of SIZES) {
  const f = (label) => (SHOT.has(w) ? { file: `${label}-${w}.png` } : {});
  add({ wait: 350, width: w, height: h });
  add({ name: `audit_workspace@${w}`, js: `(async()=>{${W}${nav('Workspace')}${settle}window.__audit_fn('workspace@${w}');return 'ok'})()`, wait: 150, ...f('workspace') });
  add({ name: `audit_runs@${w}`, js: `(async()=>{${W}${nav('Runs')}${run('Implement the whole thing')}await W(()=>/recorded outcome/i.test((document.querySelector('[role=tabpanel]')||{innerText:''}).innerText),60000);${settle}window.__audit_fn('runs-overview@${w}');return 'ok'})()`, wait: 150, ...f('runs-overview') });
  for (const t of ['Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence']) {
    add({ name: `audit_${t.toLowerCase()}@${w}`, js: `(async()=>{${W}${tab(t)}${settle}${t === 'Changes' ? `const f=document.querySelector('[role=tabpanel] ul button');if(f){f.click();await S(350)}` : ''}window.__audit_fn('${t.toLowerCase()}@${w}');return 'ok'})()`, wait: 150, ...f(t.toLowerCase()) });
  }
  add({ name: `audit_drawer@${w}`, js: `(async()=>{${W}const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Raw Events');if(b){b.click();await S(500)}window.__audit_fn('bottom-drawer@${w}');return 'ok'})()`, wait: 150, ...f('drawer') });
  for (const [tag, text] of [['unsealed', 'SPECIAL unsealed run'], ['tampered', 'SPECIAL tampered run'], ['digestonly', 'SPECIAL digestonly run']]) {
    add({ name: `audit_${tag}@${w}`, js: `(async()=>{${W}${run(text)}${settle}await S(400)${tag === 'digestonly' ? `;${tab('Changes')};await S(300);const q=document.querySelector('[role=tabpanel] ul button');if(q){q.click();await S(300)}` : tag === 'tampered' ? `;${tab('Validation')};await S(400)` : ''};window.__audit_fn('${tag}@${w}');return 'ok'})()`, wait: 150, ...(w === 1100 || w === 1536 ? { file: `special-${tag}-${w}.png` } : {}) });
  }
}
add({ name: 'audit_results', js: `(()=>window.__audit)()`, wait: 100 });
// a screenshot is of the LAST COMPOSITED frame: give the window time to repaint after the DOM change before capturing
for (const st of steps) if (st.file) st.wait = Math.max(st.wait ?? 0, 900);
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-responsive.json'), JSON.stringify(steps, null, 1));
console.log(`capture-responsive.json: ${steps.length} steps`);
