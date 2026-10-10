/** Workspace lifecycle on the host (GUI M5): the native chooser yields a PATH; the host remembers which paths the person chose and refuses to hand
 * Kriya any other folder. It interprets nothing about the folder (Kriya does). */
import { describe, expect, it } from 'vitest';
import { RECENT_WORKSPACES_MAX, validateKupRequest, type KupRequest } from '../src/main/ipc_contract';
import { WorkspaceRegistry, type WorkspaceStore } from '../src/main/workspace_host';

const SID = '20261004T093000000000Z-f1c70001';
const memory = (recent: string[] = [], current: string | null = null) => {
  const state = { recent, current };
  const store: WorkspaceStore = { getRecent: () => state.recent, setRecent: (l) => { state.recent = l; }, getCurrent: () => state.current, setCurrent: (p) => { state.current = p; } };
  return { state, store };
};
const req = (r: unknown): KupRequest => { const v = validateKupRequest(r); if (!v.ok) throw new Error(v.message); return v.value; };

describe('native chooser', () => {
  it('cancelling is a normal outcome and changes nothing (no approval, no recent entry, current workspace untouched)', async () => {
    const { state, store } = memory(['/old'], '/old');
    const reg = new WorkspaceRegistry(store);
    expect(await reg.choose(async () => null)).toEqual({ chosen: false, path: null, refused: null });
    expect(state).toEqual({ recent: ['/old'], current: '/old' });
    expect(reg.isApproved('/anything')).toBe(false);
  });
  it('a chosen folder is approved for this session but is not remembered or current until Kriya has validated it and the renderer remembers it', async () => {
    const { state, store } = memory();
    const reg = new WorkspaceRegistry(store);
    expect(await reg.choose(async () => '/work/new')).toEqual({ chosen: true, path: '/work/new', refused: null });
    expect(reg.isApproved('/work/new')).toBe(true);
    expect(state).toEqual({ recent: [], current: null });
    expect(reg.remember('/work/new')).toEqual({ ok: true, value: ['/work/new'] });
    expect(state).toEqual({ recent: ['/work/new'], current: '/work/new' });
  });
  it('a chooser that returns something that is not an absolute path is treated as cancelled', async () => {
    const reg = new WorkspaceRegistry(memory().store);
    for (const bad of ['relative/dir', '', '/a\nb']) expect(await reg.choose(async () => bad), JSON.stringify(bad)).toEqual({ chosen: false, path: null, refused: null });
  });
});

describe('the renderer cannot name a folder the person did not choose', () => {
  it('remember() refuses a path that was never chosen, and approves nothing by being asked', () => {
    const { state, store } = memory();
    const reg = new WorkspaceRegistry(store);
    expect(reg.remember('/etc')).toMatchObject({ ok: false });
    expect(state).toEqual({ recent: [], current: null });
    expect(reg.isApproved('/etc')).toBe(false);
  });
  it('a request that carries a workspace is refused unless the folder was chosen (this session) or remembered; requests without one are never gated', async () => {
    const reg = new WorkspaceRegistry(memory(['/remembered']).store);
    await reg.choose(async () => '/picked');
    for (const ok of ['/picked', '/remembered']) {
      expect(reg.refuse(req({ operation: 'workspace.describe', workspace: ok }))).toBeNull();
      expect(reg.refuse(req({ operation: 'workspace.status', workspace: ok }))).toBeNull();
      expect(reg.refuse(req({ operation: 'history.list', snapshot_id: SID, workspace: ok }))).toBeNull();
      expect(reg.refuse(req({ operation: 'snapshot.acquire', workspace: ok }))).toBeNull();
    }
    for (const operation of ['workspace.describe', 'workspace.status', 'snapshot.acquire']) expect(reg.refuse(req({ operation, workspace: '/Users/someone/.ssh' })), operation).toMatch(/not chosen with Open Folder/);
    expect(reg.refuse(req({ operation: 'history.list', snapshot_id: SID, workspace: '/elsewhere' }))).toMatch(/not chosen/);
    for (const free of [{ operation: 'capabilities' }, { operation: 'snapshot.list' }, { operation: 'history.list', snapshot_id: SID }, { operation: 'history.detail', snapshot_id: SID, run_id: 'r' }, { operation: 'evidence.verify', run_id: 'r' }, { operation: 'snapshot.acquire' }]) expect(reg.refuse(req(free)), JSON.stringify(free)).toBeNull();
  });
  it('a path differing only by a trailing component or case is a different, unapproved path (the host does not canonicalize; Kriya does)', async () => {
    const reg = new WorkspaceRegistry(memory(['/Work/Project']).store);
    for (const near of ['/work/project', '/Work/Project/sub', '/Work/Projec', '/Work/Project/..']) expect(reg.isApproved(near), near).toBe(false);
  });
  it('the current-workspace setting can be cleared at any time but only set through remember()', () => {
    const reg = new WorkspaceRegistry(memory().store);
    expect(reg.refuseSetting(null)).toBeNull();
    expect(reg.refuseSetting('/anywhere')).toMatch(/Open Folder/);
  });
});

