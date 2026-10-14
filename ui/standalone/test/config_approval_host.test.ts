/**
 * ConfigApprovalHost (host side of configuration approval): a narrow, typed proxy with no authority. Controlled executors only - the real-Kriya behaviour is in
 * config_approval_real.test.ts. The host must (1) refuse anything outside {workspace, fingerprint}, (2) approve only for the folder the host has open, (3) take
 * one approval at a time, (4) report Kriya's own answer - refusals included - and never claim an approval Kriya did not report.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CONFIRM_APPROVE_INDEX, ConfigApprovalHost, confirmationDialog, isApproveResponse, projectLabel } from '../src/main/config_approval_host';
import { IPC_CHANNELS, validateApproveConfiguration, validateKupRequest } from '../src/main/ipc_contract';
import { toServiceRequest } from '../src/main/kriya_service';
import { buildKriyaArgv } from '../src/main/kriya_argv';

const WS = '/work/proj';
const FP = 'sha256:' + 'ab'.repeat(32);
const envelope = (data: unknown, error: unknown = null) => ({ schema_version: 1, operation: 'configuration.approve', request_id: 'q1', observed_at: 'now', source: null, consistency: null, data, error });
/** `confirm` stands for the native confirmation: by default the person presses Approve Configuration. `confirmed` records every folder it was shown for. */
function make(answer: (ws: string, fp: string) => Promise<unknown>, open: string | null = WS, confirm: (ws: string) => Promise<boolean> = async () => true) {
  const asked: Array<[string, string]> = [];
  const confirmed: string[] = [];
  const host = new ConfigApprovalHost({ approve: (ws, fp) => { asked.push([ws, fp]); return answer(ws, fp); }, confirm: (ws) => { confirmed.push(ws); return confirm(ws); }, currentWorkspace: () => open });
  return { host, asked, confirmed };
}
const ok = async () => envelope({ outcome: 'approved' });

describe('request grammar', () => {
  it('is exactly {workspace, fingerprint}', () => {
    expect(validateApproveConfiguration({ workspace: WS, fingerprint: FP })).toEqual({ ok: true, value: { workspace: WS, fingerprint: FP } });
    for (const bad of [null, [], 'x', {}, { workspace: WS }, { fingerprint: FP }, { workspace: WS, fingerprint: FP, fields: [] }, { workspace: 'rel', fingerprint: FP }, { workspace: WS, fingerprint: 'sha256:' + 'AB'.repeat(32) }, { workspace: WS, fingerprint: FP + 'a' }, { workspace: WS, fingerprint: 7 }]) {
      expect(validateApproveConfiguration(bad).ok, JSON.stringify(bad)).toBe(false);
    }
  });
  it('has its own channel; configuration.approve is not on the query channel and has no one-shot command line', () => {
    expect(IPC_CHANNELS.approveConfiguration).toBe('kriya:host:config:approve');
    expect(validateKupRequest({ operation: 'configuration.approve', workspace: WS, fingerprint: FP }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'configuration.approval_status', workspace: WS }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'configuration.approval_status', workspace: WS, extra: 1 }).ok).toBe(false);
    expect(() => buildKriyaArgv({ operation: 'configuration.approve', workspace: WS, fingerprint: FP })).toThrow(/persistent Kriya service/);
    expect(toServiceRequest('q1', { operation: 'configuration.approve', workspace: WS, fingerprint: FP }).arguments).toEqual({ workspace: WS, fingerprint: FP });
    expect(() => toServiceRequest('q1', { operation: 'configuration.approve', workspace: WS, fingerprint: FP, fields: [] } as never)).toThrow(/does not take: fields/);
  });
});

