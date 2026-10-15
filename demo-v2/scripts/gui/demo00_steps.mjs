#!/usr/bin/env node
/** Demo V2 runtime-approval smoke steps: node demo00_steps.mjs main|reject|cancel|managed|failure <out.json>.
 *  Drives the evidence-packaged desktop app with the product's CONTROLLED workflow body (NO model; ui/standalone/test/fixtures/controlled_kriya.py), over a DISPOSABLE scratch project and HOME.
 *  Everything around the body is real Kriya: New Project, Project Setup, configuration approval (scripted NATIVE confirmation), the launch, the REAL change-approval path (one click), the REAL
 *  runtime-command approval path (Reject / Continue to Run Approval... then the scripted NATIVE confirmation), the result screens. CONTROLLED: the files and the program output are scripted test data.
 *  The native confirmation itself is an operating-system sheet and cannot appear in an in-page screenshot; its answer is scripted (KRIYA_UI_CAPTURE_CONFIRM) and its effects are captured. */
import { writeFileSync } from 'node:fs';

const [kind, outFile] = process.argv.slice(2);
const KINDS = ['main', 'reject', 'cancel', 'managed', 'failure'];
if (!KINDS.includes(kind) || !outFile) { console.error('usage: demo00_steps.mjs main|reject|cancel|managed|failure <out.json>'); process.exit(2); }
const GOAL = kind === 'managed' ? 'Start a small web server and confirm it responds with 200 OK on /health.'
  : kind === 'failure' ? 'Add input validation to the greeting endpoint. [controlled:failure]'
  : "Create a small Java 17 Maven application named hello-kriya that prints 'Hello from Kriya' and includes a unit test. Make the application runnable.";
