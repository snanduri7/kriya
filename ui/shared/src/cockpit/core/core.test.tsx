import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../../state/selection';
import type { Capabilities } from '../../model/kup';
import { AvailabilityView, CapabilityRegistry, availabilityTitle, createModuleRegistry, createSelectionStore, useModuleSelection, ModuleScope, CockpitProviders, type Availability, type ModuleDefinition } from '.';
import { createFixtureProvider } from '../providers/fixture';

const caps = (features: Record<string, unknown>, operations: string[] = ['capabilities']): Capabilities => ({ kup_versions: [1], operations, identity: {}, limits: {}, features } as unknown as Capabilities);
const Nothing = () => null;
const mod = (id: string, over: Partial<ModuleDefinition> = {}): ModuleDefinition => ({ id, placement: { page: 'p', surface: 'center-tab', label: id, order: 1 }, requiredCapabilities: [], optionalCapabilities: [], selectionDependencies: [], render: Nothing, ...over });
const pages = [{ id: 'p', label: 'P', order: 1, layout: 'run' as const }];

describe('CapabilityRegistry', () => {
  it('is fail-closed: unloaded, unknown and non-true flags are absent', () => {
    expect(new CapabilityRegistry(null).has('evidence')).toBe(false);
    const r = new CapabilityRegistry(caps({ evidence: true, live: false, odd: 'yes' }, ['evidence.records']));
    expect([r.has('evidence'), r.has('live'), r.has('odd'), r.has('nope')]).toEqual([true, false, false, false]);
    expect([r.has('op:evidence.records'), r.has('op:evidence.verify')]).toEqual([true, false]);
    expect(r.missing(['evidence', 'live', 'op:evidence.verify'])).toEqual(['live', 'op:evidence.verify']);
  });
  it('reports the capability set and advertised limits', () => {
    expect(new CapabilityRegistry(caps({ capability_set: '1.1' })).capabilitySet).toBe('1.1');
    expect(CapabilityRegistry.limit({ ...caps({}), limits: { evidence_page_max: 1000 } } as Capabilities, 'evidence_page_max')).toBe(1000);
    expect(CapabilityRegistry.limit(null, 'x')).toBeNull();
  });
});

describe('ModuleRegistry', () => {
  it('refuses duplicate ids, unknown pages, unknown surfaces and malformed capabilities', () => {
    expect(() => createModuleRegistry([mod('a'), mod('a')], { pages })).toThrow(/duplicate module id a/);
    expect(() => createModuleRegistry([mod('a', { placement: { page: 'zzz', surface: 'page', label: 'a', order: 1 } })], { pages })).toThrow(/unknown page/);
    expect(() => createModuleRegistry([mod('a', { placement: { page: 'p', surface: 'sidebar' as never, label: 'a', order: 1 } })], { pages })).toThrow(/unknown surface/);
    expect(() => createModuleRegistry([mod('a', { requiredCapabilities: ['has space'] })], { pages })).toThrow(/malformed capability/);
    expect(() => createModuleRegistry([], { pages: [...pages, ...pages] })).toThrow(/duplicate page/);
  });
  it('orders by placement, hides on a missing required capability, explains only when asked, honours disabled ids', () => {
    const registry = createModuleRegistry([mod('b', { placement: { page: 'p', surface: 'center-tab', label: 'b', order: 2 } }), mod('a'), mod('hidden', { requiredCapabilities: ['live'] }), mod('explains', { requiredCapabilities: ['live'], whenUnavailable: 'explain' })], { pages });
    const none = new CapabilityRegistry(caps({}));
    expect(registry.resolve('p', 'center-tab', none).map((r) => `${r.def.id}:${r.state}`)).toEqual(['a:visible', 'explains:unavailable', 'b:visible']);
    const live = new CapabilityRegistry(caps({ live: true }));
    expect(registry.resolve('p', 'center-tab', live).map((r) => r.def.id)).toEqual(['a', 'explains', 'hidden', 'b']);
    const off = createModuleRegistry([mod('a'), mod('b', { placement: { page: 'p', surface: 'center-tab', label: 'b', order: 2 } })], { pages, disabled: ['b'] });
    expect(off.resolve('p', 'center-tab', none).map((r) => r.def.id)).toEqual(['a']);
    expect(off.byId('b')).toBeUndefined();
  });
  it('a page with no visible module has no rail slot', () => {
    const registry = createModuleRegistry([mod('a', { requiredCapabilities: ['knowledge'] })], { pages });
    expect(registry.visiblePages(new CapabilityRegistry(caps({})))).toEqual([]);
    expect(registry.visiblePages(new CapabilityRegistry(caps({ knowledge: true })))).toHaveLength(1);
  });
});

