/**
 * The Security card. The renderer states nothing of its own: protections are Kriya's `health` rows; every refusal is the canonical owner's decision (outcome, reason code, rule)
 * about a situation built in a disposable folder. The backend rows are pinned in tests/test_security_demonstration.py and tests/test_project_health_rows.py.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExecutionPreflight, SecurityDemonstration } from '../../../model/kup';
import { renderWorkspaceShell } from '../../testing/harness';
import { SecurityCard } from './SecurityCard';

type Row = NonNullable<ExecutionPreflight['health']>[number];
const health = (id: string, state: Row['state'], detail = `${id} detail`): Row => ({ id, label: id === 'security' ? 'Security' : 'Private state', state, reason_code: `${id.toUpperCase()}_CODE`, detail, evidence: ['e1'] });
const demo = (id: string, outcome: SecurityDemonstration['outcome'], over: Partial<SecurityDemonstration> = {}): SecurityDemonstration =>
  ({ id, protection: 'Write-scope enforcement', requested: `request ${id}`, outcome, reason_code: 'PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS', authority: 'filesystem.authorized_writer.outside_scope', explanation: `why ${id}`, ...over });
const li = (id: string) => document.querySelector(`[data-demo="${id}"]`) as HTMLElement;

describe('the Security card', () => {
  it('shows each refusal with its outcome, requested operation, reason code, rule and explanation', () => {
    render(<SecurityCard rows={[]} demonstrations={[demo('write_outside_scope', 'BLOCKED'), demo('write_inside_scope', 'ALLOWED', { reason_code: 'PATH_WITHIN_WORKSPACE_ALLOWED', authority: 'filesystem.within_workspace_allowed' })]} />);
    const blocked = li('write_outside_scope');
    expect(within(blocked).getByText('BLOCKED')).toBeTruthy();
    expect(blocked.textContent).toContain('request write_outside_scope');
    expect(blocked.textContent).toContain('PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS');
    expect(blocked.textContent).toContain('rule filesystem.authorized_writer.outside_scope');
    expect(blocked.textContent).toContain('why write_outside_scope');
    expect(li('write_inside_scope').getAttribute('data-outcome')).toBe('ALLOWED');
    expect(screen.getByText('What Kriya refuses · 1 of 2 checks blocked')).toBeTruthy();
  });

  it('says it is a demonstration in a disposable folder and shows no path, content or secret', () => {
    render(<SecurityCard rows={[]} demonstrations={[demo('redact_secret', 'REDACTED', { reason_code: 'SECRET_REDACTED', authority: 'kriya.foundation.redaction.redact_text', explanation: 'masked (40 characters)' })]} />);
    expect(screen.getByText(/disposable folder\. Nothing in your project was read or changed, and no secret is shown/)).toBeTruthy();
    expect(li('redact_secret').textContent).not.toMatch(/ghp_|\/Users\/|\/private\//);
  });

  it('shows the protections in force from the health rows, with the reason code and evidence in the title, and ignores rows that are not protections', () => {
    render(<SecurityCard rows={[health('security', 'AVAILABLE'), health('private_state', 'DEGRADED'), { ...health('lsp', 'UNAVAILABLE'), label: 'Language server' }]} demonstrations={[]} />);
    expect(within(document.querySelector('[data-protection="security"]') as HTMLElement).getByText('In force')).toBeTruthy();
    expect((document.querySelector('[data-protection="security"]') as HTMLElement).title).toBe('SECURITY_CODE; evidence: e1');
    expect(within(document.querySelector('[data-protection="private_state"]') as HTMLElement).getByText('Degraded')).toBeTruthy();
    expect(document.querySelector('[data-protection="lsp"]')).toBeNull();
    expect(screen.queryByText(/What Kriya refuses/)).toBeNull();
  });
});

describe('the Project page', () => {
  it('shows the security card from the preflight', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/empty', execution: { outcome: 'success', health: [health('security', 'AVAILABLE')], security: { demonstrations: [demo('write_outside_scope', 'BLOCKED')] } } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    const card = await screen.findByTestId('security-card');
    expect(within(card).getByText('BLOCKED')).toBeTruthy();
  });

  it('shows no security card when Kriya reports none (an older Kriya)', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/empty', execution: { outcome: 'success' } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    await screen.findByText('What do you want Kriya to do?');
    expect(screen.queryByTestId('security-card')).toBeNull();
  });
});