const W = `const W=(f,ms=60000)=>new Promise((r)=>{const i=setInterval(()=>{let v=false;try{v=f()}catch(e){}if(v){clearInterval(i);r(true)}},40);setTimeout(()=>{clearInterval(i);r(false)},ms)});`;
const btn = (label) => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;
const nav = (label) => `document.querySelector('nav[aria-label=Destinations] button[aria-label=${JSON.stringify(label)}]')`;
const goalSet = (text) => `const ta=document.querySelector('#ck-goal');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,${JSON.stringify(text)});ta.dispatchEvent(new Event('input',{bubbles:true}));`;
const run = `document.querySelector('[aria-label="Current run"]')`;
const dlg = (name) => `document.querySelector('[role=dialog][aria-label=${JSON.stringify(name)}]')`;
const region = (name) => `document.querySelector('[aria-label=${JSON.stringify(name)}]')`;
const visibleText = `(()=>{const out=[];const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while((n=w.nextNode())){const t=n.textContent.trim();if(!t)continue;const e=n.parentElement;if(!e||e.closest('details:not([open])')&&!e.closest('summary'))continue;const r=e.getBoundingClientRect();if(r.width===0||r.height===0)continue;const cs=getComputedStyle(e);if(cs.visibility==='hidden'||cs.display==='none')continue;out.push(t)}return out.join(' | ').slice(0,6000)})()`;
const steps = [];
// Every step reports a thrown error as data ({error}) instead of aborting the whole journey: a screenshot of what IS on screen is still evidence, and results.json says which step failed.
const guard = (js) => `(async()=>{try{return await (${js})}catch(e){return {error:String((e&&e.message)||e)}}})()`;
const add = (s) => steps.push(s.js ? { ...s, js: guard(s.js) } : s);
const shot = (name, file, extra = {}) => add({ name, file, js: `(()=>${visibleText})()`, wait: 300, ...extra });
const DIFF_POM = { path: 'pom.xml', diff: '--- a/pom.xml\n+++ b/pom.xml\n@@ -0,0 +1,12 @@\n+<project xmlns="http://maven.apache.org/POM/4.0.0">\n+  <modelVersion>4.0.0</modelVersion>\n+  <groupId>dev.kriya</groupId>\n+  <artifactId>hello-kriya</artifactId>\n+  <version>1.0.0</version>\n+  <properties><maven.compiler.release>17</maven.compiler.release></properties>\n+</project>\n' };
const DIFF_APP = { path: 'src/main/java/dev/kriya/App.java', diff: '--- a/src/main/java/dev/kriya/App.java\n+++ b/src/main/java/dev/kriya/App.java\n@@ -0,0 +1,7 @@\n+package dev.kriya;\n+\n+public class App {\n+    public static void main(String[] args) {\n+        System.out.println("Hello from Kriya");\n+    }\n+}\n' };
const DIFF_TEST = { path: 'src/test/java/dev/kriya/AppTest.java', diff: '--- a/src/test/java/dev/kriya/AppTest.java\n+++ b/src/test/java/dev/kriya/AppTest.java\n@@ -0,0 +1,7 @@\n+package dev.kriya;\n+\n+import org.junit.jupiter.api.Test;\n+\n+class AppTest {\n+    @Test void mainRuns() { App.main(new String[0]); }\n+}\n' };
const RUNTIME = { runtime_commands: [['mvn', '-q', 'package'], ['java', '-cp', 'target/classes', 'dev.kriya.App']], runtime_criterion: "The application prints 'Hello from Kriya' and exits with status 0.", runtime_stdout: 'Hello from Kriya\n' };
const GATES_OK = [{ attempt: 1, type: 'compile', success: true, output: 'ok' }, { attempt: 1, type: 'regression_test', success: true, output: 'Tests run: 3' }];
const SCRIPTS = {
  main: { outcome: 'greenfield_flow', step_ms: 1500, post_approval_ms: 6000, diffs: [DIFF_POM, DIFF_APP, DIFF_TEST], tests: 3, compiled_goal: true, rich_events: true, ...RUNTIME },
  reject: { outcome: 'runtime_review', step_ms: 1200, tests: 3, diffs: [DIFF_POM, DIFF_APP, DIFF_TEST], compiled_goal: true, rich_events: true, gate_outcomes: GATES_OK, ...RUNTIME },
  cancel: { outcome: 'runtime_review', step_ms: 1200, tests: 3, diffs: [DIFF_POM, DIFF_APP, DIFF_TEST], compiled_goal: true, rich_events: true, gate_outcomes: GATES_OK, ...RUNTIME },
  managed: { outcome: 'failure', step_ms: 1200, compiled_goal: true, rich_events: true, gate_outcomes: [...GATES_OK, { attempt: 1, type: 'verification_infrastructure_failure', success: false,
    output: 'MANAGED_SERVICE_APPROVAL_UNSUPPORTED: this goal requires Kriya to start a long-running service to verify it, and this version has no way to ask you to approve that. No service was started.' }] },
  failure: { outcome: 'success', step_ms: 1200, compiled_goal: true, rich_events: true },
}[kind];

add({ wait: 700, file: '00-launch-no-project.png', width: 1280, height: 800, name: 'welcome', js: `(()=>${visibleText})()` });
// New Project -> Project Setup -> configuration approval (scripted NATIVE confirmation) -> Ready
add({ name: 'new_project', js: `(async()=>{${W}${btn('New Project…')}.click();return await W(()=>${dlg('New Project')},20000)})()`, wait: 400 });
add({ name: 'create_project', js: `(async()=>{${W}const i=document.querySelector('#ck-project-name');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'hello-kriya');i.dispatchEvent(new Event('input',{bubbles:true}));await new Promise(r=>setTimeout(r,200));${btn('Create project')}.click();return await W(()=>${region('Project setup')},30000)})()`, wait: 600 });
add({ name: 'setup_advanced', js: `(()=>{const d=${region('Project setup')}.querySelector('details');if(d)d.open=true;return true})()`, wait: 300 });
add({ name: 'create_setup', js: `(async()=>{${W}${btn('Create Project Configuration')}.click();return await W(()=>${region('Configuration approval')}&&/ACTION REQUIRED/.test(${region('Configuration approval')}.innerText),30000)})()`, wait: 500 });
add({ name: 'review_open', js: `(async()=>{${W}${btn('Review & Approve')}.click();return await W(()=>${dlg('Configuration approval')}&&/Protected settings/.test(${dlg('Configuration approval')}.innerText)&&${btn('Continue to Approval…')},20000)})()`, wait: 500 });
add({ name: 'native_approve_config', js: `(async()=>{${W}${btn('Continue to Approval…')}.click();return await W(()=>!${dlg('Configuration approval')}&&/✓ Ready/.test(document.body.innerText),30000)})()`, wait: 700 });
shot('project_ready', '01-project-ready.png');
add({ name: 'goal', js: `(async()=>{${W}await W(()=>document.querySelector('#ck-goal'),30000);${goalSet(GOAL)}return await W(()=>${btn('Start Run')}&&!${btn('Start Run')}.disabled,30000)})()`, wait: 400 });
shot('goal_entered', '02-goal-entered.png');
add({ name: 'start', js: `(async()=>{${W}${btn('Start Run')}.click();return await W(()=>${run},30000)})()`, wait: 100 });