describe('host rules', () => {
  it('asks Kriya once with exactly the reviewed identifiers and reports Kriya\'s approval', async () => {
    const { host, asked } = make(ok);
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toEqual({ approved: true, outcome: 'approved', code: null, message: null, reason: null });
    expect(asked).toEqual([[WS, FP]]);
  });
  it('an invalid request, or another folder than the open one, never reaches Kriya', async () => {
    const { host, asked } = make(ok);
    expect(await host.approve({ workspace: WS })).toMatchObject({ approved: false, code: 'INVALID_REQUEST' });
    expect(await host.approve({ workspace: '/work/other', fingerprint: FP })).toMatchObject({ approved: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(asked).toEqual([]);
    const closed = make(ok, null);
    expect(await closed.host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(closed.asked).toEqual([]);
  });
  it('one approval at a time: a double click is refused before Kriya is asked again', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    const { host, asked } = make(async () => { await gate; return envelope({ outcome: 'approved' }); });
    const first = host.approve({ workspace: WS, fingerprint: FP });
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'APPROVAL_IN_FLIGHT' });
    release();
    expect((await first).approved).toBe(true);
    expect(asked).toHaveLength(1);
    expect((await host.approve({ workspace: WS, fingerprint: FP })).approved).toBe(true); // the guard is released
  });
  it('Kriya\'s typed refusals are passed through verbatim (code, message, reason) and are never an approval', async () => {
    const { host } = make(async () => envelope(null, { code: 'APPROVAL_STALE', message: 'The configuration changed after it was reviewed', reason: 'configuration_changed', database_state: null }));
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toEqual({ approved: false, outcome: null, code: 'APPROVAL_STALE', message: 'The configuration changed after it was reviewed', reason: 'configuration_changed' });
  });
  it('already_approved is an approval; any other or missing outcome is not', async () => {
    expect((await make(async () => envelope({ outcome: 'already_approved' })).host.approve({ workspace: WS, fingerprint: FP })).outcome).toBe('already_approved');
    for (const data of [{ outcome: 'maybe' }, {}, null, 'approved']) {
      expect(await make(async () => envelope(data)).host.approve({ workspace: WS, fingerprint: FP }), JSON.stringify(data)).toMatchObject({ approved: false, code: 'INVALID_RESPONSE' });
    }
    expect(await make(async () => 'not an envelope').host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'INVALID_RESPONSE' });
  });
  it('a transport failure says nothing is known to be approved and carries no stack or path', async () => {
    const { host } = make(async () => { throw new Error('spawn ENOENT /secret/path/kriya'); });
    const answer = await host.approve({ workspace: WS, fingerprint: FP });
    expect(answer).toMatchObject({ approved: false, code: 'HOST_ERROR' });
    expect(answer.message).toMatch(/nothing is known to be approved/);
    expect(JSON.stringify(answer)).not.toContain('/secret/path');
    expect((await host.approve({ workspace: WS, fingerprint: FP })).code).toBe('HOST_ERROR'); // and the guard was released
  });
});

