/**
 * Project Setup from the Project page (greenfield journey): a new folder with no configuration is offered Kriya's recommended setup - never required, because the folder
 * already runs on the defaults - and creating it leads into the ordinary configuration approval. The renderer holds no authority and writes nothing: every assertion is about
 * what it asks the host (the scripted fixture host stands for the real one) and how it renders the answers. The backend itself is pinned in tests/test_kup_project_init.py and
 * ui/standalone/test/project_setup_real.test.ts.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FixtureExecutionOptions } from '../../providers/fixture/fixtureExecution';
import { renderWorkspaceShell } from '../../testing/harness';
import setupSource from './ProjectSetup.tsx?raw';

const WS = '/fixture/empty';
const GOAL = 'Create a small Java 17 Maven application named hello-kriya that prints Hello from Kriya.';
const AFTER = ['autonomy.mode', 'llm.base_url', 'llm_chain', 'workflow_controller.enabled', 'workflow_controller.mode'];

type Config = NonNullable<FixtureExecutionOptions['configuration']>;
async function open(configuration: Config | undefined, extra: Partial<FixtureExecutionOptions> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: WS, execution: { outcome: 'success', ...(configuration ? { configuration } : {}), ...extra } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  await screen.findByText('What do you want Kriya to do?');
  return mounted.host;
}
const unconfigured = (over: Partial<Config> = {}): Config => ({ fields: [], unconfigured: { fieldsAfterSetup: AFTER }, ...over });
const card = () => screen.findByRole('region', { name: 'Project setup' });
const goalBox = () => screen.getByRole('textbox', { name: 'Engineering goal' }) as HTMLTextAreaElement;
const startButton = () => screen.getByRole('button', { name: /^Start Run$|^Starting…$/ });
const typeGoal = () => fireEvent.change(goalBox(), { target: { value: GOAL } });

describe('1. a new folder with no configuration is offered Project Setup', () => {
  it('shows the card with what setup applies - from what Kriya reports, no raw setting names - and says it is recommended, not required', async () => {
    await open(unconfigured());
    const c = await card();
    expect(c).toHaveTextContent('Set up this project');
    expect(c).toHaveTextContent('Recommended');
    expect(c).toHaveTextContent('fixture-model:7b'); // the model Kriya reports it would use
    expect(c).toHaveTextContent('fixture-fallback:3b');
    expect(c).toHaveTextContent('Held for my approval');
    expect(c).toHaveTextContent('Local only; live web lookup off');
    for (const jargon of ['autonomy.mode', 'llm_chain', 'paths.memory', 'paths.skills', 'workflow_controller']) expect(c.textContent).not.toContain(jargon);
    expect(within(c).getByRole('button', { name: 'Create Project Configuration' })).toBeEnabled();
  });
  it('is never a gate: the goal box and Start Run are already usable (the folder runs on the defaults)', async () => {
    await open(unconfigured());
    await card();
    typeGoal();
    await waitFor(() => expect(startButton()).toBeEnabled());
  });
  it('the advanced disclosure explains what is written without values or paths', async () => {
    await open(unconfigured());
    const c = await card();
    const adv = within(c).getByText('Advanced configuration').closest('details')!;
    expect(adv.open).toBe(false);
    expect(within(c).getByTestId('setup-advanced')).toHaveTextContent(/kriya\.yaml/);
    expect(within(c).getByTestId('setup-advanced')).toHaveTextContent(/approve them before Kriya uses them/);
  });
});

describe('2-7. setup goes through the backend and into the ordinary approval', () => {
  it('Create asks the host with exactly {workspace}; the card goes; ACTION REQUIRED appears; the goal survives; nothing was approved by setup', async () => {
    const host = await open(unconfigured());
    await card();
    typeGoal();
    fireEvent.click(screen.getByRole('button', { name: 'Create Project Configuration' }));
    expect(await screen.findByRole('region', { name: 'Configuration approval' })).toHaveTextContent('Configuration approval required');
    expect(host.exec!.setups).toEqual([{ workspace: WS }]);
    expect(Object.keys(host.exec!.setups[0]!)).toEqual(['workspace']); // no setting, model, path or content
    expect(host.exec!.approvals).toEqual([]); // setup approved nothing
    expect(screen.queryByRole('region', { name: 'Project setup' })).toBeNull();
    expect(goalBox().value).toBe(GOAL);
    await waitFor(() => expect(startButton()).toBeDisabled()); // the protected settings are unapproved: not ready
  });
  it('the full journey: Setup -> Review & Approve -> native confirmation -> Ready -> Start Run enabled, goal intact -> the run starts', async () => {
    const host = await open(unconfigured());
    await card();
    typeGoal();
    fireEvent.click(screen.getByRole('button', { name: 'Create Project Configuration' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Review & Approve' }));
    const dialog = await screen.findByRole('dialog', { name: 'Configuration approval' });
    await within(dialog).findByText('workflow_controller.mode', { selector: 'li > span.mono' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue to Approval…' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(host.exec!.confirmationsShown).toBe(1);
    expect(host.exec!.approvals).toHaveLength(1);
    await waitFor(() => expect(screen.getAllByText(/^✓ Ready/).length).toBeGreaterThan(0));
    expect(goalBox().value).toBe(GOAL);
    await waitFor(() => expect(startButton()).toBeEnabled());
    fireEvent.click(startButton());
    await waitFor(() => expect(host.exec!.starts).toEqual([{ workspace: WS, goal: GOAL }]));
  });
  it('the renderer creates no file: the setup card imports no file API and the only way to a configuration is the typed host port', () => {
    expect(setupSource).not.toMatch(/from 'node:|require\(|fs\.|writeFile|fetch\(|XMLHttpRequest|kriya\.yaml['"`]\s*[,)]/);
    expect(setupSource).toMatch(/ex\.initializeProject\(\)/);
  });
});

describe('8/9. when Setup is not offered and typed failures', () => {
  it('a folder that already has a configuration is not offered Setup', async () => {
    await open({ fields: AFTER });
    expect(screen.queryByRole('region', { name: 'Project setup' })).toBeNull();
    expect(await screen.findByRole('region', { name: 'Configuration approval' })).toBeInTheDocument(); // it needs approval instead
  });
  it('a configuration Kriya cannot read shows the blocker and no setup card (Setup never overwrites or papers over a broken file)', async () => {
    await open(undefined, { facts: { configFailed: true } });
    expect(screen.queryByRole('region', { name: 'Project setup' })).toBeNull();
    expect((await screen.findAllByText(/configuration/i)).length).toBeGreaterThan(0);
    expect(startButton()).toBeDisabled();
  });
  it('a host with no way to create a configuration offers no setup (a browser test host)', async () => {
    await open(unconfigured({ noSetupPort: true }));
    expect(screen.queryByRole('region', { name: 'Project setup' })).toBeNull();
  });
  it('a configuration that appeared meanwhile is explained and nothing was overwritten; the page then reads it again', async () => {
    const host = await open(unconfigured({ unconfigured: { fieldsAfterSetup: AFTER, fault: { code: 'INVALID_REQUEST', message: 'this folder already has kriya.yaml', reason: 'already_configured' } } }));
    await card();
    fireEvent.click(screen.getByRole('button', { name: 'Create Project Configuration' }));
    const alert = await screen.findByText(/This folder already has a Kriya configuration, so nothing was changed/);
    expect(alert).toBeInTheDocument();
    expect(host.exec!.setups).toHaveLength(1);
    expect(host.exec!.approvals).toEqual([]);
  });
  it('an unwritable folder says so in words and offers another try', async () => {
    await open(unconfigured({ unconfigured: { fieldsAfterSetup: AFTER, fault: { code: 'WORKSPACE_UNAVAILABLE', message: 'the configuration could not be created in this folder: Permission denied', reason: 'unwritable' } } }));
    await card();
    fireEvent.click(screen.getByRole('button', { name: 'Create Project Configuration' }));
    expect(await screen.findByText(/Kriya could not write to this folder/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create Project Configuration' })).toBeEnabled();
  });
  it('a double click asks once', async () => {
    const host = await open(unconfigured());
    await card();
    const create = screen.getByRole('button', { name: 'Create Project Configuration' });
    fireEvent.click(create);
    fireEvent.click(create);
    await screen.findByRole('region', { name: 'Configuration approval' });
    expect(host.exec!.setups).toHaveLength(1);
  });
});