if (kind === 'main') {
  add({ name: 'planning', js: `(async()=>{${W}const ok=await W(()=>${run}&&['PLANNING','STARTING','EDITING'].includes(${run}.dataset.state),30000);return {ok,state:${run}&&${run}.dataset.state,text:${visibleText}}})()`, wait: 150, file: '03-running-planning.png' });
  add({ name: 'source_review', js: `(async()=>{${W}const ok=await W(()=>${region('Change review')}&&${region('Change review')}.querySelector('[data-testid=proposed-change]'),90000);return {ok,subject:(${region('Change review')}.querySelector('[data-testid=approval-subject]')||{}).textContent,text:${visibleText}}})()`, wait: 600, file: '04-source-diff.png' });
  add({ name: 'source_verification', js: `(()=>{const e=${region('Change review')}.querySelector('ul[aria-label="Verification so far"]');if(e)e.scrollIntoView({block:'center'});return {list:e?e.innerText:null,text:${visibleText}}})()`, wait: 500, file: '06-tests-nonzero-count.png' });
  add({ name: 'source_approve_one_click', js: `(async()=>{${W}const confirmShown=!!${btn('Confirm approval')};${btn('Approve & Continue')}.click();const ok=await W(()=>!${region('Change review')}||/recorded|sent/i.test(${region('Change review')}.innerText),30000);return {ok,secondConfirmationExisted:confirmShown,text:${visibleText}}})()`, wait: 500, file: '05-source-approval-one-click.png' });
  add({ name: 'runtime_review', js: `(async()=>{${W}const ok=await W(()=>${region('Runtime command review')}&&${region('Runtime command review')}.querySelector('[data-testid=runtime-commands]'),90000);const r=${region('Runtime command review')};return {ok,subject:(r.querySelector('[data-testid=approval-subject]')||{}).textContent,commands:[...r.querySelectorAll('[data-testid=runtime-commands] li')].map(l=>l.textContent),runsIn:(r.querySelector('[data-testid=runtime-runs-in]')||{}).textContent,text:${visibleText}}})()`, wait: 600, file: '07-runtime-command-review.png' });
  add({ name: 'continue_to_run_approval', js: `(async()=>{${W}${btn('Continue to Run Approval…')}.click();const ok=await W(()=>!${region('Runtime command review')}||/Permission sent|recorded/i.test(${region('Runtime command review')}.innerText),30000);return {ok,note:'the native Run Commands confirmation is an OS sheet (not in-page); its scripted answer was approve',text:${visibleText}}})()`, wait: 900, file: '09-running-application.png' });
  add({ name: 'activity_drawer', js: `(async()=>{${W}${btn('Show Activity')}.click();await W(()=>${region('Activity')}&&${region('Activity')}.querySelector('[data-testid=activity-list]'),20000);return {text:${region('Activity')}.innerText}})()`, wait: 600, file: '10-activity-drawer.png' });
  add({ name: 'completed', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',120000);await W(()=>${run}.querySelector('[data-testid=application-outcome]'),30000);await new Promise(r=>setTimeout(r,800));return {ok,outcome:${run}.dataset.outcome,statement:(${run}.querySelector('[data-testid=applied-statement]')||{}).textContent,text:${visibleText}}})()`, wait: 700, file: '12-completed-overview.png' });
  add({ name: 'application_output', js: `(()=>{const e=${run}.querySelector('[data-testid=application-outcome]');if(e)e.scrollIntoView({block:'start'});return {text:e?e.innerText:null}})()`, wait: 500, file: '11-application-output.png' });
  add({ name: 'runs_history', js: `(async()=>{${W}${nav('Runs')}.click();await W(()=>document.querySelector('.ck-runtitle'),30000);document.querySelector('.ck-list li button').click();await W(()=>document.querySelector('[aria-label="Run result"]'),30000);[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.trim()==='Verification').click();await W(()=>document.querySelector('[aria-label="How Kriya knows this works"]'),30000);return {text:${visibleText}}})()`, wait: 700, file: '13-verification.png' });
  add({ name: 'project_details', js: `(async()=>{${W}${nav('Project')}.click();await W(()=>document.querySelector('details.ck-setup > summary'),20000);document.querySelector('details.ck-setup').open=true;await new Promise(r=>setTimeout(r,300));const e=document.querySelector('[data-testid=project-facts]');if(e)e.scrollIntoView({block:'start'});return {facts:e?e.innerText:null,text:${visibleText}}})()`, wait: 600, file: '14-project-details.png' });
  add({ name: 'technical_project_details', js: `(()=>{const d=document.querySelector('[data-testid=technical-project-details]');d.open=true;d.scrollIntoView({block:'start'});return {text:${visibleText}}})()`, wait: 600, file: '15-technical-project-details.png' });
} else if (kind === 'reject' || kind === 'cancel') {
  add({ name: 'runtime_review', js: `(async()=>{${W}const ok=await W(()=>${region('Runtime command review')}&&${region('Runtime command review')}.querySelector('[data-testid=runtime-commands]'),90000);return {ok,text:${visibleText}}})()`, wait: 600 });
  if (kind === 'reject') {
    add({ name: 'reject', js: `(async()=>{${W}${btn('Reject')}.click();const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);await new Promise(r=>setTimeout(r,800));return {ok,outcome:${run}.dataset.outcome,text:${visibleText}}})()`, wait: 700, file: '16-runtime-command-rejected.png' });
  } else {
    add({ name: 'cancel_native', js: `(async()=>{${W}${btn('Continue to Run Approval…')}.click();const ok=await W(()=>${region('Runtime command review')}&&${region('Runtime command review')}.querySelector('[data-testid=runtime-declined]'),30000);return {ok,stillPending:!!${region('Runtime command review')},note:'native Cancel (scripted): no decision, nothing run',text:${visibleText}}})()`, wait: 700, file: '17-native-cancel-still-pending.png' });
    add({ name: 'then_reject_to_end', js: `(async()=>{${W}${btn('Reject')}.click();return await W(()=>${run}&&${run}.dataset.stage==='finished',90000)})()`, wait: 500 });
  }
} else if (kind === 'managed') {
  add({ name: 'stopped', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);await W(()=>${run}.querySelector('[data-testid=runtime-stop]'),30000);await new Promise(r=>setTimeout(r,600));return {ok,outcome:${run}.dataset.outcome,text:${visibleText}}})()`, wait: 700, file: '18-managed-service-unsupported.png' });
} else {
  add({ name: 'stopped', js: `(async()=>{${W}const ok=await W(()=>${run}&&${run}.dataset.stage==='finished',90000);await new Promise(r=>setTimeout(r,800));return {ok,outcome:${run}.dataset.outcome,text:${visibleText}}})()`, wait: 700, file: '19-general-failure.png' });
}
writeFileSync(outFile, JSON.stringify(steps, null, 1));
writeFileSync(outFile.replace(/\.json$/, '.script.json'), JSON.stringify(SCRIPTS));
console.log(`${outFile}: ${steps.length} steps (${kind})`);
