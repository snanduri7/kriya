import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CockpitProviders, NavigationContext, PROJECT_PAGE, createSelectionStore, useCapabilities, useShellSelection, useWorkspace, type ModuleRegistry, type PollIntervals, type SelectionStore, type UIProvider } from '../core';
import type { Selection } from '../../state/selection';
import { EvidenceDrawer } from './EvidenceDrawer';
import { ExecutionInspector } from './ExecutionInspector';
import { GlobalToolbar, type ToolbarToggles } from './GlobalToolbar';
import { MainWorkspace } from './MainWorkspace';
import { NewProjectDialog } from './NewProjectDialog';
import { ModuleHost } from './ModuleHost';
import { NavigationRail } from './NavigationRail';
import { SecondaryPane } from './SecondaryPane';
import { WelcomeScreen } from './WelcomeScreen';
import { ResizeHandle } from './ResizeHandle';

export const INSPECTOR_COLLAPSE_AT = 1180;
const SECONDARY = { min: 240, max: 360, initial: 280 };
const INSPECTOR = { min: 300, max: 460, initial: 340 };

export interface AppShellProps {
  provider: UIProvider;
  registry: ModuleRegistry;
  /** Tests and hosts may seed or share the selection; otherwise the shell owns a fresh store. */
  selection?: SelectionStore;
  initialSelection?: Selection;
  /** Whether the technical timeline (Timeline, WHY) is open when the page first shows. Product default: closed - the normal layer is the result, not the record stream. */
  initialInspectorOpen?: boolean;
  /** Overrides the measured viewport width (tests, screenshots). */
  viewportWidth?: number;
  /** Tests only: how often a started run is polled. */
  pollIntervals?: PollIntervals;
  /** Tests and screenshots only: the page shown until the person chooses one (production always lands on the first page, Project). */
  initialPage?: string;
}

/**
 * Composition and layout only. It knows pages, surfaces and capabilities, never a feature: which modules exist, what they show and
 * what they interpret is the registry's business. Adding or removing a module changes the registry input, not this file.
 */
export function AppShell({ provider, registry, selection, initialSelection, viewportWidth, pollIntervals, initialPage, initialInspectorOpen }: AppShellProps) {
  const store = useMemo(() => selection ?? createSelectionStore(initialSelection), [selection, initialSelection]);
  const services = useMemo(() => ({ provider, selection: store, registry, pollIntervals }), [provider, store, registry, pollIntervals]);
  return <CockpitProviders services={services}><Shell registry={registry} viewportWidth={viewportWidth} initialPage={initialPage} initialInspectorOpen={initialInspectorOpen} /></CockpitProviders>;
}

function useWidth(override?: number): number {
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth));
  useEffect(() => {
    if (override !== undefined) return undefined;
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [override]);
  return override ?? width;
}