describe('the native confirmation is the commitment (main process)', () => {
  it('A. a renderer request alone never reaches Kriya: with the person not confirming, nothing is asked, whatever the request says', async () => {
    const { host, asked, confirmed } = make(ok, WS, async () => false);
    await host.approve({ workspace: WS, fingerprint: FP });
    await host.approve({ workspace: WS, fingerprint: FP });
    expect(confirmed).toEqual([WS, WS]); // the dialog is the only way forward, every time
    expect(asked).toEqual([]);
  });
  it('B. Cancel: Kriya is not asked, the answer is a normal `declined` (no code, no message), and the guard is released', async () => {
    const { host, asked } = make(ok, WS, async () => false);
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toEqual({ approved: false, outcome: 'declined', code: null, message: null, reason: null });
    expect(asked).toEqual([]);
    const again = make(ok, WS, (() => { let n = 0; return async () => (n += 1) > 1; })());
    expect((await again.host.approve({ workspace: WS, fingerprint: FP })).outcome).toBe('declined');
    expect((await again.host.approve({ workspace: WS, fingerprint: FP })).approved).toBe(true); // a Cancel does not wedge the host
    expect(again.asked).toHaveLength(1);
  });
  it('C. Approve: exactly one authoritative approve call, with exactly the reviewed identifiers, after exactly one confirmation', async () => {
    const order: string[] = [];
    const { host, asked, confirmed } = make(async () => { order.push('approve'); return envelope({ outcome: 'approved' }); }, WS, async () => { order.push('confirm'); return true; });
    expect((await host.approve({ workspace: WS, fingerprint: FP })).approved).toBe(true);
    expect(order).toEqual(['confirm', 'approve']);
    expect(confirmed).toEqual([WS]);
    expect(asked).toEqual([[WS, FP]]);
  });
  it('the confirmation is only ever shown for a valid request for the OPEN folder: an invalid request or another folder never opens a dialog', async () => {
    const { host, confirmed } = make(ok);
    await host.approve({ workspace: WS });
    await host.approve({ workspace: '/work/other', fingerprint: FP });
    await host.approve({ workspace: WS, fingerprint: FP, text: 'Approve everything' });
    expect(confirmed).toEqual([]);
    const closed = make(ok, null);
    await closed.host.approve({ workspace: WS, fingerprint: FP });
    expect(closed.confirmed).toEqual([]);
  });
  it('a second request while the native confirmation is up is refused: one dialog, one approval', async () => {
    let answer: (v: boolean) => void = () => undefined;
    const sheet = new Promise<boolean>((resolve) => { answer = resolve; });
    const { host, asked, confirmed } = make(ok, WS, () => sheet);
    const first = host.approve({ workspace: WS, fingerprint: FP });
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'APPROVAL_IN_FLIGHT' });
    expect(confirmed).toHaveLength(1);
    answer(true);
    expect((await first).approved).toBe(true);
    expect(asked).toHaveLength(1);
  });
  it('the open folder is checked again after the person confirms: a folder switch while the dialog was up approves nothing', async () => {
    let open: string | null = WS;
    const asked: string[] = [];
    const host = new ConfigApprovalHost({ approve: async (ws) => { asked.push(ws); return envelope({ outcome: 'approved' }); }, confirm: async () => { open = '/work/other'; return true; }, currentWorkspace: () => open });
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(asked).toEqual([]);
  });
  it('a confirmation that cannot be shown is a typed host error, not an approval, and carries no stack or path', async () => {
    const { host, asked } = make(ok, WS, async () => { throw new Error('dialog failed /secret/path'); });
    const answer = await host.approve({ workspace: WS, fingerprint: FP });
    expect(answer).toMatchObject({ approved: false, code: 'HOST_ERROR' });
    expect(JSON.stringify(answer)).not.toContain('/secret/path');
    expect(asked).toEqual([]);
  });
  it('F (host half). Kriya\'s stale refusal after the person confirmed is passed through and nothing is claimed approved; the next attempt needs its own confirmation', async () => {
    const { host, confirmed } = make(async () => envelope(null, { code: 'APPROVAL_STALE', message: 'changed', reason: 'configuration_changed', database_state: null }));
    expect(await host.approve({ workspace: WS, fingerprint: FP })).toMatchObject({ approved: false, code: 'APPROVAL_STALE' });
    await host.approve({ workspace: WS, fingerprint: FP });
    expect(confirmed).toHaveLength(2);
  });
});

