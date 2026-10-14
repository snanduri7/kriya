/** GUI v1 hardening (Phase K): every user-visible failure is a typed reason in plain words; raw technical text (a child's stderr, a traceback) lives only behind a
 * "Technical detail" disclosure, never in the message. The states a person can meet are distinct from one another and none says "something went wrong". */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../../state/selection';
import { createCockpitRegistry } from '../composition';
import { availabilityTitle, createSelectionStore, failed, unavailable, type UnavailableReason } from '../core';
import { FixtureHost } from '../providers/fixture';
import { HostProvider } from '../providers/hostProvider';
import { FAST_POLL } from '../testing/harness';
import { AppShell } from './AppShell';
import type { KupEnvelope, KupRequest } from '../../model/kup';

const TRACEBACK = 'Traceback (most recent call last):\n  File "/x/kriya/cli.py", line 9, in main\nModuleNotFoundError: No module named \'kriya\'\n    at Module._resolveFilename (node:internal/modules/cjs/loader:1248:15)';
const mount = (host: FixtureHost) => { cleanup(); render(<AppShell provider={new HostProvider(host, 'fixture')} registry={createCockpitRegistry()} selection={createSelectionStore({ ...initialSelection })} viewportWidth={1440} pollIntervals={FAST_POLL} initialPage="runs" />); };
const hostError = (operation: string, code: string, message: string, detail?: string) => ({ schema_version: 1, operation, request_id: 'x', observed_at: 't', source: null, consistency: null, data: null, error: { code, message, ...(detail ? { detail } : {}) } }) as unknown as KupEnvelope;

describe('technical detail never reaches the message', () => {
  it('a failed folder open shows the typed message and code; the traceback is only inside a collapsed Technical detail', async () => {
    const host = new FixtureHost({ workspace: null, choices: ['/fixture/workspace'] });
    const original = host.query.bind(host);
    host.query = async (r: KupRequest) => (r.operation === 'workspace.describe' ? hostError('workspace.describe', 'HOST_ERROR', 'stdout was not JSON (exit 1)', TRACEBACK) : original(r));
    mount(host);
    await screen.findByRole('main', { name: 'Welcome' });
    host.menu('openFolder');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('stdout was not JSON (exit 1)');
    expect(alert).toHaveTextContent('HOST_ERROR');
    const details = alert.querySelector('details')!;
    expect(details).not.toBeNull();
    expect(details.hasAttribute('open')).toBe(false);
    expect(details.textContent).toContain('ModuleNotFoundError');
    const visible = Array.from(alert.childNodes).filter((n) => n !== details).map((n) => n.textContent).join(' ');
    expect(visible).not.toMatch(/Traceback|File "|node:internal|ModuleNotFoundError|at Module\./);
  });

  it('a failed read in a module shows the typed error title and message; the stderr is behind Technical detail', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace' });
    const original = host.query.bind(host);
    host.query = async (r: KupRequest) => (r.operation === 'history.detail' ? hostError('history.detail', 'HOST_ERROR', 'kriya service died (exit 1)', TRACEBACK) : original(r));
    mount(host);
    fireEvent.click((await screen.findAllByRole('button', { name: /success/ }))[0]!);
    const alert = (await screen.findAllByRole('alert')).find((a) => a.textContent?.includes('kriya service died'))!;
    expect(alert).toHaveTextContent('Error HOST_ERROR');
    expect(within(alert).getByText('Technical detail')).toBeInTheDocument();
    const visible = Array.from(alert.childNodes).filter((n) => (n as Element).tagName !== 'DETAILS').map((n) => n.textContent).join(' ');
    expect(visible).not.toMatch(/Traceback|node:internal|ModuleNotFound/);
  });
});

