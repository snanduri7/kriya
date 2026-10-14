/** The Skills page (Demo V2): read-only visibility and provenance. Every state is shown, nothing can be changed, and what is shown is what Kriya's `skills.*` operations returned. */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FixtureHostOptions } from '../../providers/fixture';
import { renderWorkspaceShell } from '../../testing/harness';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
async function openSkills(fixture: FixtureHostOptions = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Skills');
  await screen.findByRole('heading', { name: 'Skills' });
  return mounted;
}
const MUTATIONS = /install|remove|delete|enable|disable|edit|approve|unverify|promote|import|add skill|update/i;

describe('Skills page', () => {
  it('says it is asking Kriya while the answer is on its way, instead of showing an empty page', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Skills');
    expect(screen.getByText('Asking Kriya about Skills…')).toBeInTheDocument();
    await screen.findByRole('table', { name: 'Skills' });
    expect(screen.queryByText('Asking Kriya about Skills…')).toBeNull();
  });

  it('lists each Skill with its verification state, rule counts and scope', async () => {
    await openSkills();
    const table = await screen.findByRole('table', { name: 'Skills' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText('python-testing')).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent(/verified 2026-10-01/);
    expect(rows[0]).toHaveTextContent('install');
    expect(rows[1]).toHaveTextContent(/unverified/);
    expect(within(rows[1]!).getByText('3')).toBeInTheDocument(); // staged rules badge
    expect(rows[1]).toHaveTextContent('workspace');
  });

  it('offers no way to change anything: no install, remove, enable, edit, approve or unverify control', async () => {
    await openSkills();
    await screen.findByRole('table', { name: 'Skills' });
    for (const button of screen.getAllByRole('button')) expect(button.getAttribute('aria-label') ?? button.textContent ?? '').not.toMatch(MUTATIONS);
    expect(screen.queryAllByRole('textbox')).toHaveLength(0);
    expect(screen.getByText(/this application does not change them/i)).toBeInTheDocument();
  });

  it('choosing a Skill shows its provenance sources, counts and digest, and says what is not known', async () => {
    await openSkills();
    fireEvent.click(await screen.findByRole('button', { name: 'Show python-testing' }));
    const detail = await screen.findByRole('region', { name: 'Skill python-testing' });
    expect(detail).toHaveTextContent('Skill version');
    expect(detail).toHaveTextContent(/a skill has no version of its own/);
    expect(detail).toHaveTextContent('sha256:1a1a1a1a1a1a…'); // shown shortened; Copy puts the full digest on the clipboard
    const sources = within(detail).getByRole('table', { name: 'Sources of python-testing' });
    expect(within(sources).getByText('https://docs.pytest.org/en/stable/how-to/fixtures.html')).toBeInTheDocument();
    expect(within(sources).getAllByRole('row')).toHaveLength(3);
    expect(detail).toHaveTextContent('8 of 12'); // recorded sources / rules
    expect(detail).toHaveTextContent(/rules is not shown here|text of the rules is not shown/i);
    expect(within(detail).queryByRole('link')).toBeNull(); // a recorded source is text, never a navigation target
  });

  it('an unverified Skill with no recorded provenance says "not the same as verified" and shows no source table', async () => {
    await openSkills();
    fireEvent.click(await screen.findByRole('button', { name: 'Show spring-boot' }));
    const detail = await screen.findByRole('region', { name: 'Skill spring-boot' });
    expect(detail).toHaveTextContent(/No provenance is recorded\. These rules are pre-existing content, which is not the same as verified\./);
    expect(within(detail).queryByRole('table')).toBeNull();
    expect(detail).toHaveTextContent('3 staged and awaiting review');
  });

  it('no Skills: says so, never an empty table', async () => {
    await openSkills({ contextMode: 'empty' });
    expect(await screen.findByText('No Skills were discovered for this folder.')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Skills' })).toBeNull();
  });

  it('an unresolvable configuration is shown as that, not as "no Skills"', async () => {
    await openSkills({ contextMode: 'unavailable' });
    expect(await screen.findByText(/Kriya could not resolve this folder's configuration: ValueError: Failed to load configuration \(fixture\) \(CONFIG_LOAD_FAILED\)/)).toBeInTheDocument();
    expect(screen.queryByText('No Skills were discovered for this folder.')).toBeNull();
  });

  it('a long list is rendered in full from the first answer and the page stays usable', async () => {
    await openSkills({ contextMode: 'many' });
    const table = await screen.findByRole('table', { name: 'Skills' });
    expect(within(table).getAllByRole('row')).toHaveLength(121);
  });

  it('without an open workspace the page explains instead of asking Kriya anything', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null } });
    await screen.findByRole('navigation', { name: 'Destinations' }).catch(() => null);
    expect(host.calls.filter((c) => c.operation.startsWith('skills.'))).toEqual([]);
  });

  it('Refresh asks Kriya again; selecting a Skill uses the describe operation with the Skill id, never a path', async () => {
    const { host } = await openSkills();
    await screen.findByRole('table', { name: 'Skills' });
    const lists = () => host.calls.filter((c) => c.operation === 'skills.list').length;
    expect(lists()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(lists()).toBe(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Show spring-boot' }));
    await screen.findByRole('region', { name: 'Skill spring-boot' });
    const describe_ = host.calls.filter((c) => c.operation === 'skills.describe');
    expect(describe_).toEqual([{ operation: 'skills.describe', workspace: '/fixture/workspace', skill_id: 'spring-boot' }]);
  });
});
