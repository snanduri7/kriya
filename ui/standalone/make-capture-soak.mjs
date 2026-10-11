#!/usr/bin/env node
/** Generates capture-soak.json (hardening Phase L): a deterministic SOAK of the real app (evidence package), no screenshots. Each cycle: switch workspace (A <-> B through the real
 *  switcher and the scripted chooser), open history, select runs, step through every tab, open a diff, toggle the drawer, and every third cycle run a CONTROLLED execution through the
 *  real launch path and Approve or Reject its review (alternating). In-page: heap, DOM size, live timers (duplicate pollers) and event-loop lag per cycle. Outside: process count, RSS and
 *  file descriptors (run-soak.sh). Duration: SOAK_MINUTES (default 30). */
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const MINUTES = Number(process.env.SOAK_MINUTES ?? 30);
const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},25);setTimeout(()=>{clearInterval(i);r(false)},ms)});const S=(ms)=>new Promise(r=>setTimeout(r,ms));`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='${label}')`;
const confirmBtn = `(${btn('Confirm approval')}||${btn('Approve without viewing diff')})`;
const body = `
${W}
const nav=(n)=>{const b=document.querySelector('nav[aria-label=Destinations] button[aria-label='+n+']');if(b)b.click()};
const tab=(n)=>{const t=[...document.querySelectorAll('[role=tab]')].find(b=>b.textContent.trim()===n);if(t)t.click()};
const panel=()=>document.querySelector('[role=tabpanel]');
const setVal=(el,v,proto)=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}))};
// live timers: a duplicate poller would show as growth here
const live=new Set(),liveTO=new Set();const si=window.setInterval.bind(window),ci=window.clearInterval.bind(window),st=window.setTimeout.bind(window),ct=window.clearTimeout.bind(window);
window.setInterval=(...a)=>{const id=si(...a);live.add(id);return id};window.clearInterval=(id)=>{live.delete(id);return ci(id)};
window.setTimeout=(f,ms,...a)=>{const id=st((...x)=>{liveTO.delete(id);return f(...x)},ms,...a);liveTO.add(id);return id};window.clearTimeout=(id)=>{liveTO.delete(id);return ct(id)};
window.__lag=[];let last=performance.now();si(()=>{const n=performance.now();window.__lag.push(Math.max(0,Math.round(n-last-50)));last=n},50);
const pct=(a,p)=>a.length?a.slice().sort((x,y)=>x-y)[Math.min(a.length-1,Math.floor(a.length*p))]:null;
const heap=()=>Math.round((performance.memory?performance.memory.usedJSHeapSize:0)/1048576);
const t0=performance.now();const END=${MINUTES}*60*1000;
const samples=[];const errors=[];const counts={cycles:0,switches:0,runsStarted:0,approvals:0,rejections:0,reviewMissed:0};
let lagFrom=0;
const step=async(name,f)=>{try{return await f()}catch(e){errors.push({cycle:counts.cycles,name,error:String(e&&e.message||e).slice(0,160)})}};
// first open (scripted chooser #1)
[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();await W(()=>document.querySelector('nav[aria-label=Destinations]'),60000);
while(performance.now()-t0<END){
  const c=counts.cycles;
  await step('switch',async()=>{const sw=document.querySelector('button[aria-label="Switch workspace"]');sw.click();await S(120);const item=[...document.querySelectorAll('[role=menuitem]')].find(x=>x.textContent.includes('Open Folder'));if(item){item.click();counts.switches++}await S(600);await W(()=>document.querySelector('nav[aria-label=Destinations]'),30000)});
  await step('history',async()=>{nav('Runs');await S(300);await W(()=>document.querySelectorAll('.ck-list button').length>0||${btn('Acquire snapshot')},20000);const a=${btn('Acquire snapshot')};if(a){a.click();await W(()=>document.querySelectorAll('.ck-list button').length>0,30000)}
    const rows=[...document.querySelectorAll('.ck-list button')];const more=${btn('Load more runs')};if(c%4===0&&more){more.click();await S(300)}
    const pick=(c%5===0)?rows.find(x=>x.textContent.includes('Implement the whole thing'))||rows[0]:rows[(c*7)%Math.max(1,rows.length)];if(pick)pick.click();await S(500)});
  await step('tabs',async()=>{for(const t of ['Validation','Changes','Context','Plan','Attempts','Evidence','Overview']){tab(t);await S(120);if(t==='Changes'){const f=panel()&&panel().querySelector('ul button');if(f){f.click();await S(150);const sd=${btn('Show diff')};if(sd){sd.click();await S(150)}}}}const d=${btn('Raw Events')};if(d){d.click();await S(150)}});
  if(c%3===1){await step('run',async()=>{nav('Workspace');await S(300);await W(()=>document.querySelector('#ck-goal')&&!document.querySelector('#ck-goal').disabled,20000);setVal(document.querySelector('#ck-goal'),'Soak cycle '+c+': add a clamp function.',HTMLTextAreaElement.prototype);await W(()=>{const b=${btn('Start Run')};return b&&!b.disabled},30000);${btn('Start Run')}.click();counts.runsStarted++;
    const got=await W(()=>document.querySelector('[aria-label="Change review"]'),60000);
    if(!got){counts.reviewMissed++}else{if(c%2===1){${btn('Approve changes')}.click();await S(150);const k=${confirmBtn};if(k)k.click();counts.approvals++}else{${btn('Reject changes')}.click();counts.rejections++}}
    await W(()=>{const b=document.querySelector('[aria-label="Active run"]');return b&&b.dataset.stage==='finished'},60000);await S(300);const dm=${btn('Dismiss')};if(dm)dm.click();await S(200)})}
  counts.cycles++;
  const lag=window.__lag.slice(lagFrom);lagFrom=window.__lag.length;
  samples.push({cycle:c,t_s:Math.round((performance.now()-t0)/1000),heap_mb:heap(),nodes:document.querySelectorAll('*').length,live_intervals:live.size,live_timeouts:liveTO.size,lag_p95:pct(lag,.95),lag_max:lag.length?Math.max(...lag):null});
  await S(150);
}
return {minutes:${MINUTES},counts,errors:errors.slice(0,20),error_count:errors.length,samples:samples.filter((s,i)=>i%Math.max(1,Math.floor(samples.length/40))===0||i===samples.length-1),first:samples[0],last:samples[samples.length-1],lag_all:{samples:window.__lag.length,p95:pct(window.__lag,.95),p99:pct(window.__lag,.99),max:Math.max(...window.__lag),over100ms:window.__lag.filter(x=>x>100).length}};`;
const steps = [{ wait: 800 }, { name: 'soak', js: `(async()=>{${body}})()`, wait: 500 }];
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'capture-soak.json'), JSON.stringify(steps, null, 1));
console.log(`capture-soak.json: ${MINUTES} minutes`);
