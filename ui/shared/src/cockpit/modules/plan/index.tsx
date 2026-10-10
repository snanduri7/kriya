import { PendingView, useRunEvidence, AvailabilityView, type ModuleDefinition } from '../../core';

function PlanPending() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{() => <PendingView title="Plan" needs="recorded plan and obligation snapshots (slice M4)" />}</AvailabilityView>;
}

export const planModule: ModuleDefinition = {
  id: 'plan',
  placement: { page: 'runs', surface: 'center-tab', label: 'Plan', order: 30 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId'],
  whenUnavailable: 'explain',
  render: PlanPending,
};
