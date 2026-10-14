/** Accessibility and responsive behaviour of the cockpit shell (GUI M7.11/M7.12): widths, internal scrolling, labelled icon controls, keyboard, no colour-only status. */
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import css from '../design/cockpit.css?raw';
import diffSource from '../modules/changes/DiffView.tsx?raw';
import { renderShell, renderWorkspaceShell } from '../testing/harness';
const body = () => document.querySelector('.ck-body') as HTMLElement;

describe('widths: 1536 / 1360 / 1180 / 1100', () => {
  it.each([[1536, false], [1360, false], [1180, true], [1100, true]])('%ipx: inspector collapsed by default = %s, main workspace and rail always present', async (width, collapsed) => {
    cleanup();
    renderShell({ width, runId: 'fx-greenfield-compile-retry' }); // the side panel exists once a run is selected
    await screen.findByRole('navigation', { name: 'Destinations' });
    await waitFor(() => expect(body().className.includes('no-inspector')).toBe(collapsed));
    expect(body().querySelector('.ck-center, main')).not.toBeNull();
    expect(screen.getByRole('navigation', { name: 'Destinations' })).toBeInTheDocument();
  });

  it('the main workspace keeps width priority: it is the one flexible track, side panes are bounded', () => {
    expect(css).toMatch(/\.ck-body \{[^}]*grid-template-columns: var\(--ck-rail, 92px\) var\(--ck-secondary, 280px\) 6px minmax\(0, 1fr\) 6px var\(--ck-inspector, 340px\)/);
    expect(css).toMatch(/\.ck-secondary, \.ck-center, \.ck-inspector \{[^}]*min-width: 0[^}]*overflow: auto/);
  });

  it('the bottom drawer, diffs and long content scroll INSIDE their own boxes and never grow the page grid', () => {
    expect(css).toMatch(/\.ck-drawer-body \{[^}]*height: 240px[^}]*overflow: auto/);
    expect(css).toMatch(/\.ck-shell \{[^}]*grid-template-rows: auto minmax\(0, 1fr\) auto[^}]*height: 100%/);
    expect(diffSource).toMatch(/overflow: ?'auto'|overflow:\s*auto|overflowX|maxHeight/);
  });
});

describe('accessibility', () => {
  it('every navigation button has an accessible name, a visible text label and a tooltip', async () => {
    renderShell({ width: 1440 });
    const rail = await screen.findByRole('navigation', { name: 'Destinations' });
    const buttons = within(rail).getAllByRole('button');
    expect(buttons.length).toBeGreaterThan(1);
    for (const b of buttons) {
      expect(b.getAttribute('aria-label'), b.outerHTML).toMatch(/\S/);
      expect(b.getAttribute('title'), b.outerHTML).toMatch(/\S/);
      expect(b.textContent?.replace(b.querySelector('.ck-rail-icon')?.textContent ?? '', '').trim(), b.outerHTML).toBe(b.getAttribute('aria-label')); // the label is on screen, not only in a tooltip
      expect(b.textContent, b.outerHTML).not.toMatch(/[▶■]/); // no play or stop glyph is ever navigation
    }
  });

  it('no positive tabindex anywhere: keyboard order is document order, and every control is a native or roled element', async () => {
    renderShell({ width: 1440 });
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(document.querySelectorAll('[tabindex]:not([tabindex="0"]):not([tabindex="-1"])')).toHaveLength(0);
    for (const el of document.querySelectorAll('[onclick]')) throw new Error(`inline click handler on a non-semantic element: ${(el as HTMLElement).outerHTML}`);
  });

  it('the workspace switcher menu closes with Escape and does not trap focus; Open Folder is also reachable from the application menu command', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', choices: [] } });
    const button = await screen.findByRole('button', { name: 'Switch workspace' });
    fireEvent.click(button);
    expect(await screen.findByRole('menu', { name: 'Workspace menu' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'Workspace menu' })).toBeNull());
    host.menu('openFolder'); // the File menu / Cmd+O command: the same chooser the button uses
    await waitFor(() => expect(host.workspaceCalls).toContain('choose'));
  });

  it('status is never colour alone: badges and outcomes carry text', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.click(await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 }));
    await screen.findByLabelText('Trust strip');
    for (const badge of document.querySelectorAll('.ck-badge')) expect((badge.textContent ?? '').trim().length, (badge as HTMLElement).outerHTML).toBeGreaterThan(0);
  });

  it('every control has a visible focus style: the shared focus-visible ring is defined once for the whole shell', () => {
    expect(css).toMatch(/\.ck \*:focus-visible \{ outline: 2px solid var\(--ck-focus\)/);
    expect(css).not.toMatch(/outline: ?none/);
  });
});