describe('SelectionStore', () => {
  it('shares one state, notifies subscribers once per real change, and unsubscribes', () => {
    const store = createSelectionStore();
    let calls = 0;
    const off = store.subscribe(() => { calls += 1; });
    store.dispatch({ type: 'selectRun', runId: 'r1' });
    store.dispatch({ type: 'selectRun', runId: 'r1' });
    expect([calls, store.getState().runId]).toEqual([1, 'r1']);
    off();
    store.dispatch({ type: 'selectRun', runId: 'r2' });
    expect(calls).toBe(1);
  });
  it('a timeline node drives unit, attempt, event and gate together; a new run or unit resets everything below', () => {
    const store = createSelectionStore({ ...initialSelection, runId: 'r1' });
    store.dispatch({ type: 'selectTimelineNode', node: { unitId: 'u1', attempt: '2', eventSeq: 9, gateId: '2:compile' } });
    expect(store.getState()).toMatchObject({ unitId: 'u1', attempt: '2', eventSeq: 9, gateId: '2:compile' });
    store.dispatch({ type: 'selectFile', filePath: 'a.java' });
    store.dispatch({ type: 'selectUnit', unitId: 'u2' });
    expect(store.getState()).toMatchObject({ unitId: 'u2', attempt: null, eventSeq: null, gateId: null, filePath: null });
    store.dispatch({ type: 'selectTimelineNode', node: { unitId: 'u2', attempt: '1', eventSeq: 3, gateId: null } });
    store.dispatch({ type: 'selectRun', runId: 'r2' });
    expect(store.getState()).toMatchObject({ runId: 'r2', unitId: null, attempt: null, eventSeq: null, gateId: null, filePath: null, phaseFilter: null });
  });
  it('selecting a file never opens the drawer (the legacy selectComparison does, and the cockpit does not use it)', () => {
    const store = createSelectionStore();
    store.dispatch({ type: 'selectFile', filePath: 'a.java' });
    expect(store.getState()).toMatchObject({ filePath: 'a.java', drawerOpen: false, drawerTab: 'why', comparisonPath: null });
    store.dispatch({ type: 'selectComparison', path: 'x' }); // M1 behaviour kept for the legacy panels until parity retirement
    expect(store.getState()).toMatchObject({ drawerOpen: true, drawerTab: 'diff' });
  });
  it('the phase filter filters visibility only: it never changes what is selected', () => {
    const store = createSelectionStore({ ...initialSelection, runId: 'r', attempt: '1', unitId: 'u' });
    store.dispatch({ type: 'phaseFilter', phase: 'attempt' });
    expect(store.getState()).toMatchObject({ runId: 'r', attempt: '1', unitId: 'u', phaseFilter: 'attempt' });
  });
});

describe('module selection scope', () => {
  const Reader = ({ field }: { field: 'runId' | 'filePath' }) => { const v = useModuleSelection(field); return <span>{String(v)}</span>; };
  const wrap = (def: ModuleDefinition, field: 'runId' | 'filePath') => {
    const store = createSelectionStore({ ...initialSelection, runId: 'r1' });
    const registry = createModuleRegistry([def], { pages });
    return render(<CockpitProviders services={{ provider: createFixtureProvider(), selection: store, registry }}><ModuleScope def={def}><Reader field={field} /></ModuleScope></CockpitProviders>);
  };
  it('a module reads the fields it declared and nothing else', () => {
    wrap(mod('m', { selectionDependencies: ['runId'] }), 'runId');
    expect(screen.getByText('r1')).toBeInTheDocument();
  });
  it('an undeclared read is a programming error', () => {
    const spy = console.error; console.error = () => undefined;
    try { expect(() => wrap(mod('m', { selectionDependencies: ['runId'] }), 'filePath')).toThrow(/does not declare it/); } finally { console.error = spy; }
  });
});

describe('Availability', () => {
  const states: Availability<string>[] = [
    { state: 'loading' }, { state: 'not_recorded', detail: 'x' }, { state: 'error', code: 'E', message: 'm' },
    ...(['capability_absent', 'store_absent', 'pruned', 'capture_off', 'pre_recorder', 'recorder_unavailable', 'digest_only', 'denied', 'unverified', 'unknown_run', 'pending'] as const).map((reason) => ({ state: 'unavailable' as const, reason, detail: 'd' })),
  ];
  it('every non-ready state has its own title and none says "no data" or "empty"', () => {
    const titles = states.map((s) => availabilityTitle(s));
    expect(new Set(titles).size).toBe(titles.length);
    for (const t of titles) expect(/no data|empty/i.test(t)).toBe(false);
  });
  it('the view shows the title and the reason verbatim, and hands data to the renderer only when there is some', () => {
    const { rerender } = render(<AvailabilityView value={{ state: 'unavailable', reason: 'pruned', detail: 'retention removed it' }}>{() => <i>DATA</i>}</AvailabilityView>);
    expect(screen.getByText('Pruned by retention')).toBeInTheDocument();
    expect(screen.getByText('retention removed it')).toBeInTheDocument();
    expect(screen.queryByText('DATA')).toBeNull();
    const prov = { operation: 'x', source: null, consistency: null, schemaVersion: 1, requestId: 'r', observedAt: '' };
    rerender(<AvailabilityView value={{ state: 'degraded', data: 1, provenance: prov, reasons: ['store unsealed'] }}>{(d) => <i>DATA {d}</i>}</AvailabilityView>);
    expect(screen.getByText('store unsealed')).toBeInTheDocument();
    expect(screen.getByText('DATA 1')).toBeInTheDocument();
  });
});
