/** Readiness and run setup: what Kriya reports a run here would have. Every row is a field of Kriya's answer; the model check is one measured probe; Skills and tools are
 * shown, never edited; nothing here is a score and nothing is invented. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../../../state/selection';
import { createCockpitRegistry } from '../../composition';
import { createSelectionStore } from '../../core';
import { FixtureHost, type FixtureHostOptions } from '../../providers/fixture';
import type { FixtureExecutionOptions } from '../../providers/fixture/fixtureExecution';
import { HostProvider } from '../../providers/hostProvider';
import { FAST_POLL, renderWorkspaceShell } from '../../testing/harness';
import { AppShell } from '../../shell/AppShell';
import type { KupEnvelope, KupRequest } from '../../../model/kup';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
async function openPanel(fixture: FixtureHostOptions = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  await screen.findAllByText(/^✓ Ready|Cannot start/);
  const readiness = await screen.findByRole('region', { name: 'Readiness' }); // under Project details: the compact summary is what sits above Start Run
  return { ...mounted, readiness };
}
const section = (summary: RegExp) => screen.getByText(summary, { selector: 'summary' }).closest('details') as HTMLElement;
const row = (readiness: HTMLElement, label: string) => within(readiness).getByText(label).closest('li') as HTMLElement;

describe('readiness', () => {
  it('lists the facts Kriya reported - workspace, configuration, state, git, model server, approvals - with no score', async () => {
    const { readiness } = await openPanel();
    expect(within(readiness).getByText('ready')).toBeInTheDocument();
    for (const label of ['Workspace', 'Configuration', 'Run state', 'Git (needed by every run)', 'Model server', 'Approvals']) expect(within(readiness).getByText(label)).toBeInTheDocument();
    await waitFor(() => expect(within(row(readiness, 'Model server')).getByText('Answering; configured models are served')).toBeInTheDocument());
    expect(within(row(readiness, 'Approvals')).getByText('Every change is held for your review')).toBeInTheDocument();
    expect(readiness.textContent).not.toMatch(/\b\d+\s*\/\s*10\b|score|grade/i);
  });

  it('a model that the server does not serve is a red row saying which model and who can fix it; Start Run stays Kriya\'s decision', async () => {
    const { readiness } = await openPanel({ probeMode: 'missing' });
    const model = row(readiness, 'Model server');
    await waitFor(() => expect(within(model).getByText('A configured model is not served')).toBeInTheDocument());
    expect(model).toHaveTextContent('Not served: fixture-model:7b, fixture-planner:14b');
    expect(model).toHaveTextContent('Pull or load the model on the server');
    expect(within(readiness).getByText('ready to start, with a warning')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Do it' } });
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeEnabled(); // the probe informs; Kriya's preflight alone gates Start
  });

  it('an unreachable model server says so with the reason and that this application cannot start it', async () => {
    const { readiness } = await openPanel({ probeMode: 'unreachable' });
    const model = row(readiness, 'Model server');
    await waitFor(() => expect(within(model).getByText('Not reachable')).toBeInTheDocument());
    expect(model).toHaveTextContent('Connection refused');
    expect(model).toHaveTextContent('Start the model server (for example Ollama); this application cannot.');
  });

  it('a non-local endpoint under local_only is "Not checked", never contacted, and says why', async () => {
    const { readiness } = await openPanel({ probeMode: 'not_probed' });
    const model = row(readiness, 'Model server');
    await waitFor(() => expect(within(model).getByText('Not checked')).toBeInTheDocument());
    expect(model).toHaveTextContent('non-local endpoint, not contacted under local_only');
  });

  it('no model probe is made while a run is in progress (the persistent service must stay free for the run\'s own reads); it is asked again after the run ends', async () => {
    const { host } = await openPanel();
    const probes = () => host.calls.filter((c) => c.operation === 'execution.model_probe').length;
    await waitFor(() => expect(probes()).toBe(1));
    fireEvent.change(screen.getByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Do it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Run' }));
    await screen.findByRole('region', { name: 'Current run' });
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await new Promise((r) => setTimeout(r, 200));
    expect(probes()).toBe(1);
  });

  it('the probe is asked once per check and again only when the person checks again', async () => {
    const { host, readiness } = await openPanel();
    await waitFor(() => expect(within(row(readiness, 'Model server')).getByText(/Answering/)).toBeInTheDocument());
    const probes = () => host.calls.filter((c) => c.operation === 'execution.model_probe').length;
    expect(probes()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(probes()).toBe(2));
  });

  it.each([
    ['/fixture/blocked', 'RECOVERY_REQUIRED', /kriya runs recover/],
  ])('a blocker (%s) shows Kriya\'s message, its code and which authority acts next', async (workspace, code, fix) => {
    await openPanel({ workspace });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Cannot start a run');
    expect(alert).toHaveTextContent(code);
    expect(alert).toHaveTextContent(fix);
    expect(alert).toHaveTextContent('The last run left the folder mid-change. Recover it with `kriya runs recover` in a terminal, then check again.'); // the authority-next guidance (not just Kriya\'s own message)
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeDisabled();
    expect(within(screen.getByRole('region', { name: 'Readiness' })).getByText('not ready')).toBeInTheDocument();
  });
});

describe('probeStateFor matches a configured model to the probe by BOTH model and endpoint', () => {
  const probe = (roles: Array<{ for: string; model: string; endpoint: string; state: string }>) => ({
    state: 'ready', provenance: {} as never,
    data: { available: true, unavailable_reason: null, authority_status: 'no_findings', endpoints: [], roles: roles.map((r) => ({ ...r, available: r.state === 'model_present' })) },
  }) as never;
  it('the same model name on another server is not the same binding', async () => {
    const { probeStateFor } = await import('./SetupSections');
    const p = probe([{ for: 'default', model: 'm', endpoint: 'http://localhost:11434/v1', state: 'model_present' }, { for: 'planner', model: 'm', endpoint: 'http://localhost:9/v1', state: 'unreachable' }]);
    expect(probeStateFor({ model: 'm', endpoint: 'http://localhost:11434/v1' }, p)).toBe('model_present');
    expect(probeStateFor({ model: 'm', endpoint: 'http://localhost:9/v1' }, p)).toBe('unreachable');
    expect(probeStateFor({ model: 'm', endpoint: 'http://localhost:7/v1' }, p)).toBe('unknown');
    expect(probeStateFor({ model: 'other', endpoint: 'http://localhost:11434/v1' }, p)).toBe('unknown');
  });
});

describe('Skills', () => {
  it('are listed with Kriya\'s own metadata and state they are selected automatically - there is no way to select, add, edit or build one here', async () => {
    await openPanel();
    const skills = section(/^Skills/);
    expect(skills).toHaveTextContent('Skills · 2 (1 unverified)');
    const body = await within(skills).findByText('Selected automatically by Kriya.');
    expect(body).toBeInTheDocument();
    const list = within(skills).getByRole('list', { name: 'Skills Kriya can use' });
    expect(within(list).getByText('python-testing')).toBeInTheDocument();
    expect(within(list).getByText(/verified 2026-10-01/)).toBeInTheDocument();
    expect(within(list).getByText('unverified')).toBeInTheDocument();
    expect(within(list).getByText('3 staged rules awaiting review')).toBeInTheDocument();
    expect(within(list).getByText(/part of the Kriya installation/)).toBeInTheDocument();
    expect(skills).toHaveTextContent('can stop a desktop run');
    expect(skills).toHaveTextContent('kriya skills');
    // no control of any kind that selects, adds, builds, imports or edits
    expect(within(skills).queryAllByRole('checkbox')).toHaveLength(0);
    expect(within(skills).queryAllByRole('radio')).toHaveLength(0);
    expect(within(skills).queryAllByRole('combobox')).toHaveLength(0);
    expect(within(skills).queryAllByRole('textbox')).toHaveLength(0);
    expect(within(skills).queryAllByRole('button')).toHaveLength(0);
    expect(skills.textContent).not.toMatch(/\bimport\b|\bbuild\b.*skill|from URL|add skill/i);
  });

  it('shows no rule or instruction text and offers Show all only past a dozen', async () => {
    await openPanel({ contextMode: 'many' });
    const skills = section(/^Skills/);
    const list = await within(skills).findByRole('list', { name: 'Skills Kriya can use' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(12);
    fireEvent.click(within(skills).getByRole('button', { name: 'Show all 300' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(300);
    fireEvent.click(within(skills).getByRole('button', { name: 'Show fewer' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(12);
  });

  it('no Skills discovered is stated plainly', async () => {
    await openPanel({ contextMode: 'empty' });
    expect(await within(section(/^Skills/)).findByText('No Skills were discovered for this folder.')).toBeInTheDocument();
  });

  it('a folder whose configuration does not load says so (with Kriya\'s code) instead of showing an empty list', async () => {
    await openPanel({ contextMode: 'unavailable' });
    const skills = section(/^Skills/);
    expect(await within(skills).findByText(/Kriya could not resolve this folder's configuration/)).toBeInTheDocument();
    expect(skills).toHaveTextContent('CONFIG_LOAD_FAILED');
    expect(within(skills).queryByRole('list')).toBeNull();
  });
});

describe('Tools and capabilities', () => {
  it('shows configured plugins and MCP servers with their declared capabilities, says they are operator-managed, and offers no control, command, argument or credential', async () => {
    await openPanel();
    const tools = section(/^Tools and capabilities/);
    expect(tools).toHaveTextContent('Tools and capabilities · 2 MCP servers');
    await within(tools).findByText(/Managed by Kriya \/ operator configuration/);
    const list = within(tools).getByRole('list', { name: 'MCP servers' });
    expect(within(list).getByText('filesystem')).toBeInTheDocument();
    expect(list).toHaveTextContent('reads the workspace');
    expect(list).toHaveTextContent('network: allowed');
    expect(within(list).getAllByText('health not checked')).toHaveLength(2);
    expect(tools).toHaveTextContent('core_tools');
    expect(tools).toHaveTextContent('kriya mcp approve');
    for (const role of ['button', 'textbox', 'checkbox', 'combobox']) expect(within(tools).queryAllByRole(role), role).toHaveLength(0);
    expect(tools.textContent).not.toMatch(/--token|API_TOKEN|command:|args:|env:/);
  });

  it('none configured is said plainly', async () => {
    await openPanel({ contextMode: 'empty' });
    expect(await within(section(/^Tools and capabilities/)).findByText('No MCP servers are configured for this folder.')).toBeInTheDocument();
  });
});

describe('Model routing', () => {
  it('shows every role with its model, server, source and measured availability, and offers no model picker', async () => {
    await openPanel();
    const routing = section(/^Model routing/);
    const table = await within(routing).findByRole('table', { name: 'Model routing' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['Planner', 'Architect', 'Developer', 'Reviewer', 'Run verifier', 'Skill-gap check', 'Spec compliance']);
    expect(rows[0]).toHaveTextContent('fixture-planner:14b');
    expect(rows[0]).toHaveTextContent('role override');
    expect(rows[2]).toHaveTextContent('fixture-model:7b → fixture-fallback:3b');
    await waitFor(() => expect(within(rows[0]!).getByText('served')).toBeInTheDocument());
    expect(within(routing).queryAllByRole('combobox')).toHaveLength(0);
    expect(within(routing).queryAllByRole('button')).toHaveLength(0);
  });

  it('the availability column follows the probe: a missing model is flagged on exactly the roles that use it', async () => {
    await openPanel({ probeMode: 'missing' });
    const table = await within(section(/^Model routing/)).findByRole('table', { name: 'Model routing' });
    await waitFor(() => expect(within(table).getAllByText('not served by the server').length).toBeGreaterThan(0));
  });
});

class GatedHost extends FixtureHost {
  gates: Array<{ match: (r: KupRequest) => boolean; open: boolean; wait: Promise<void>; release: () => void }> = [];
  hold(match: (r: KupRequest) => boolean) {
    let release: () => void = () => undefined;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const gate = { match, open: false, wait, release: () => undefined as void };
    gate.release = () => { gate.open = true; release(); };
    this.gates.push(gate);
    return gate;
  }
  override async query(request: KupRequest): Promise<KupEnvelope> {
    const gate = this.gates.find((g) => !g.open && g.match(request));
    if (gate) await gate.wait;
    return super.query(request);
  }
}

describe('stale answers never overwrite a newer workspace', () => {
  it('a slow Skills/probe answer for workspace A that lands after switching to workspace B is dropped', async () => {
    cleanup();
    const host = new GatedHost({ workspace: '/fixture/workspace', choices: ['/fixture/empty'], contextModeByWorkspace: { '/fixture/empty': 'empty' }, probeModeByWorkspace: { '/fixture/empty': 'unreachable' } });
    const slow = host.hold((r) => (r.operation === 'execution.context' || r.operation === 'execution.model_probe') && (r as { workspace?: string }).workspace === '/fixture/workspace');
    render(<AppShell provider={new HostProvider(host, 'fixture')} registry={createCockpitRegistry()} selection={createSelectionStore({ ...initialSelection })} viewportWidth={1440} pollIntervals={FAST_POLL} />);
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    host.menu('openFolder'); // B opens while A's context and probe are still held
    await waitFor(() => expect(host.workspaceCalls).toContain('remember:/fixture/empty'));
    goTo('Project');
    await screen.findAllByText(/^✓ Ready/);
    const readiness = await screen.findByRole('region', { name: 'Readiness' });
    await waitFor(() => expect(within(row(readiness, 'Model server')).getByText('Not reachable')).toBeInTheDocument());
    await within(section(/^Skills/)).findByText('No Skills were discovered for this folder.');
    act(() => slow.release()); // A's late answers land now
    await new Promise((r) => setTimeout(r, 200));
    expect(within(row(screen.getByRole('region', { name: 'Readiness' }), 'Model server')).getByText('Not reachable')).toBeInTheDocument(); // still B's
    expect(within(section(/^Skills/)).getByText('No Skills were discovered for this folder.')).toBeInTheDocument();
    expect(within(section(/^Skills/)).queryByText('python-testing')).toBeNull();
  });
});

describe('readiness matrix: each failure names Kriya\'s fact, its code, and the authority that fixes it', () => {
  const open = (facts: FixtureExecutionOptions['facts'], extra: FixtureHostOptions = {}) => openPanel({ execution: { outcome: 'success', facts }, ...extra });
  it('git unavailable: the Git row is red with the reason, the blocker says what to do, Start is off', async () => {
    const { readiness } = await open({ gitAvailable: false, blockers: [{ code: 'TOOL_UNAVAILABLE', message: 'git is required for every run and exits 69' }] });
    const git = row(readiness, 'Git (needed by every run)');
    expect(within(git).getByText('Unavailable')).toBeInTheDocument();
    expect(git).toHaveTextContent('the Xcode license has not been accepted');
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('TOOL_UNAVAILABLE');
    expect(alert).toHaveTextContent('accepting the Xcode license makes /usr/bin/git work');
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeDisabled();
  });
  it('configuration authority refused: the Configuration row is red and names the terminal action an operator takes', async () => {
    const { readiness } = await open({ authority: 'refused', blockers: [{ code: 'CONFIG_AUTHORITY_REFUSED', message: '2 field(s) in this folder\'s configuration need an operator approval' }] });
    const config = row(readiness, 'Configuration');
    expect(within(config).getByText(/Refused: operator-only fields need an approval/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('`kriya authority approve` in a terminal; this application cannot');
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeDisabled();
  });
  it('configuration that does not load: red, "unreadable", with the fix being the kriya.yaml; routing and Skills say they cannot be shown', async () => {
    const { readiness } = await open({ configFailed: true, blockers: [{ code: 'CONFIG_LOAD_FAILED', message: 'ValueError: Failed to load configuration' }] }, { contextMode: 'unavailable' });
    expect(within(row(readiness, 'Configuration')).getByText('The configuration could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Fix the kriya.yaml in this folder');
    expect(await within(section(/^Model routing/)).findByText(/Kriya could not resolve this folder's configuration/)).toBeInTheDocument();
  });
  it('state directory not resolved / not read by this app: red rows and the operator-environment guidance', async () => {
    const a = await open({ stateDirectory: null, blockers: [{ code: 'STATE_DIRECTORY_INVALID', message: 'KRIYA_STATE_DIR must be absolute' }] });
    expect(within(row(a.readiness, 'Run state')).getByText('Not resolved')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('comes from the operator environment (KRIYA_STATE_DIR)');
    cleanup();
    const b = await open({ observable: false, blockers: [{ code: 'STATE_DIRECTORY_NOT_OBSERVABLE', message: 'a run here would record into /elsewhere' }] });
    expect(within(row(b.readiness, 'Run state')).getByText('Not read by this app')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('An operator must align KRIYA_STATE_DIR');
  });
  it('unavailable toolchains are listed (java, mvn) but are not blockers: Kriya only learns what a goal needs while it runs', async () => {
    await openPanel();
    fireEvent.click(screen.getByText('Run configuration details'));
    const details = section(/^Run configuration details/);
    expect(details).toHaveTextContent('unavailable: java, mvn');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeDisabled(); // (only because no goal is entered yet)
  });
  it('an operator-configured autonomy mode that does not hold changes for review is a warning row quoting Kriya, not a control', async () => {
    const { readiness } = await open({ approvals: 'configured_mode' });
    const approvals = row(readiness, 'Approvals');
    expect(within(approvals).getByText('Autonomy mode autonomous')).toBeInTheDocument();
    expect(approvals).toHaveTextContent('not held for review by this configuration');
    expect(within(approvals).queryAllByRole('button')).toHaveLength(0);
  });
});
