import { describe, expect, it } from 'vitest';
import css from './cockpit.css?raw';

/**
 * RUN-CARD-RECENT-ACTIVITY-OVERLAP-001 (owner-visible P3): in a live run the card is the scroll container (`.ck-current[data-live="true"]`: flex item with min-height 0 and
 * overflow auto), so when the window is short its content must SCROLL, never compress. The recent-activity block had `flex-shrink: 1` and an explicit `min-height: 120px`
 * (which replaces the content-based minimum), so in a short window it was squeezed to about 131 px while its 5 rows spilled out and the last row was drawn over the
 * "Technical status" line (MEASURED in Chromium with this stylesheet: overlap at card heights <= ~500 px, none at >= ~540 px; with `flex: 1 0 auto` none at any height).
 * Layout cannot be computed in jsdom, so this pins the two declarations the behaviour rests on; the rendered proof is the Chromium measurement recorded with the change.
 */
describe('live run card: recent activity never compresses over the Technical status disclosure', () => {
  it('the live card is the scroll container (content scrolls, it is not squeezed)', () => {
    expect(css).toMatch(/\.ck-current\[data-live="true"\] \{[^}]*flex: 1 1 auto;[^}]*min-height: 0;[^}]*overflow: auto/);
  });
  it('the recent-activity block may grow but never shrinks below its content', () => {
    const rule = /\.ck-recent \{ flex: ([^;]+); min-height: 120px; display: flex;/.exec(css);
    expect(rule, 'the live recent-activity rule (the one with min-height: 120px) exists').not.toBeNull();
    expect(rule![1]).toBe('1 0 auto');
  });
  it('the Technical status disclosure stays a normal, reachable block pushed to the bottom (not hidden or absolutely positioned)', () => {
    expect(css).toMatch(/\.ck-current\[data-live="true"\] \.ck-tech \{ margin-top: auto; \}/);
    expect(css).not.toMatch(/\.ck-tech[^{]*\{[^}]*(position: absolute|display: none)/);
  });
});
