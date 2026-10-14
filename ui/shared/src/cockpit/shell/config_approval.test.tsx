/**
 * Configuration approval from the Project page (Demo V2): a person opens a folder whose operator-controlled configuration has no current approval, reviews it, approves
 * it and starts a run without leaving the application. The renderer holds no authority: every assertion below is about what it asks Kriya (the scripted fixture host
 * stands for Kriya's `configuration.*` operations) and how it renders Kriya's answers. The approval semantics themselves - digest binding, staleness, equivalence with
 * `kriya authority` - are pinned against the real backend in tests/test_kup_config_approval.py and ui/standalone/test/config_approval_real.test.ts.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { initialSelection } from '../../state/selection';
import { createCockpitRegistry } from '../composition';
import { createSelectionStore } from '../core';
import { FixtureHost, type FixtureHostOptions } from '../providers/fixture';
import type { FixtureExecutionOptions } from '../providers/fixture/fixtureExecution';
import { HostProvider } from '../providers/hostProvider';
import { FAST_POLL } from '../testing/harness';
import hostAdapterSource from '../../host/HostAdapter.ts?raw';
import executionSource from '../core/execution.tsx?raw';
import approvalSource from '../modules/workspace/ConfigApproval.tsx?raw';
import { AppShell } from './AppShell';

const FIELDS = ['autonomy.mode', 'embedding.base_url', 'llm.base_url', 'llm_chain', 'mcp.hostile', 'paths.memory', 'paths.skills'];
const GOAL = 'Add a clamp(value, low, high) function with tests.';
const WS = '/fixture/workspace';

function mount(host: FixtureHost) {
  const provider = new HostProvider(host, 'fixture');
  const store = createSelectionStore({ ...initialSelection });
  return render(<AppShell provider={provider} registry={createCockpitRegistry()} selection={store} viewportWidth={1440} pollIntervals={FAST_POLL} />);
}
async function open(options: Partial<FixtureHostOptions> & { fields?: readonly string[]; facts?: FixtureExecutionOptions['facts']; configuration?: Record<string, unknown> } = {}) {
  const { fields = FIELDS, facts, configuration, ...rest } = options;
  const host = new FixtureHost({ workspace: WS, execution: { outcome: 'success', facts, configuration: { fields, ...configuration } }, ...rest });
  mount(host);
  await screen.findByRole('navigation', { name: 'Destinations' });
  await screen.findByText('What do you want Kriya to do?');
  return host;
}
const card = () => screen.findByRole('region', { name: 'Configuration approval' });
const dialog = () => screen.findByRole('dialog', { name: 'Configuration approval' });
const goalBox = () => screen.getByRole('textbox', { name: 'Engineering goal' }) as HTMLTextAreaElement;
const startButton = () => screen.getByRole('button', { name: /^Start Run$|^Starting…$/ });
const typeGoal = (text = GOAL) => fireEvent.change(goalBox(), { target: { value: text } });
const reviewAndApprove = async () => { fireEvent.click(await screen.findByRole('button', { name: 'Review & Approve' })); return dialog(); };
const approveButton = () => screen.getByRole('button', { name: 'Approve Configuration' });
const calls = (host: FixtureHost, operation: string) => vi.mocked(host.query).mock.calls.filter(([r]) => r.operation === operation).length;
function spyOnQueries(host: FixtureHost) { vi.spyOn(host, 'query'); return host; }

describe('A. approval is the only blocker: ACTION REQUIRED, not a terminal instruction', () => {
  it('shows an actionable card with the product wording; no "Cannot start a run", no CLI, no instruction to leave the app', async () => {
    const host = await open();
    const c = await card();
    expect(c).toHaveTextContent('ACTION REQUIRED');
    expect(c).toHaveTextContent('Configuration approval required');
    await waitFor(() => expect(c).toHaveTextContent(`This project's configuration uses ${FIELDS.length} operator-controlled settings. Review and approve the current configuration before starting Kriya.`));
    expect(within(c).getByRole('button', { name: 'Review & Approve' })).toBeVisible();
    // the Project page's primary surface: the approval card and the composer around it (Skills/Tools explain, elsewhere, that those are managed in a terminal)
    const primary = (c.textContent ?? '') + (screen.getByRole('region', { name: 'Start a run' }).textContent ?? '');
    expect(primary).not.toMatch(/kriya authority approve/);
    expect(primary).not.toMatch(/terminal/);
    expect(primary).not.toMatch(/this application cannot/);
    expect(document.body.textContent).not.toMatch(/kriya authority approve|this application cannot/);
    expect(screen.queryByText(/Cannot start a run/)).toBeNull();
    expect(screen.getByText('Action required')).toBeInTheDocument(); // the header badge: an action, not a failure
    expect(screen.getByRole('textbox', { name: 'Engineering goal' })).toBeVisible();
    expect(startButton()).toBeDisabled();
    expect(screen.getByText('Approve the configuration above to start.')).toBeInTheDocument();
    expect(host.exec!.approvals).toHaveLength(0); // opening a project, reading the page and typing approve nothing
  });
  it('typing a goal and pressing the Start shortcut with an unapproved configuration approves nothing and starts nothing', async () => {
    const host = await open();
    await card();
    typeGoal();
    fireEvent.keyDown(goalBox(), { key: 'Enter', metaKey: true });
    fireEvent.keyDown(goalBox(), { key: 'Enter', ctrlKey: true });
    expect(host.exec!.approvals).toHaveLength(0);
    expect(host.exec!.starts).toHaveLength(0);
  });
  it('"View configuration" is the secondary inspection; it approves nothing', async () => {
    const host = await open();
    fireEvent.click(within(await card()).getByRole('button', { name: 'View configuration' }));
    expect(host.exec!.approvals).toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('B/C. the review shows protected setting NAMES and never a value', () => {
  it('lists every protected setting by name, the project, the fingerprint and the scope sentence; technical details are one disclosure down', async () => {
    const host = await open();
    const d = await reviewAndApprove();
    for (const f of FIELDS) expect(await within(d).findByText(f, { selector: 'li' })).toBeInTheDocument();
    expect(d).toHaveTextContent('Project: workspace');
    expect(d).toHaveTextContent('Kriya requires approval for operator-controlled configuration used by this project.');
    expect(screen.getByTestId('approval-scope')).toHaveTextContent('This approval applies only to the current protected configuration. If those settings change, Kriya will require approval again.');
    expect(d).toHaveTextContent(/Configuration fingerprint: [0-9a-f]{12}/);
    expect(d).toHaveTextContent('Only the names of the settings are shown');
    const technical = within(d).getByText('Technical details').closest('details')!;
    expect(technical).not.toHaveAttribute('open');
    expect(technical).toHaveTextContent(host.exec!.approvalStatus(WS).fingerprint!);
  });
  it('even if a host (wrongly) sent values, the contract refuses the answer and nothing but names is ever rendered', async () => {
    const host = new FixtureHost({ workspace: WS, execution: { outcome: 'success', configuration: { fields: ['mcp.hostile', 'llm.base_url'] } } });
    const real = host.query.bind(host);
    vi.spyOn(host, 'query').mockImplementation(async (request) => {
      const env = await real(request);
      if (request.operation !== 'configuration.approval_status' || !env.data) return env;
      const data = env.data as { protected_fields: Array<Record<string, unknown>> };
      return { ...env, data: { ...data, protected_fields: data.protected_fields.map((f) => ({ ...f, value: 'http://user:hunter2@host/v1', env: { API_TOKEN: 'tok-SECRET-1' } })) } } as typeof env;
    });
    mount(host);
    await reviewAndApprove();
    expect(await screen.findByText('The configuration could not be read')).toBeInTheDocument(); // the KUP schema forbids a value on a protected field (INVALID_RESPONSE)
    expect(approveButton()).toBeDisabled();
    expect(document.body.textContent).not.toMatch(/SECRET|hunter2|user:|http:\/\/user/);
  });
});

describe('D/E. Cancel approves nothing; Approve sends one typed request carrying only what was reviewed', () => {
  it('Cancel (and Escape) close the review without any approval request; focus opens on the safe choice', async () => {
    const host = await open();
    const d = await reviewAndApprove();
    expect(within(d).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    fireEvent.click(within(d).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    const again = await reviewAndApprove();
    fireEvent.keyDown(again, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(host.exec!.approvals).toHaveLength(0);
    expect(await card()).toBeInTheDocument(); // still required
  });
  it('Enter inside the review does not approve (no default-keyboard approval)', async () => {
    const host = await open();
    const d = await reviewAndApprove();
    await within(d).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.keyDown(d, { key: 'Enter' });
    fireEvent.keyPress(d, { key: 'Enter', charCode: 13 });
    expect(host.exec!.approvals).toHaveLength(0);
  });
  it('Approve Configuration is off until Kriya\'s status is read, then asks for exactly {workspace, fingerprint} - once', async () => {
    const host = await open();
    const d = await reviewAndApprove();
    await within(d).findByText('autonomy.mode', { selector: 'li' });
    const fingerprint = host.exec!.approvalStatus(WS).fingerprint;
    fireEvent.click(approveButton());
    fireEvent.click(screen.queryByRole('button', { name: 'Approving…' }) ?? approveButton()); // a second click while in flight reaches nothing
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(host.exec!.approvals).toEqual([{ workspace: WS, fingerprint }]);
    expect(Object.keys(host.exec!.approvals[0]!).sort()).toEqual(['fingerprint', 'workspace']);
  });
});

describe('F/G/H. after approval readiness is read again, the goal survives and Start Run turns on', () => {
  it('re-fetches the preflight from Kriya, keeps the typed goal, becomes Ready, enables Start Run, and the run then starts - no terminal, no retyping', async () => {
    const host = spyOnQueries(await open());
    await card();
    typeGoal();
    const before = calls(host, 'execution.preflight');
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(calls(host, 'execution.preflight')).toBeGreaterThan(before)); // readiness came from Kriya, not from the approval answer
    expect((await screen.findAllByText(/Configuration approved/)).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getAllByText(/^✓ Ready/).length).toBeGreaterThan(0));
    expect(screen.queryByRole('region', { name: 'Configuration approval' })).toBeNull();
    expect(goalBox().value).toBe(GOAL);
    await waitFor(() => expect(startButton()).toBeEnabled());
    fireEvent.click(startButton());
    await waitFor(() => expect(host.exec!.starts).toEqual([{ workspace: WS, goal: GOAL }]));
  });
  it('readiness is not concluded from the approval answer: Start Run stays off until Kriya\'s own new preflight says so', async () => {
    const host = await open();
    await card();
    typeGoal();
    const real = host.query.bind(host);
    let hold: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => { hold = resolve; });
    vi.spyOn(host, 'query').mockImplementation(async (request) => {
      if (request.operation === 'execution.preflight' && host.exec!.approvals.length > 0) await gate; // the re-check after the approval is slow
      return real(request);
    });
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(host.exec!.approvals).toHaveLength(1));
    expect(await screen.findByText(/Checking that this project is ready/)).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    hold!();
    await waitFor(() => expect(startButton()).toBeEnabled());
  });
});

describe('F2. Kriya\'s next readiness answer, not the approval answer, decides what the page shows', () => {
  it('if the configuration changed again right after the approval was recorded, the re-check puts the approval card back and Start Run stays off', async () => {
    const host = await open();
    typeGoal();
    const real = host.query.bind(host);
    let hold: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { hold = resolve; });
    vi.spyOn(host, 'query').mockImplementation(async (request) => {
      if (request.operation === 'execution.preflight' && host.exec!.approvals.length > 0) await gate;
      return real(request);
    });
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(host.exec!.approvals).toHaveLength(1));
    expect(await screen.findByText(/Checking that this project is ready/)).toBeInTheDocument();
    host.exec!.changeConfiguration(); // the approval Kriya recorded no longer covers what is there
    hold();
    expect(await screen.findByRole('button', { name: 'Review & Approve' })).toBeInTheDocument();
    expect(screen.queryByText(/Checking that this project is ready/)).toBeNull();
    expect(startButton()).toBeDisabled();
    expect(goalBox().value).toBe(GOAL);
  });
});

describe('I. another blocker is never hidden', () => {
  const OTHER = { code: 'TOOL_UNAVAILABLE', message: 'git is required for every run and exits 69' };
  it('shows the approval action AND the other blocker; after approval only the other blocker remains and Start Run stays off', async () => {
    const host = await open({ facts: { blockers: [OTHER], gitAvailable: false } });
    const c = await card();
    expect(c).toHaveTextContent('Configuration approval required');
    const also = screen.getByRole('alert');
    expect(also).toHaveTextContent('Also needs attention');
    expect(also).toHaveTextContent('TOOL_UNAVAILABLE');
    expect(also).not.toHaveTextContent('CONFIG_AUTHORITY_REFUSED');
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(host.exec!.approvals).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Configuration approval' })).toBeNull());
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Cannot start a run');
    expect(alert).toHaveTextContent('TOOL_UNAVAILABLE');
    typeGoal();
    expect(startButton()).toBeDisabled();
  });
});

describe('J. a configuration that changed while it was being reviewed is not approved', () => {
  it('the request carries the OLD fingerprint, Kriya refuses it as stale, the person is told to review again, the review reloads, and approving the new one is a second explicit click', async () => {
    const host = await open();
    typeGoal();
    const d = await reviewAndApprove();
    await within(d).findByText('autonomy.mode', { selector: 'li' });
    const reviewed = host.exec!.approvalStatus(WS).fingerprint;
    host.exec!.changeConfiguration([...FIELDS, 'execution_policy.network']); // the race: the configuration changes after Review, before Approve
    fireEvent.click(approveButton());
    expect(await within(await dialog()).findByText('Configuration changed while you were reviewing it. Review the updated configuration before approving.')).toBeInTheDocument();
    expect(host.exec!.approvals).toEqual([{ workspace: WS, fingerprint: reviewed }]);
    expect(host.exec!.approvalStatus(WS).status).toBe('required'); // NOT approved
    expect(await within(await dialog()).findByText('execution_policy.network', { selector: 'li' })).toBeInTheDocument(); // the reload shows the updated configuration
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(host.exec!.approvals).toHaveLength(1); // nothing was approved on the person's behalf
    fireEvent.click(approveButton());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(host.exec!.approvals).toHaveLength(2);
    expect(host.exec!.approvals[1]!.fingerprint).not.toBe(reviewed);
    expect(goalBox().value).toBe(GOAL);
  });
});

describe('K. an approval does not outlive the configuration it was given for', () => {
  it('after a protected setting changes, the next readiness check returns the page to "Configuration approval required"', async () => {
    const host = await open();
    typeGoal();
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(startButton()).toBeEnabled());
    host.exec!.changeConfiguration();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await card()).toHaveTextContent('Configuration approval required');
    await waitFor(() => expect(startButton()).toBeDisabled());
    expect(goalBox().value).toBe(GOAL);
    expect(screen.queryByText(/Configuration approved/)).toBeNull();
  });
  it('a revocation (as `kriya authority revoke`) is seen the same way', async () => {
    const host = await open();
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Configuration approval' })).toBeNull());
    host.exec!.revokeConfigurationApproval();
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await card()).toBeInTheDocument();
  });
});

describe('L. durability: the approval lives in Kriya, not in the window', () => {
  it('a brand-new window over the same backend shows the project ready without asking again', async () => {
    const host = await open();
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    await waitFor(() => expect(host.exec!.approvals).toHaveLength(1));
    cleanup(); // the window is closed
    mount(host);
    await screen.findByText('What do you want Kriya to do?');
    await waitFor(() => expect(screen.getAllByText(/^✓ Ready/).length).toBeGreaterThan(0));
    expect(screen.queryByRole('region', { name: 'Configuration approval' })).toBeNull();
    expect(host.exec!.approvals).toHaveLength(1);
  });
});

describe('typed failures are shown, never swallowed', () => {
  it('an approval that cannot be recorded here (trust file in effect) is explained and cannot be attempted', async () => {
    const host = await open({ configuration: { unavailable: { reason: 'trust_file_in_effect', message: 'A trust file is in effect for Kriya (KRIYA_TRUST_FILE), so approvals given here would not be consulted.' } } });
    const d = await reviewAndApprove();
    expect(await within(d).findByText(/An approval cannot be given from this application/)).toBeInTheDocument();
    expect(d).toHaveTextContent('trust file is in effect');
    expect(approveButton()).toBeDisabled();
    expect(host.exec!.approvals).toHaveLength(0);
  });
  it('a store that cannot be written reports Kriya\'s code and reason and leaves the project unapproved', async () => {
    await open({ configuration: { approveFault: { code: 'APPROVAL_UNAVAILABLE', message: 'The approval could not be saved: Permission denied', reason: 'store_unwritable' } } });
    await reviewAndApprove();
    await within(await dialog()).findByText('autonomy.mode', { selector: 'li' });
    fireEvent.click(approveButton());
    const failure = await screen.findByText('The configuration was not approved');
    expect(failure.closest('[role="alert"]')).toHaveTextContent('APPROVAL_UNAVAILABLE: store_unwritable');
    expect(failure.closest('[role="alert"]')).toHaveTextContent('Permission denied');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
  });
  it('a status that says required but not approvable keeps Approve off (the renderer follows can_approve, it does not infer it)', async () => {
    const host = new FixtureHost({ workspace: WS, execution: { outcome: 'success', configuration: { fields: FIELDS } } });
    const real = host.query.bind(host);
    vi.spyOn(host, 'query').mockImplementation(async (request) => {
      const env = await real(request);
      if (request.operation !== 'configuration.approval_status' || !env.data) return env;
      return { ...env, data: { ...(env.data as object), can_approve: false, blocking_reason: 'Kriya says this cannot be approved here.' } } as typeof env;
    });
    mount(host);
    await reviewAndApprove();
    await screen.findByText('autonomy.mode', { selector: 'li' });
    expect(approveButton()).toBeDisabled();
    expect(host.exec!.approvals).toHaveLength(0);
  });
  it('a status Kriya cannot read is a typed error with a retry, and approval stays off', async () => {
    const host = new FixtureHost({ workspace: WS, execution: { outcome: 'success', configuration: { fields: FIELDS } } });
    const real = host.query.bind(host);
    vi.spyOn(host, 'query').mockImplementation(async (request) => (request.operation === 'configuration.approval_status' && document.querySelector('[role="dialog"]')
      ? { schema_version: 1, operation: 'configuration.approval_status', request_id: 'x', observed_at: '2026-10-14T00:00:00Z', source: null, consistency: null, data: null, error: { code: 'CONFIG_LOAD_FAILED', message: 'the configuration cannot be read: ParserError' } } as never
      : real(request)));
    mount(host);
    await reviewAndApprove();
    expect(await screen.findByText('The configuration could not be read')).toBeInTheDocument();
    expect(approveButton()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
  it('a host with no way to record an approval keeps the plain blocker and the technical fallback; there is no Review & Approve to press', async () => {
    await open({ configuration: { noApprovalPort: true } });
    expect(await screen.findByText(/Cannot start a run/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review & Approve' })).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('This window cannot record the approval');
  });
});

describe('M. the renderer holds no authority and touches no authority file', () => {
  it('the approval UI reaches Kriya only through the provider status read and the typed execution port', () => {
    const ui = approvalSource;
    const core = executionSource;
    expect(ui + core).not.toMatch(/from 'node:|from 'fs'|child_process|require\(/);
    expect(ui).not.toMatch(/~\/\.kriya|\/authority\/|KRIYA_AUTHORITY_HOME|KRIYA_TRUST_FILE|localStorage|sessionStorage|\.yaml/);
    expect(ui.match(/ex\.approveConfiguration\(/g)).toHaveLength(1);
    expect(ui).toMatch(/provider\.configurationApprovalStatus\(/);
    expect(ui).not.toMatch(/\bfetch\(|XMLHttpRequest|ipcRenderer|window\.kriyaHost/);
  });
  it('the approval request type has exactly workspace and fingerprint', () => {
    expect(hostAdapterSource).toMatch(/approveConfiguration\?\(request: ApproveConfigurationRequest\)/);
  });
});
