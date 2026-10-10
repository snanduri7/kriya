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
  // Run Cockpit v1.1 (additive, Q-4): the shared cockpit selection. M1 fields above keep their behaviour until parity retirement.
  unitId: string | null; // S2 unit id
  eventSeq: number | null; // S2 record seq (the M1 eventIndex indexes S1 run_events and is a different thing)
  gateId: string | null; // a gate selection key, e.g. "2:compile" (attempt:gate)
  filePath: string | null; // selected file in Changes (recorded path)
  centralTab: string; // a module id; modules, not the reducer, define which exist
  phaseFilter: string | null; // filters visibility only, never selection
  bottomTab: string | null; // the evidence drawer's active module id (drawerOpen is shared with M1)
  compareAttempt: string | null; // the second attempt (B) of an A/B comparison; `attempt` is A
}

export const initialSelection: Selection = {
  runId: null,
  attempt: null,
  eventIndex: null,
  comparisonPath: null,
  inspectorTab: 'context',
  drawerTab: 'why',
  drawerOpen: false,
  unitId: null,
  eventSeq: null,
  gateId: null,
  filePath: null,
  centralTab: 'overview',
  phaseFilter: null,
  bottomTab: null,
  compareAttempt: null,
};

export type SelectionAction =
  | { type: 'selectRun'; runId: string | null }
  | { type: 'selectAttempt'; attempt: string | null }
  | { type: 'selectEvent'; eventIndex: number | null }
  | { type: 'selectComparison'; path: string | null }
  | { type: 'inspectorTab'; tab: InspectorTab }
  | { type: 'drawerTab'; tab: DrawerTab }
  | { type: 'toggleDrawer'; open?: boolean }
  | { type: 'selectUnit'; unitId: string | null }
  | { type: 'selectEventSeq'; eventSeq: number | null }
  | { type: 'selectGate'; gateId: string | null }
  | { type: 'selectFile'; filePath: string | null }
  | { type: 'centralTab'; tab: string }
  | { type: 'phaseFilter'; phase: string | null }
  | { type: 'bottomTab'; tab: string }
  | { type: 'compareAttempt'; attempt: string | null }
  /** The timeline is the one navigator: a node click sets the unit, attempt, event and gate together, so every consumer sees one context. */
  | { type: 'selectTimelineNode'; node: { unitId: string | null; attempt: string | null; eventSeq: number | null; gateId: string | null } }
  | { type: 'reset' };

export function selectionReducer(state: Selection, action: SelectionAction): Selection {
  switch (action.type) {
    case 'selectRun':
      // A new run invalidates every finer selection; the tab choice is kept (user intent).
      if (action.runId === state.runId) return state;
      return { ...state, runId: action.runId, attempt: null, eventIndex: null, comparisonPath: null, unitId: null, eventSeq: null, gateId: null, filePath: null, phaseFilter: null, compareAttempt: null };
    case 'selectAttempt':
      // Narrowing to one attempt may hide the selected event, so the event selection is dropped; widening back to
      // "all events" hides nothing, so the selected event (an index into the recorded list) stays selected.
      return { ...state, attempt: action.attempt, eventIndex: action.attempt === null ? state.eventIndex : null };
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
    case 'selectUnit':
      // A new unit invalidates the attempt and everything finer.
      return action.unitId === state.unitId ? state : { ...state, unitId: action.unitId, attempt: null, eventIndex: null, eventSeq: null, gateId: null, filePath: null, compareAttempt: null };
    case 'selectEventSeq':
      return { ...state, eventSeq: action.eventSeq };
    case 'selectGate':
      return { ...state, gateId: action.gateId };
    case 'selectFile':
      // Sets the path only: unlike the M1 selectComparison it never opens the drawer.
      return { ...state, filePath: action.filePath };
    case 'centralTab':
      return { ...state, centralTab: action.tab };
    case 'phaseFilter':
      return { ...state, phaseFilter: action.phase };
    case 'compareAttempt':
      return { ...state, compareAttempt: action.attempt };
    case 'bottomTab':
      return { ...state, bottomTab: action.tab, drawerOpen: true };
    case 'selectTimelineNode':
      return { ...state, unitId: action.node.unitId, attempt: action.node.attempt, eventSeq: action.node.eventSeq, gateId: action.node.gateId, eventIndex: null, filePath: null };
    case 'reset':
      return initialSelection;
    default:
      return state;
  }
}