describe('the states a person can meet are distinct, typed and never generic', () => {
  it('every unavailable reason has its own plain title; none says "no data", "empty", "something went wrong" or "undefined"', () => {
    const reasons: UnavailableReason[] = ['capability_absent', 'store_absent', 'pruned', 'capture_off', 'pre_recorder', 'recorder_unavailable', 'digest_only', 'denied', 'unverified', 'unknown_run', 'pending'];
    const titles = reasons.map((r) => availabilityTitle(unavailable(r, 'x')));
    expect(new Set(titles).size).toBe(reasons.length);
    for (const t of titles) expect(t).not.toMatch(/no data|empty|went wrong|undefined|unknown error|error occurred/i);
    expect(availabilityTitle(failed('EVIDENCE_VERIFY_FAILED', 'm'))).toBe('Error EVIDENCE_VERIFY_FAILED'); // corrupted evidence names its own code
    expect(availabilityTitle(failed('WORKSPACE_UNAVAILABLE', 'm'))).toBe('Error WORKSPACE_UNAVAILABLE');
  });

  it('no production source says "something went wrong", renders a stack, or prints [object Object]', () => {
    const files = import.meta.glob('../**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
    for (const [path, text] of Object.entries(files).filter(([p]) => !/\.test\.|testing\//.test(p))) {
      expect(text, path).not.toMatch(/something went wrong|unexpected error|an error occurred|oops|\[object Object\]|\.stack\b|stack trace/i);
    }
  });

  it('the outcome vocabulary distinguishes the cases a person must not confuse', async () => {
    const { deriveOutcome } = await import('../core');
    const labels = new Set<string>();
    const base = { workspace: { path: '/w', identity: 'x' }, requested_at: 't', goal: { sha256: 'g', chars: 1 }, options: {}, history: [], process: null, declined_decisions: [], pending_approval: null };
    const run = { run_id: 'r', started_at: 't', base_revision: null };
    const ended = (over: Record<string, unknown>) => ({ at: 't', exit_code: 1, workflow_status: null, quality_gates_passed: false, failure_category: null, trace_run_id: null, error: null, note: 'n', ...over });
    const status = (launch: Record<string, unknown>, record: Record<string, unknown>, liveness = 'ended') => ({ launch: { launch_id: 'x', phase: 'ENDED', run, ended: ended({}), approval_required: null, ...base, ...launch }, run_record: { found: true, unreadable: null, ...record }, evidence: { state: 'sealed', line_count: 3, sealed: true }, workspace_lock: { held: false, held_by_this_launch: false }, liveness, liveness_note: 'n' }) as never;
    const exited = { phase: 'exited', launch_id: 'x', workspace: '/w', started_at: 't', ended_at: 't', exit: { code: 1, signal: null }, diagnostic: null } as never;
    for (const outcome of [
      deriveOutcome(status({}, { terminal_status: 'SUCCESS' }), false, null), deriveOutcome(status({}, { terminal_status: 'FAILURE' }), false, null),
      deriveOutcome(status({ run: null, ended: ended({ error: 'refused' }) }, {}), false, null),
      deriveOutcome(status({ approval_required: { kind: 'knowledge_gap_acknowledgement', reason: 'r', details: {}, noted_at: 't' } }, { terminal_status: 'FAILURE' }), false, null),
      deriveOutcome(status({ pending_approval: { status: 'REJECTED', review_id: 'ra-' + 'a'.repeat(24), decision: { decision: 'reject' } } }, { terminal_status: 'FAILURE' }), false, null),
      deriveOutcome(status({ pending_approval: { status: 'EXPIRED', review_id: 'ra-' + 'a'.repeat(24), decision: { source: 'timeout' } } }, { terminal_status: 'FAILURE' }), false, null),
      deriveOutcome(status({ phase: 'RUNNING', ended: null }, { terminal_status: null }, 'interrupted'), false, exited),
      deriveOutcome(null, true, exited),
    ]) { expect(outcome).not.toBeNull(); labels.add(outcome!.label); }
    expect(labels.size).toBe(8); // succeeded, failed, refused, approval required, rejected, review expired, did not finish, no run recorded - eight distinct labels
    for (const l of labels) expect(l).not.toMatch(/went wrong|error occurred|unknown/i);
  });
});

describe('a pruned, pre-recorder, capture-off, digest-only and unsealed run each say so in their own words', () => {
  it('the fixture runs render distinct, typed titles through the real Evidence/Changes modules', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace' });
    mount(host);
    const expectations: Array<[RegExp, RegExp]> = [[/never closed its store/, /unsealed|Recorded, with limits/i], [/retention removed/, /pruned/i], [/capture disabled/, /Capture was off/], [/no evidence\.attempt_store event/, /Recorded before the recorder/]];
    for (const [goal, title] of expectations) {
      fireEvent.click(await screen.findByRole('button', { name: goal }));
      await waitFor(() => expect(document.body.textContent).toMatch(title), { timeout: 4000 });
    }
  });
});
