/** Selection reducer (P-23): run -> attempt -> event. Lives ONLY here so every host shares one rule set. */
export type InspectorTab = 'context' | 'prompt' | 'output' | 'gates' | 'evidence';
export type DrawerTab = 'diff' | 'why';
export const INSPECTOR_TABS: readonly InspectorTab[] = ['context', 'prompt', 'output', 'gates', 'evidence'];
export const DRAWER_TABS: readonly DrawerTab[] = ['diff', 'why'];

export interface Selection {
  runId: string | null;
  attempt: string | null; // group key from eventsByAttempt
  eventIndex: number | null; // index into the recorded run_events array
  comparisonPath: string | null;
  inspectorTab: InspectorTab;
  drawerTab: DrawerTab;
  drawerOpen: boolean;
}

export const initialSelection: Selection = {
  runId: null,
  attempt: null,
  eventIndex: null,
  comparisonPath: null,
  inspectorTab: 'context',
  drawerTab: 'why',
  drawerOpen: false,
};

export type SelectionAction =
  | { type: 'selectRun'; runId: string | null }
  | { type: 'selectAttempt'; attempt: string | null }
  | { type: 'selectEvent'; eventIndex: number | null }
  | { type: 'selectComparison'; path: string | null }
  | { type: 'inspectorTab'; tab: InspectorTab }
  | { type: 'drawerTab'; tab: DrawerTab }
  | { type: 'toggleDrawer'; open?: boolean }
  | { type: 'reset' };

export function selectionReducer(state: Selection, action: SelectionAction): Selection {
  switch (action.type) {
    case 'selectRun':
      // A new run invalidates every finer selection; the tab choice is kept (user intent).
      if (action.runId === state.runId) return state;
      return { ...state, runId: action.runId, attempt: null, eventIndex: null, comparisonPath: null };
    case 'selectAttempt':
      return { ...state, attempt: action.attempt, eventIndex: null };
    case 'selectEvent':
      return { ...state, eventIndex: action.eventIndex };
    case 'selectComparison':
      return { ...state, comparisonPath: action.path, drawerOpen: action.path !== null ? true : state.drawerOpen, drawerTab: action.path !== null ? 'diff' : state.drawerTab };
    case 'inspectorTab':
      return { ...state, inspectorTab: action.tab };
    case 'drawerTab':
      return { ...state, drawerTab: action.tab, drawerOpen: true };
    case 'toggleDrawer':
      return { ...state, drawerOpen: action.open ?? !state.drawerOpen };
    case 'reset':
      return initialSelection;
    default:
      return state;
  }
}
