#!/usr/bin/env node
/** Writes the PRODUCT-UX-COMPLETION smoke steps: node product_ux_steps.mjs greenfield|brownfield <out.json>.
 *  They drive the evidence-packaged desktop app with the product's CONTROLLED workflow body (no model). Everything around the body is the real Kriya: New Project, Project Setup
 *  (`project.initialize`), the configuration approval (scripted NATIVE confirmation), the launch, the change review with the approval-scoped diff, the result screens.
 *  CONTROLLED: what the "model" produced (the files, the program output) is scripted test data, never a claim about a real run. */
import { writeFileSync } from 'node:fs';

const [kind, outFile] = process.argv.slice(2);
if (!['greenfield', 'brownfield'].includes(kind) || !outFile) { console.error('usage: product_ux_steps.mjs greenfield|brownfield <out.json>'); process.exit(2); }
const GOAL = kind === 'greenfield'
  ? "Create a small Java 17 Maven application named hello-kriya that prints 'Hello from Kriya' and includes a unit test. Make the application runnable."
  : 'Fix a bug in the existing Spring Boot driver service: `DefaultDriverService.delete(Long driverId)` marks a driver as deleted in memory, but the soft-delete is silently lost. Fix only this method.';
const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},40);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const S = `const S=(ms)=>new Promise(r=>setTimeout(r,ms));`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;
const nav = (label) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${JSON.stringify(label)}]')`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const run = `document.querySelector('[aria-label="Current run"]')`;
const dlg = (name) => `document.querySelector('[role=dialog][aria-label=${JSON.stringify(name)}]')`;
const region = (name) => `document.querySelector('[aria-label=${JSON.stringify(name)}]')`;
const reveal = (sel) => `(()=>{const e=${sel};if(e)e.scrollIntoView({block:'start'});return !!e})()`;
/** What a person could read on screen without expanding anything: visible text only, for the screenshot review (closed <details> bodies are not visible). */
const visibleText = `(()=>{const out=[];const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while((n=w.nextNode())){const t=n.textContent.trim();if(!t)continue;const e=n.parentElement;if(!e||e.closest('details:not([open])')&&!e.closest('summary'))continue;const r=e.getBoundingClientRect();if(r.width===0||r.height===0)continue;const cs=getComputedStyle(e);if(cs.visibility==='hidden'||cs.display==='none')continue;out.push(t)}return out.join(' | ').slice(0,6000)})()`;
const steps = [];
const add = (s) => steps.push(s);
const shot = (name, file, extra = {}) => add({ name, file, js: `(()=>${visibleText})()`, wait: 300, ...extra });

const DIFF_HELLO = {
  path: 'pom.xml', diff: '--- a/pom.xml\n+++ b/pom.xml\n@@ -0,0 +1,22 @@\n+<project xmlns="http://maven.apache.org/POM/4.0.0">\n+  <modelVersion>4.0.0</modelVersion>\n+  <groupId>dev.kriya</groupId>\n+  <artifactId>hello-kriya</artifactId>\n+  <version>1.0.0</version>\n+  <properties>\n+    <maven.compiler.release>17</maven.compiler.release>\n+  </properties>\n+  <dependencies>\n+    <dependency>\n+      <groupId>org.junit.jupiter</groupId>\n+      <artifactId>junit-jupiter</artifactId>\n+      <version>5.10.2</version>\n+      <scope>test</scope>\n+    </dependency>\n+  </dependencies>\n+</project>\n',
};
const DIFFS_GREEN = [DIFF_HELLO,
  { path: 'src/main/java/dev/kriya/App.java', diff: '--- a/src/main/java/dev/kriya/App.java\n+++ b/src/main/java/dev/kriya/App.java\n@@ -0,0 +1,9 @@\n+package dev.kriya;\n+\n+public class App {\n+    public static String greeting() { return "Hello from Kriya"; }\n+\n+    public static void main(String[] args) {\n+        System.out.println(greeting());\n+    }\n+}\n' },
  { path: 'src/test/java/dev/kriya/AppTest.java', diff: '--- a/src/test/java/dev/kriya/AppTest.java\n+++ b/src/test/java/dev/kriya/AppTest.java\n@@ -0,0 +1,10 @@\n+package dev.kriya;\n+\n+import static org.junit.jupiter.api.Assertions.assertEquals;\n+import org.junit.jupiter.api.Test;\n+\n+class AppTest {\n+    @Test\n+    void greets() { assertEquals("Hello from Kriya", App.greeting()); }\n+}\n' }];
