/** The replay label: names the run, states read-only, and claims nothing live. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReplayNotice } from './ReplayNotice';

describe('the replay notice', () => {
  it('names the run being replayed and says nothing is running or re-executed', () => {
    render(<ReplayNotice runId="1e51ee67" />);
    const text = screen.getByTestId('replay-notice').textContent ?? '';
    expect(text).toContain('Replay of Run 1e51ee67');
    expect(text).toContain('read-only');
    expect(text).toContain('Nothing is running and nothing is re-executed');
  });

  it('never presents a replay as live progress', () => {
    render(<ReplayNotice runId="r" />);
    expect(screen.getByTestId('replay-notice').textContent ?? '').not.toMatch(/in progress|running now|live/i);
  });
});
