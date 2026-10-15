/** Test helpers for the Project page's compact "Recent runs" (Demo V2: at most 3). The fixture lists newest first, so the three shown are these; the older Greenfield run is one click away under "View all runs". */
import { fireEvent, screen } from '@testing-library/react';

export const RECENT_RUN = /A run that never closed its store/;   // the newest card: exactly ONE element matches (find/query by text fail on several)
export const GREENFIELD_RUN = /Greenfield: |Create a utility library/;

/** Open the full Runs list from the Project page and click the Greenfield run card there. */
export async function openGreenfieldFromAllRuns(): Promise<void> {
  fireEvent.click(await screen.findByRole('button', { name: 'View all runs' }));
  fireEvent.click(await screen.findByText(GREENFIELD_RUN, undefined, { timeout: 3000 }));
}