const DIFFS_BROWN = [{ path: 'src/main/java/com/myapp/service/driver/DefaultDriverService.java',
  diff: '--- a/src/main/java/com/myapp/service/driver/DefaultDriverService.java\n+++ b/src/main/java/com/myapp/service/driver/DefaultDriverService.java\n@@ -41,6 +41,7 @@\n     public void delete(Long driverId) {\n         Driver driver = findById(driverId);\n         driver.setDeleted(true);\n+        driverRepository.save(driver);\n     }\n \n     @Override\n' }];
export const SCRIPTS = {
  greenfield: { outcome: 'review', step_ms: 1500, post_approval_ms: 4000, diffs: DIFFS_GREEN, compiled_goal: true, rich_events: true,
    review_reason: 'Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe new project creates a runnable hello-kriya application, prints the requested greeting, and includes a unit test that checks it.',
    gate_outcomes: [{ attempt: 1, type: 'compile', success: true, output: 'ok' }, { attempt: 1, type: 'regression_test', success: true, output: 'Tests run: 1' },
      { attempt: 1, type: 'run_verification', success: true, output: 'ran', steps: [{ command: ['java', '-cp', 'target/classes', 'dev.kriya.App'], exit_code: 0, stdout: 'Hello from Kriya\n', stderr: '', timed_out: false }] }] },
  brownfield: { outcome: 'review', step_ms: 1500, post_approval_ms: 3000, diffs: DIFFS_BROWN, compiled_goal: true, rich_events: true,
    review_reason: 'Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe soft-delete flag was changed in memory but never persisted; saving the driver after setting the flag fixes it.',
    gate_outcomes: [{ attempt: 1, type: 'compile', success: true, output: 'ok' }, { attempt: 1, type: 'goal_spec_compliance', success: true, output: 'ok' }, { attempt: 1, type: 'regression_test', success: true, output: 'Tests run: 53' }] },
}[kind];

