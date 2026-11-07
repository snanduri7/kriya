/**
 * GREENFIELD-OS-METADATA-BLOCKS-FIRST-RUN-001 (UI half): a run Kriya refused to START because a new project folder holds files it does not own gets its OWN customer-facing card. The words are
 * selected by the repository layer's reason code recorded on the run's `membership_authority.unavailable` event, and the blocking names are the ones the backend recorded; the renderer never
 * parses text and never guesses a cause (a run with a different stop, or no such event, keeps its generic sentence).
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { membershipStopWords } from '../core';
import { renderWorkspaceShell } from '../testing/harness';

const banner = () => screen.findByRole('region', { name: 'Current run' });

async function refused(membershipStop?: { paths: string[] }) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'failure', ...(membershipStop ? { membershipStop } : {}) } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Create hello.py that prints hello world' } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 4; i += 1) act(() => mounted.host.exec!.advance());
  await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'failure'));
  return bar;
}

describe('the refused-to-start card', () => {
  it('says the folder holds files Kriya does not own, names the blocking entries, and offers only supported next steps', async () => {
    const bar = await refused({ paths: ['README.md', 'notes.txt'] });
    const card = await within(bar).findByTestId('membership-stop');
    expect(card).toHaveTextContent("Kriya can't start this new project because the folder already contains files that it does not own.");
    expect(within(card).getByTestId('membership-stop-names')).toHaveTextContent('In the way: README.md, notes.txt');
    expect(card).toHaveTextContent('Choose an empty folder for a new project');
    expect(card).toHaveTextContent('git init, then commit');
    expect(card).toHaveTextContent('Nothing was generated and nothing in this folder was changed.');
    expect(card.textContent).not.toMatch(/BOUND_TREE|AUTHORITY_EXECUTION|\/fixture\/workspace/);       // no raw codes, no raw path
    expect(within(bar).queryByTestId('failure-summary')).toBeNull();                                    // it replaces the generic sentence
    expect(within(bar).queryByRole('button', { name: /git init|initialize/i })).toBeNull();            // no button for something the product does not do
  });

  it('without recorded names the card still stands and invents none', async () => {
    const bar = await refused({ paths: [] });
    const card = await within(bar).findByTestId('membership-stop');
    expect(within(card).queryByTestId('membership-stop-names')).toBeNull();
  });

  it('a run with no such recorded event keeps the generic failure sentence (the renderer does not infer the reason)', async () => {
    const bar = await refused();
    expect(await within(bar).findByTestId('failure-summary')).toBeInTheDocument();
    expect(within(bar).queryByTestId('membership-stop')).toBeNull();
  });
});

describe('membershipStopWords', () => {
  it('only the repository layer\'s own reason code selects the words', () => {
    expect(membershipStopWords('BOUND_TREE_UNAVAILABLE', ['x'])).toBeNull();
    expect(membershipStopWords(null, [])).toBeNull();
    expect(membershipStopWords(undefined, [])).toBeNull();
    expect(membershipStopWords('BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY', [])).not.toBeNull();
  });
  it('bounds the names it shows: five at most, each shortened, with a count of the rest', () => {
    const many = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];
    expect(membershipStopWords('BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY', many)!.names).toBe('In the way: a, b, c, d, e and 2 more');
    const long = membershipStopWords('BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY', ['x'.repeat(200)])!.names!;
    expect(long.length).toBeLessThan(90);
    expect(long.endsWith('…')).toBe(true);
  });
});
