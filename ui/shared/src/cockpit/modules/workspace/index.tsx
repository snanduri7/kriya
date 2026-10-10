import { AvailabilityView, Badge, KeyValue, Panel, loading, toAvailability, useAsync, useProvider, type ModuleDefinition, type Provenance } from '../../core';
import type { WorkspaceStatus } from '../../../model/kup';

const HOST_PROVENANCE: Provenance = { operation: 'host.setting', source: null, consistency: null, schemaVersion: 1, requestId: 'host', observedAt: '' };

/** RUN_ACTIVE is a property of the workspace (its lock), never of a run row. */
function Workspace() {
  const provider = useProvider();
  const path = useAsync<string | null>('workspace-setting', async () => { const p = await provider.getSetting('workspacePath'); return { state: 'ready', data: p ?? null, provenance: HOST_PROVENANCE }; }, loading());
  const workspace = path.state === 'ready' ? path.data : undefined;
  const state = useAsync<WorkspaceStatus>(workspace ? `workspace:${workspace}` : null, async (signal) => toAvailability(await provider.workspaceStatus(workspace!, signal)),
    workspace === undefined ? loading() : { state: 'unavailable', reason: 'unknown_run', detail: 'No workspace is selected: set one in Settings.' });
  return (
    <Panel title="Workspace">
      <AvailabilityView value={state}>
        {(s) => <KeyValue items={[['Path', s.workspace], ['Recovery status', <Badge key="s" tone={s.status === 'CLEAN' ? 'ok' : 'warn'}>{s.status}</Badge>], ['A run holds the workspace', s.run_active ? 'yes (RUN_ACTIVE)' : 'no']]} />}
      </AvailabilityView>
    </Panel>
  );
}

export const workspaceModule: ModuleDefinition = {
  id: 'workspace',
  placement: { page: 'workspace', surface: 'page', label: 'Workspace', order: 10 },
  requiredCapabilities: ['op:workspace.status'], optionalCapabilities: [], selectionDependencies: [],
  render: Workspace,
};