add({ wait: 700, file: kind === 'greenfield' ? '01-launch-no-project.png' : 'b00-launch-no-project.png', width: 1280, height: 800, name: 'welcome', js: `(()=>${visibleText})()` });
if (kind === 'greenfield') {
  // New Project -> setup
  add({ name: 'new_project', js: `(async()=>{${W}${btn('New Project…')}.click();return await W(()=>${dlg('New Project')},20000)})()`, wait: 400 });
  add({ name: 'create_project', js: `(async()=>{${W}const i=document.querySelector('#ck-project-name');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'hello-kriya');i.dispatchEvent(new Event('input',{bubbles:true}));await new Promise(r=>setTimeout(r,200));${btn('Create project')}.click();return await W(()=>${region('Project setup')},30000)})()`, wait: 500 });
  shot('setup_offered', '02-greenfield-project-needs-setup.png');
  add({ name: 'setup_advanced', js: `(()=>{const d=${region('Project setup')}.querySelector('details');d.open=true;return true})()`, wait: 300 });
  shot('setup_review', '03-project-setup-review.png');
  add({ name: 'create_setup', js: `(async()=>{${W}${btn('Create Project Configuration')}.click();return await W(()=>${region('Configuration approval')}&&/ACTION REQUIRED/.test(${region('Configuration approval')}.innerText),30000)})()`, wait: 500 });
  shot('config_approval_required', '04-config-approval-required.png');
  add({ name: 'review_open', js: `(async()=>{${W}${btn('Review & Approve')}.click();return await W(()=>${dlg('Configuration approval')}&&/Protected settings/.test(${dlg('Configuration approval')}.innerText)&&${btn('Continue to Approval…')},20000)})()`, wait: 500 });
  shot('config_approval_review', '05-config-approval-review.png');
  add({ name: 'native_approve', js: `(async()=>{${W}${btn('Continue to Approval…')}.click();return await W(()=>!${dlg('Configuration approval')}&&/✓ Ready/.test(document.body.innerText),30000)})()`, wait: 700 });
  shot('project_ready', '06-project-ready.png');
}
add({ name: 'open_folder', js: kind === 'greenfield' ? '(()=>true)()' : `(async()=>{${W}[...document.querySelectorAll('main[aria-label=Welcome] button')].find(x=>/^Open Folder/.test(x.textContent.trim())).click();return await W(()=>document.querySelector('#ck-goal'),30000)})()`, wait: 500 });
add({ name: 'goal', js: `(async()=>{${W}await W(()=>document.querySelector('#ck-goal'),30000);${goalSet(GOAL)}return await W(()=>${btn('Start Run')}&&!${btn('Start Run')}.disabled,30000)})()`, wait: 400 });
shot('goal_entered', kind === 'greenfield' ? '07-goal-entered-start-visible.png' : 'b01-goal-entered.png', { js: `(()=>{const s=${btn('Start Run')};const b=s&&s.getBoundingClientRect();return {startBottom:b?Math.round(b.bottom):null,innerHeight,startVisible:!!b&&b.top>=0&&b.bottom<=innerHeight,text:${visibleText}}})()` });
add({ name: 'start', js: `(async()=>{${W}${btn('Start Run')}.click();return await W(()=>${run},30000)})()`, wait: 100 });
if (kind === 'greenfield') {
  add({ name: 'planning', js: `(async()=>{${W}const ok=await W(()=>${run}&&['PLANNING','STARTING'].includes(${run}.dataset.state),20000);return {ok,state:${run}&&${run}.dataset.state,text:${visibleText}}})()`, wait: 150, file: '08-run-planning.png' });
  add({ name: 'editing', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.state==='EDITING',30000);return {ok,state:${run}&&${run}.dataset.state,text:${visibleText}}})()`, wait: 150, file: '09-run-editing.png' });
}
add({ name: 'review_shown', js: `(async()=>{${W}const ok=await W(()=>${region('Change review')}&&${region('Change review')}.querySelector('[data-testid=proposed-change]'),90000);const r=${region('Change review')};return {ok,approveButton:!!${btn('Approve & Continue')},approveWithoutViewing:/without viewing/i.test(document.body.innerText),diffRows:r?r.querySelectorAll('[data-diff-row]').length:0,text:${visibleText}}})()`, wait: 600, file: kind === 'greenfield' ? '10-change-approval-with-diff.png' : 'b02-change-approval-with-diff.png' });
if (kind === 'greenfield') {
  add({ name: 'skills_while_active', js: `(async()=>{${W}${nav('Skills')}.click();await W(()=>document.querySelector('[aria-label="Active run"]'),20000);const s=document.querySelector('[aria-label="Active run"]');return {strip:s?s.innerText:null,text:${visibleText}}})()`, wait: 500, file: '18-skills-while-run-active.png' });
  add({ name: 'return_to_run', js: `(async()=>{${W}const s=document.querySelector('[aria-label="Active run"]');[...s.querySelectorAll('button')].find(b=>/Review|Return to Run/.test(b.textContent)).click();return await W(()=>${region('Change review')},20000)})()`, wait: 400 });
}
add({ name: 'approve', js: `(async()=>{${W}${btn('Approve & Continue')}.click();return await W(()=>${run}&&/Finishing|final checks/i.test(${run}.innerText),30000)})()`, wait: 300 });
shot('finishing', kind === 'greenfield' ? '11-validating-final-checks.png' : 'b03-finishing.png', { js: `(()=>({state:${run}&&${run}.dataset.state,text:${visibleText}}))()` });
add({ name: 'completed', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);await W(()=>${run}.querySelector('[data-testid=applied-statement]')&&${run}.querySelector('ul[aria-label=Verification]'),20000);await new Promise(r=>setTimeout(r,800));return {ok,outcome:${run}.dataset.outcome,application:!!${run}.querySelector('[data-testid=application-outcome]'),output:(${run}.querySelector('[aria-label="Application output"]')||{}).innerText||null,applied:(${run}.querySelector('[data-testid=applied-statement]')||{}).innerText||null,text:${visibleText}}})()`, wait: 600, file: kind === 'greenfield' ? '13-greenfield-completed-with-output.png' : '12-completed-brownfield-result.png' });
if (kind === 'greenfield') {
  add({ name: 'runs_history', js: `(async()=>{${W}${nav('Runs')}.click();await W(()=>document.querySelector('.ck-runtitle'),30000);const titles=[...document.querySelectorAll('.ck-runtitle')].map(e=>e.textContent);return {titles,internalPromptShown:/Authoritative Goal|unmediated user request/.test(document.body.innerText),text:${visibleText}}})()`, wait: 500, file: '14-run-history.png' });
  add({ name: 'select_run', js: `(async()=>{${W}document.querySelector('.ck-list li button').click();await W(()=>document.querySelector('[aria-label="Run result"]'),30000);return {title:(document.querySelector('[data-testid=run-title]')||{}).textContent,internalPromptShown:/Authoritative Goal|unmediated user request/.test(document.body.innerText),text:${visibleText}}})()`, wait: 600, file: '14b-run-overview.png' });
  add({ name: 'verification_tab', js: `(async()=>{${W}[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.trim()==='Verification').click();await W(()=>document.querySelector('[aria-label="How Kriya knows this works"]'),30000);return {text:${visibleText}}})()`, wait: 600, file: '15-verification.png' });
  add({ name: 'trust', js: `(()=>{const e=document.querySelector('[aria-label="Why you can trust this result"]');e.scrollIntoView({block:'start'});return {text:${visibleText}}})()`, wait: 500, file: '16-evidence-why-trust.png' });
  add({ name: 'forensic', js: `(async()=>{${W}[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.trim()==='Details').click();await new Promise(r=>setTimeout(r,300));[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.trim()==='Forensic Evidence').click();await W(()=>document.querySelector('[role=tabpanel]')&&document.querySelector('[role=tabpanel]').innerText.length>40,30000);return {text:${visibleText}}})()`, wait: 700, file: '17-technical-forensic-evidence.png' });
} else {
  // a failing run, from the same window: New Goal -> a goal that asks the controlled body for a failure
  add({ name: 'new_goal', js: `(async()=>{${W}${btn('New Goal')}.click();await W(()=>document.querySelector('#ck-goal'),20000);${goalSet('Add input validation to the driver endpoint. [controlled:failure]')}return await W(()=>${btn('Start Run')}&&!${btn('Start Run')}.disabled,20000)})()`, wait: 300 });
  add({ name: 'start_failing', js: `(async()=>{${W}${btn('Start Run')}.click();const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);await new Promise(r=>setTimeout(r,800));return {ok,outcome:${run}&&${run}.dataset.outcome,state:${run}&&${run}.dataset.state,text:${visibleText}}})()`, wait: 700, file: '19-failure-state.png' });
}
writeFileSync(outFile, JSON.stringify(steps, null, 1));
writeFileSync(outFile.replace(/\.json$/, '.script.json'), JSON.stringify(SCRIPTS));
console.log(`${outFile}: ${steps.length} steps (${kind})`);
