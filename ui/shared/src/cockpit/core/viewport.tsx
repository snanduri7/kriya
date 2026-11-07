import { createContext, useContext } from 'react';

/** The shell's measured width (the same number its own layout uses), so a module can switch to a compact treatment and a test can render at 900 or 1500: jsdom does no layout. */
export const ViewportWidthContext = createContext<number>(1440);
export const useViewportWidth = (): number => useContext(ViewportWidthContext);
/** Below this the Inspector is an overlay drawer instead of a side pane; below the second it is narrower. */
export const INSPECTOR_DRAWER_BELOW = 900;
export const INSPECTOR_NARROW_BELOW = 1180;
