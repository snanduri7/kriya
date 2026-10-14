/**
 * ConfigApprovalHost (host side of configuration approval): a narrow, typed proxy with no authority. Controlled executors only - the real-Kriya behaviour is in
 * config_approval_real.test.ts. The host must (1) refuse anything outside {workspace, fingerprint}, (2) approve only for the folder the host has open, (3) take
 * one approval at a time, (4) report Kriya's own answer - refusals included - and never claim an approval Kriya did not report.
 */
import { describe, expect, it } from 'vitest';
import { ConfigApprovalHost } from '../src/main/config_approval_host';
import { IPC_CHANNELS, validateApproveConfiguration, validateKupRequest } from '../src/main/ipc_contract';
import { toServiceRequest } from '../src/main/kriya_service';
import { buildKriyaArgv } from '../src/main/kriya_argv';

const WS = '/work/proj';
const FP = 'sha256:' + 'ab'.repeat(32);
const envelope = (data: unknown, error: unknown = null) => ({ schema_version: 1, operation: 'configuration.approve', request_id: 'q1', observed_at: 'now', source: null, consistency: null, data, error });
function make(answer: (ws: string, fp: string) => Promise<unknown>, open: string | null = WS) {
  const asked: Array<[string, string]> = [];
  const host = new ConfigApprovalHost({ approve: (ws, fp) => { asked.push([ws, fp]); return answer(ws, fp); }, currentWorkspace: () => open });
  return { host, asked };
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