describe('recent workspaces', () => {
  it('most recent first, de-duplicated, bounded', async () => {
    const { state, store } = memory();
    const reg = new WorkspaceRegistry(store);
    for (let i = 0; i < RECENT_WORKSPACES_MAX + 5; i++) { await reg.choose(async () => `/p${i}`); reg.remember(`/p${i}`); }
    expect(reg.recent()).toHaveLength(RECENT_WORKSPACES_MAX);
    expect(reg.recent()[0]).toBe(`/p${RECENT_WORKSPACES_MAX + 4}`);
    expect(reg.remember(`/p${RECENT_WORKSPACES_MAX + 2}`)).toMatchObject({ ok: true });
    expect(state.recent[0]).toBe(`/p${RECENT_WORKSPACES_MAX + 2}`);
    expect(new Set(state.recent).size).toBe(state.recent.length);
    expect(state.current).toBe(`/p${RECENT_WORKSPACES_MAX + 2}`);
  });
  it('a remembered folder is usable again (switch back) without the chooser, because the person chose it once', () => {
    const { state, store } = memory(['/a', '/b'], '/b');
    const reg = new WorkspaceRegistry(store);
    expect(reg.remember('/a')).toEqual({ ok: true, value: ['/a', '/b'] });
    expect(state.current).toBe('/a');
  });
  it('forgetting a stale entry removes it, withdraws its approval, and closes it if it was current', async () => {
    const { state, store } = memory(['/gone', '/kept'], '/gone');
    const reg = new WorkspaceRegistry(store);
    expect(reg.forget('/gone')).toEqual({ ok: true, value: ['/kept'] });
    expect(state.current).toBeNull();
    expect(reg.isApproved('/gone')).toBe(false);
    expect(reg.refuse(req({ operation: 'workspace.describe', workspace: '/gone' }))).toMatch(/not chosen/);
  });
  it('close() leaves the recent list alone and clears only the current workspace (application launch)', () => {
    const { state, store } = memory(['/a'], '/a');
    new WorkspaceRegistry(store).close();
    expect(state).toEqual({ recent: ['/a'], current: null });
  });
  it('invalid paths are rejected by remember and forget', () => {
    const reg = new WorkspaceRegistry(memory(['/a']).store);
    for (const bad of [null, 5, '', 'rel', '/a\nb', '/x'.repeat(3000)]) { expect(reg.remember(bad)).toMatchObject({ ok: false }); expect(reg.forget(bad)).toMatchObject({ ok: false }); }
  });
});

