import { createContext, useContext, useMemo, useState } from 'react';

export const INSPECTOR_TABS = [
  { id: 'runs', label: 'Runs' }, { id: 'memory', label: 'Memory' }, { id: 'security', label: 'Security' }, { id: 'skills', label: 'Skills' }, { id: 'project', label: 'Project' },
] as const;
export type InspectorTab = (typeof INSPECTOR_TABS)[number]['id'];

/**
 * The Project Workspace's own session state: which Inspector tab is selected, whether the Inspector and the Activity dock are open. Kept at the shell (above the pages) so it survives moving
 * between pages during the session; it is never written to disk. `null` = "follow the designed default for this moment" (the Inspector follows the width; the dock is open while a run is
 * active); true/false = the person's explicit choice.
 */
export interface WorkspaceUi {
  tab: InspectorTab; setTab(tab: InspectorTab): void;
  inspectorOpen: boolean | null; setInspectorOpen(open: boolean | null): void;
  dockOpen: boolean | null; setDockOpen(open: boolean | null): void;
}
export const WorkspaceUiContext = createContext<WorkspaceUi | null>(null);

export function useWorkspaceUiState(): WorkspaceUi {
  const [tab, setTab] = useState<InspectorTab>('runs');
  const [inspectorOpen, setInspectorOpen] = useState<boolean | null>(null);
  const [dockOpen, setDockOpen] = useState<boolean | null>(null);
  return useMemo(() => ({ tab, setTab, inspectorOpen, setInspectorOpen, dockOpen, setDockOpen }), [tab, inspectorOpen, dockOpen]);
}

/** The shell's state when there is one; a private one for a module rendered on its own (tests, tools). */
export function useWorkspaceUi(): WorkspaceUi {
  const shared = useContext(WorkspaceUiContext);
  const local = useWorkspaceUiState();
  return shared ?? local;
}