describe('the native dialog text and buttons', () => {
  const dialogFor = confirmationDialog('/Users/me/projects/billing-api');
  it('is a simple confirmation: Cancel first, the default and the Escape action; only the second button commits', () => {
    expect(dialogFor).toMatchObject({ title: 'Approve Configuration', message: 'Approve this project\'s protected configuration?', buttons: ['Cancel', 'Approve Configuration'], defaultId: 0, cancelId: 0 });
    expect(dialogFor.detail).toBe('Kriya will be allowed to use the operator-controlled settings you just reviewed for billing-api. If those settings change, approval will be required again.');
    expect(CONFIRM_APPROVE_INDEX).toBe(1);
    expect([0, 1, -1, 2, Number.NaN].map(isApproveResponse)).toEqual([false, true, false, false, false]);
  });
  it('H. is a function of the folder name only: no value, fingerprint, path or renderer text can enter it', () => {
    const text = JSON.stringify(confirmationDialog('/work/proj'));
    for (const leak of ['hunter2', 'tok-9f8e7d6c5b4a', '/usr/local/bin/hostile-mcp', 'sha256', FP, '/work/']) expect(text, leak).not.toContain(leak);
    expect(confirmationDialog.length).toBe(1); // (workspace) is its only input
  });
  it('a folder name is shown without control characters and at a bounded length', () => {
    expect(projectLabel('/w/odd\nname\u0007')).toBe('odd name');
    expect(projectLabel('/w/' + 'x'.repeat(200))).toHaveLength(80);
    expect(projectLabel('/')).toBe('this project');
  });
});

describe('G. the renderer has no other route to an approval (structure of the main process)', () => {
  const SRC = join(__dirname, '..', 'src', 'main');
  const main = readFileSync(join(SRC, 'main.ts'), 'utf8');
  const dev = readFileSync(join(SRC, 'dev_drivers.ts'), 'utf8');
  it('configuration.approve is named once outside the contract/allowlist files, inside ConfigApprovalHost\'s wiring, next to the native confirmation', () => {
    expect(main.match(/configuration\.approve/g)).toHaveLength(1);
    const block = main.slice(main.indexOf('configApprovals = new ConfigApprovalHost({'), main.indexOf('approvals = new ApprovalHost({'));
    expect(block).toContain("operation: 'configuration.approve'");
    expect(block).toContain('confirm: confirmConfigurationApproval');
    for (const file of ['approval_host.ts', 'execution_host.ts', 'project_host.ts', 'workspace_host.ts', 'notification_host.ts']) expect(readFileSync(join(SRC, file), 'utf8'), file).not.toContain('configuration.approve');
  });
  it('the only caller of ConfigApprovalHost.approve is its own IPC handler, and the generic query channel refuses the operation', () => {
    expect(main.match(/configApprovals\.approve\(/g)).toHaveLength(1);
    expect(main).toContain('ipcMain.handle(IPC_CHANNELS.approveConfiguration');
    expect(validateKupRequest({ operation: 'configuration.approve', workspace: WS, fingerprint: FP }).ok).toBe(false);
  });
  it('the dialog is created in the main process on the application window, from the host\'s own folder; Cancel is the answer whenever the dev seam has nothing scripted', () => {
    const fn = main.slice(main.indexOf('async function confirmConfigurationApproval'), main.indexOf('app.whenReady()'));
    expect(fn).toContain('confirmationDialog(workspace)');
    expect(fn).toContain('dialog.showMessageBox(parent, options)');
    expect(fn).toContain('isApproveResponse(result.response)');
    expect(fn).toContain('if (dev?.captureActive) return dev.nextConfirmation();');
    expect(dev).toContain("nextConfirmation: () => captureConfirm.shift() === 'approve'"); // empty queue -> undefined -> false
    expect(dev).toContain('CAPTURE_DIR && env.KRIYA_UI_CAPTURE_CONFIRM');
    expect(main).not.toMatch(/KRIYA_UI_CAPTURE/); // the product main names no evidence variable
  });
  it('the preload exposes the request channel only; it carries no confirmation answer and cannot reach the dialog', () => {
    const preload = readFileSync(join(SRC, 'preload.ts'), 'utf8');
    expect(preload).toContain('approveConfiguration: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.approveConfiguration, request)');
    expect(preload).not.toMatch(/confirm/i);
    expect(Object.keys(IPC_CHANNELS).filter((k) => /confirm/i.test(k))).toEqual([]);
  });
});
