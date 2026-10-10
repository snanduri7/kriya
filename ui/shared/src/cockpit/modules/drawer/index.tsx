import { AvailabilityView, PendingView, useModuleSelection, useRunEvidence, type ModuleDefinition } from '../../core';

function RawEvents() {
  const evidence = useRunEvidence();
  const seq = useModuleSelection('eventSeq');
  return (
    <AvailabilityView value={evidence}>
      {(data) => (
        <pre className="mono" aria-label="Raw events" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
          {data.records.filter((r) => seq === null || r.seq === seq).slice(0, 200).map((r) => JSON.stringify({ seq: r.seq, kind: r.kind, unit: r.unit_id, attempt: r.attempt_number, role: r.role, provenance: r.provenance, payload: r.payload })).join('\n')}
        </pre>
      )}
    </AvailabilityView>
  );
}

const pending = (title: string, needs: string) => function Pending() { return <PendingView title={title} needs={needs} />; };

const base = { requiredCapabilities: ['evidence'], optionalCapabilities: [] as string[], selectionDependencies: ['runId'] as const, whenUnavailable: 'explain' as const };

export const drawerBuildModule: ModuleDefinition = { id: 'drawer-build', placement: { page: 'runs', surface: 'drawer-tab', label: 'Build', order: 10 }, ...base, render: pending('Build output', 'gate output blobs (content API, M3)') };
export const drawerTestsModule: ModuleDefinition = { id: 'drawer-tests', placement: { page: 'runs', surface: 'drawer-tab', label: 'Tests', order: 20 }, ...base, render: pending('Test output', 'gate output blobs (content API, M3)') };
export const drawerGitModule: ModuleDefinition = { id: 'drawer-git', placement: { page: 'runs', surface: 'drawer-tab', label: 'Git', order: 30 }, ...base, render: pending('Git', 'a recorded repository and baseline source (not recorded today)') };
export const drawerRawEventsModule: ModuleDefinition = { id: 'drawer-raw-events', placement: { page: 'runs', surface: 'drawer-tab', label: 'Raw Events', order: 40 }, ...base, selectionDependencies: ['runId', 'eventSeq'], render: RawEvents };