describe('M6: the workspace cannot change while a run this host started is in progress', () => {
  const active = (on: { v: boolean }) => ({ runActive: () => on.v });
  it('the chooser is not even shown: it answers RUN_ACTIVE and the dialog callback is never invoked', async () => {
    const flag = { v: true };
    const { state, store } = memory(['/a'], '/a');
    const reg = new WorkspaceRegistry(store, undefined, active(flag));
    let shown = 0;
    expect(await reg.choose(async () => { shown += 1; return '/other'; })).toEqual({ chosen: false, path: null, refused: 'RUN_ACTIVE' });
    expect(shown).toBe(0);
    expect(reg.isApproved('/other')).toBe(false);
    expect(state).toEqual({ recent: ['/a'], current: '/a' });
    flag.v = false;
    expect(await reg.choose(async () => '/other')).toMatchObject({ chosen: true, path: '/other' });
  });
  it('remember of a different folder, closing, forgetting the open one and setting a different workspace are all refused; the open workspace itself is untouched', () => {
    const flag = { v: true };
    const { state, store } = memory(['/a', '/b'], '/a');
    const reg = new WorkspaceRegistry(store, undefined, active(flag));
    expect(reg.remember('/b')).toMatchObject({ ok: false, message: expect.stringContaining('run is in progress') });
    expect(reg.remember('/a')).toMatchObject({ ok: true }); // the same workspace may be re-remembered
    expect(reg.forget('/a')).toMatchObject({ ok: false });
    expect(reg.forget('/b')).toMatchObject({ ok: true }); // forgetting a folder that is not the open one changes nothing about the run
    expect(reg.refuseSetting(null)).toMatch(/run is in progress/);
    expect(reg.refuseSetting('/b')).toMatch(/run is in progress/);
    expect(state.current).toBe('/a');
    flag.v = false;
    expect(reg.refuseSetting(null)).toBeNull();
  });
});

describe('M1 compatibility: the inspector build\'s typed workspace setting', () => {
  it('COCKPIT (default): the typed setting is refused, an unapproved path is not accepted, launch closes the workspace', () => {
    const { state, store } = memory([], '/typed');
    const reg = new WorkspaceRegistry(store);
    expect(reg.refuseSetting('/typed')).toMatch(/Open Folder/);
    expect(reg.isApproved('/typed')).toBe(false);
    expect(reg.refuse(req({ operation: 'workspace.status', workspace: '/typed' }))).toMatch(/not chosen/);
    reg.close();
    expect(state.current).toBeNull();
  });
  it('INSPECTOR: the typed setting works as accepted M1 behaved - set, approved for the explicit workspace argument, kept across launches', () => {
    const { state, store } = memory([], null);
    const reg = new WorkspaceRegistry(store, undefined, { legacyInspector: true });
    expect(reg.refuseSetting('/typed')).toBeNull();
    store.setCurrent('/typed'); // what the settings handler does after the check
    expect(reg.isApproved('/typed')).toBe(true);
    expect(reg.refuse(req({ operation: 'workspace.status', workspace: '/typed' }))).toBeNull();
    expect(reg.refuse(req({ operation: 'snapshot.acquire', workspace: '/typed' }))).toBeNull();
    reg.close();
    expect(state.current).toBe('/typed'); // launch does not erase it
  });
  it('INSPECTOR: the compatibility approves ONLY the one typed path - any other folder is still refused, and clearing the setting withdraws it', () => {
    const { store } = memory([], '/typed');
    const reg = new WorkspaceRegistry(store, undefined, { legacyInspector: true });
    expect(reg.refuse(req({ operation: 'workspace.describe', workspace: '/not/typed' }))).toMatch(/not chosen/);
    store.setCurrent(null);
    expect(reg.isApproved('/typed')).toBe(false);
  });
  it('INSPECTOR does not bypass the new authority: a run in progress still freezes the workspace', () => {
    const flag = { v: true };
    const { store } = memory([], '/typed');
    const reg = new WorkspaceRegistry(store, undefined, { legacyInspector: true, runActive: () => flag.v });
    expect(reg.refuseSetting('/elsewhere')).toMatch(/run is in progress/);
    expect(reg.refuseSetting(null)).toMatch(/run is in progress/);
  });
});