function Shell({ registry, viewportWidth, initialPage, initialInspectorOpen }: { registry: ModuleRegistry; viewportWidth?: number; initialPage?: string; initialInspectorOpen?: boolean }) {
  const folder = useWorkspace();
  const folderOpen = folder.current !== null;
  const caps = useCapabilities();
  const width = useWidth(viewportWidth);
  const pages = useMemo(() => registry.visiblePages(caps), [registry, caps]);
  // The chosen page belongs to the folder it was chosen in: Open Folder (or switching folders) lands on the first page, Project, whatever page the previous folder was on.
  const openPath = folder.current?.path ?? null;
  const [chosen, setChosen] = useState<{ folder: string | null; id: string | null }>({ folder: null, id: null });
  const pageId = chosen.folder === openPath ? chosen.id : initialPage ?? null;
  const setPageId = useCallback((id: string) => setChosen({ folder: openPath, id }), [openPath]);
  const page = pages.find((p) => p.id === pageId) ?? pages[0];
  const [secondaryW, setSecondaryW] = useState(SECONDARY.initial);
  const [inspectorW, setInspectorW] = useState(INSPECTOR.initial);
  // null = follow the designed default for this width; true/false = the user's explicit choice.
  const [secondaryOpen, setSecondaryOpen] = useState<boolean | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState<boolean | null>(initialInspectorOpen ?? null);
  const root = useRef<HTMLDivElement>(null);

  const showSecondary = secondaryOpen ?? true;
  const showInspector = inspectorOpen ?? false; // the technical timeline is opt-in at every width: the normal layer is the result, not the record stream

  const resolve = useCallback((surface: Parameters<ModuleRegistry['resolve']>[1]) => (page ? registry.resolve(page.id, surface, caps) : []), [registry, caps, page]);
  const secondary = resolve('secondary');
  const inspector = resolve('inspector');
  const header = resolve('header');
  const tabs = resolve('center-tab');
  const drawer = resolve('drawer-tab');
  const single = resolve('page');
  const workspace = page?.layout === 'run';
  const hasSecondary = workspace && secondary.length > 0;
  // With no run selected there is nothing for the side panel or the bottom drawer to show: they appear with the first selected run instead of as empty "No run selected" panes.
  const runSelected = useShellSelection('runId') !== null;
  const hasInspector = workspace && inspector.length > 0 && runSelected;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!(e.ctrlKey || e.metaKey) || !/^[1-9]$/.test(e.key)) return;
    const target = pages[Number(e.key) - 1];
    if (target) { e.preventDefault(); setPageId(target.id); }
  };

  const toggles: ToolbarToggles = { secondary: hasSecondary && showSecondary, inspector: hasInspector && showInspector };
  const classes = ['ck-body'];
  if (!workspace) classes.push('single');
  if (workspace && !(hasSecondary && showSecondary)) classes.push('no-secondary');
  if (workspace && !(hasInspector && showInspector)) classes.push('no-inspector');

  const navigate = useCallback((id: string) => { if (pages.some((p) => p.id === id)) setPageId(id); }, [pages, setPageId]);
  return (
    <NavigationContext.Provider value={navigate}>
    <div className="ck ck-shell" ref={root} onKeyDown={onKeyDown} data-width={width} data-page={folderOpen ? page?.id ?? '' : 'welcome'}>
      <GlobalToolbar bare={!folderOpen} runStrip={page?.id !== PROJECT_PAGE} showSecondaryToggle={hasSecondary} showInspectorToggle={hasInspector} open={toggles} onToggle={(which) => {
        if (which === 'secondary') setSecondaryOpen(!showSecondary);
        if (which === 'inspector') setInspectorOpen(!showInspector);
      }} />
      {/* No folder open: the welcome surface and nothing else - never runs, never fixture content. The toolbar above is the same element either way. */}
      {!folderOpen ? (folder.ready ? <WelcomeScreen /> : <div className="ck-state muted" role="status">Loading…</div>) : (
      <div className={classes.join(' ')} style={{ '--ck-secondary': `${secondaryW}px`, '--ck-inspector': `${inspectorW}px` } as React.CSSProperties}>
        <NavigationRail pages={pages} active={page?.id ?? ''} onSelect={setPageId} />
        {workspace ? (
          <>
            {hasSecondary && showSecondary ? <SecondaryPane modules={secondary} /> : <div />}
            {hasSecondary && showSecondary ? <ResizeHandle label="Resize runs pane" value={secondaryW} min={SECONDARY.min} max={SECONDARY.max} sign={1} onChange={setSecondaryW} /> : <div />}
            <MainWorkspace header={header} tabs={tabs} />
            {hasInspector && showInspector ? <ResizeHandle label="Resize inspector" value={inspectorW} min={INSPECTOR.min} max={INSPECTOR.max} sign={-1} onChange={setInspectorW} /> : <div />}
            {hasInspector && showInspector ? <ExecutionInspector modules={inspector} /> : <div />}
          </>
        ) : (
          <>
            <div /><div />
            <main className="ck-center" aria-label={page?.label ?? 'Page'}>{single.map((m) => <ModuleHost key={m.def.id} resolved={m} />)}{!pages.length ? <div className="ck-state" role="status">Loading capabilities…</div> : null}</main>
            <div /><div />
          </>
        )}
      </div>
      )}
      {folderOpen && workspace && runSelected && showInspector ? <EvidenceDrawer modules={drawer} /> : null} {/* Build / Tests / Git / Raw Events are the technical layer: they open with the technical timeline */}
      <NewProjectDialog />
    </div>
    </NavigationContext.Provider>
  );
}

